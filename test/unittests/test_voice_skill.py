# Copyright 2024, Mycroft AI Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for OCPVoiceSkill: the "what's playing" / shuffle voice intents,
the dialogs the player asks it to announce, and the liked-songs search.

See https://github.com/OpenVoiceOS/ovos-media/issues/23

OCPVoiceSkill is a real OVOSCommonPlaybackSkill, so it is instantiated
directly (not mocked) with a real FakeBus, exactly the way MediaService
wires it up in production. Because the intent handlers query now-playing
state via the existing 'ovos.common_play.status' request/response bus API
(the same one OCPMediaPlayer.handle_status answers), a lightweight FakeBus
responder stands in for the player here — no bus messages beyond the ones
ovos-media already defines are used anywhere in this file.
"""
import unittest
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message
from ovos_bus_client.session import Session, SessionManager
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import LoopState, MediaEntry, MediaState, MediaType, PlaybackType, PlayerState

from ovos_media.catalog import LikedSongsStore, MediaCatalog, PlayHistoryStore


class _FakeStore(dict):
    """The JsonStorageXDG surface LikedSongsStore writes through, without
    touching disk."""

    def store(self):
        pass


def _likes(entries=None):
    return LikedSongsStore(_FakeStore(entries or {}))


def _history(entries=None):
    return PlayHistoryStore(_FakeStore(entries or {}))
from ovos_media.player import OCPMediaPlayer
from ovos_media.skill import OCPVoiceSkill

SKILL_ID = "ovos.common_play.favorites"


def _make_skill(bus, **kwargs):
    kwargs.setdefault("likes", _likes())
    return OCPVoiceSkill(bus=bus, skill_id=SKILL_ID, **kwargs)


def _make_wired_skill(status: dict, validate_source: bool = True):
    """Return a real OCPVoiceSkill wired to a FakeBus that answers
    'ovos.common_play.status' requests with the given canned status dict.
    """
    bus = FakeBus()
    bus.on("ovos.common_play.status",
          lambda m: bus.emit(m.response(dict(status))))
    skill = _make_skill(bus, validate_source=validate_source)

    spoken = []
    bus.on("speak", lambda m: spoken.append(m.data["utterance"]))
    return skill, bus, spoken


_NOTHING_PLAYING_LINES = {
    "nothing is playing right now",
    "there's nothing playing at the moment",
    "i'm not playing anything right now",
}

_NOT_RESPONDING_LINES = {
    "the media player stopped responding — try restarting playback",
    "media playback service isn't responding — try again in a moment",
    "the player process isn't answering — restart playback to fix it",
}


def _assert_nothing_playing(testcase, utterance):
    testcase.assertIn(utterance.lower(), _NOTHING_PLAYING_LINES)


PLAYING_STATUS = {"title": "Bohemian Rhapsody", "artist": "Queen", "shuffle": False}
NO_ARTIST_STATUS = {"title": "Unknown Track", "artist": "", "shuffle": False}
NOTHING_PLAYING_STATUS = {"title": "", "artist": "", "shuffle": False}


class TestWhatSong(unittest.TestCase):
    def test_speaks_title_and_artist_when_playing(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        skill.handle_what_song(Message("WhatSong"))
        self.assertEqual(len(spoken), 1)
        self.assertIn("Bohemian Rhapsody", spoken[0])
        self.assertIn("Queen", spoken[0])

    def test_speaks_title_only_when_no_artist(self):
        skill, bus, spoken = _make_wired_skill(NO_ARTIST_STATUS)
        skill.handle_what_song(Message("WhatSong"))
        self.assertEqual(len(spoken), 1)
        self.assertIn("Unknown Track", spoken[0])

    def test_speaks_nothing_playing_dialog_when_idle(self):
        skill, bus, spoken = _make_wired_skill(NOTHING_PLAYING_STATUS)
        skill.handle_what_song(Message("WhatSong"))
        self.assertEqual(len(spoken), 1)
        _assert_nothing_playing(self, spoken[0])

    def test_no_status_response_speaks_not_responding_not_nothing_playing(self):
        # No responder registered at all -> wait_for_response times out.
        # A timeout (player unreachable) must NOT be confused with an
        # answered-but-idle status (nothing playing) - see issue review.
        bus = FakeBus()
        skill = _make_skill(bus)
        spoken = []
        bus.on("speak", lambda m: spoken.append(m.data["utterance"]))
        skill.handle_what_song(Message("WhatSong"))
        self.assertEqual(len(spoken), 1)
        self.assertNotIn(spoken[0].lower(), _NOTHING_PLAYING_LINES)
        self.assertIn(spoken[0].lower(), _NOT_RESPONDING_LINES)

    def test_status_request_carries_incoming_session_id(self):
        # Regression guard for _get_status using message.forward(...)
        # instead of a bare Message(...): the outgoing status request must
        # carry the triggering intent message's session, not a fresh/default
        # one. Mutating _get_status back to `Message("ovos.common_play.status")`
        # makes this fail (captured session_id == "default" instead of
        # "sat-status-99").
        bus = FakeBus()
        bus.on("ovos.common_play.status",
              lambda m: bus.emit(m.response(dict(PLAYING_STATUS))))
        skill = _make_skill(bus)

        captured = []
        bus.on("ovos.common_play.status", lambda m: captured.append(m))

        incoming = Message("WhatSong")
        incoming.context["session"] = Session(session_id="sat-status-99").serialize()

        skill.handle_what_song(incoming)

        self.assertEqual(len(captured), 1)
        self.assertEqual(SessionManager.get(captured[0]).session_id, "sat-status-99")


class TestWhatArtist(unittest.TestCase):
    def test_speaks_artist_when_playing(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        skill.handle_what_artist(Message("WhatArtist"))
        self.assertEqual(len(spoken), 1)
        self.assertIn("Queen", spoken[0])

    def test_speaks_no_artist_info_when_artist_missing(self):
        skill, bus, spoken = _make_wired_skill(NO_ARTIST_STATUS)
        skill.handle_what_artist(Message("WhatArtist"))
        self.assertEqual(len(spoken), 1)
        self.assertIn("don't", spoken[0].lower())

    def test_speaks_nothing_playing_dialog_when_idle(self):
        skill, bus, spoken = _make_wired_skill(NOTHING_PLAYING_STATUS)
        skill.handle_what_artist(Message("WhatArtist"))
        self.assertEqual(len(spoken), 1)
        _assert_nothing_playing(self, spoken[0])

    def test_no_status_response_speaks_not_responding(self):
        bus = FakeBus()
        skill = _make_skill(bus)
        spoken = []
        bus.on("speak", lambda m: spoken.append(m.data["utterance"]))
        skill.handle_what_artist(Message("WhatArtist"))
        self.assertEqual(len(spoken), 1)
        self.assertIn(spoken[0].lower(), _NOT_RESPONDING_LINES)


class TestWhatAlbum(unittest.TestCase):
    """NowPlaying/MediaEntry does not track album metadata anywhere in
    ovos-media, so WhatAlbum always gracefully falls back while a track is
    playing (see handle_what_album docstring/comment)."""

    def test_speaks_no_album_info_when_playing(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        skill.handle_what_album(Message("WhatAlbum"))
        self.assertEqual(len(spoken), 1)
        self.assertIn("album", spoken[0].lower())

    def test_speaks_nothing_playing_dialog_when_idle(self):
        skill, bus, spoken = _make_wired_skill(NOTHING_PLAYING_STATUS)
        skill.handle_what_album(Message("WhatAlbum"))
        self.assertEqual(len(spoken), 1)
        _assert_nothing_playing(self, spoken[0])

    def test_no_status_response_speaks_not_responding(self):
        bus = FakeBus()
        skill = _make_skill(bus)
        spoken = []
        bus.on("speak", lambda m: spoken.append(m.data["utterance"]))
        skill.handle_what_album(Message("WhatAlbum"))
        self.assertEqual(len(spoken), 1)
        self.assertIn(spoken[0].lower(), _NOT_RESPONDING_LINES)


class TestShuffleIntents(unittest.TestCase):
    def test_shuffle_on_emits_existing_shuffle_set_message_and_speaks(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.shuffle.set", lambda m: received.append(m))
        skill.handle_shuffle_on(Message("ShuffleOn"))
        self.assertEqual(len(received), 1)
        self.assertEqual(len(spoken), 1)

    def test_shuffle_off_emits_existing_shuffle_unset_message_and_speaks(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.shuffle.unset", lambda m: received.append(m))
        skill.handle_shuffle_off(Message("ShuffleOff"))
        self.assertEqual(len(received), 1)
        self.assertEqual(len(spoken), 1)

    def test_shuffle_on_flips_shuffle_flag_via_existing_handler(self):
        """The 'ovos.common_play.shuffle.set'/'unset' messages the intents
        emit are the same ones OCPMediaPlayer.handle_set_shuffle /
        handle_unset_shuffle already listen for (see ovos_media.bus.api) —
        registering that exact pair here (without constructing a full
        OCPMediaPlayer, which needs a live service stack) demonstrates no
        new bus message types were introduced."""
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        state = {"shuffle": False}
        bus.on("ovos.common_play.shuffle.set", lambda m: state.update(shuffle=True))
        bus.on("ovos.common_play.shuffle.unset", lambda m: state.update(shuffle=False))

        skill.handle_shuffle_on(Message("ShuffleOn"))
        self.assertTrue(state["shuffle"])

        skill.handle_shuffle_off(Message("ShuffleOff"))
        self.assertFalse(state["shuffle"])


def _make_real_player(bus, title="Bohemian Rhapsody", artist="Queen"):
    """Construct a real OCPMediaPlayer (not a canned lambda) wired to the
    given FakeBus, so its real handle_status wires up "ovos.common_play.status"
    responses and the real payload shape (title/artist keys etc) is proven.
    External services are mocked, but handle_status and the bus edge are
    real.
    """
    with patch("ovos_media.player.AudioService"), \
         patch("ovos_media.player.VideoService"), \
         patch("ovos_media.player.WebService"), \
         patch("ovos_media.player.OcpMprisExporter"), \
         patch("ovos_media.player.Configuration", return_value={"media": {}}), \
         patch("ovos_media.player.OCPMediaCatalog"):
        p = OCPMediaPlayer.__new__(OCPMediaPlayer)
        p._init_runtime_state()
        p.ocp_config = {}
        p.validate_source = True
        p.state = PlayerState.STOPPED
        p.loop_state = LoopState.NONE
        p.media_state = MediaState.NO_MEDIA
        p.shuffle = False
        p.track_history = {}
        p._paused_on_duck = False
        p.now_playing = MagicMock()
        p.now_playing.title = title
        p.now_playing.artist = artist
        p.now_playing.image = ""
        p.now_playing.media_type = None
        p.playback_type = PlaybackType.AUDIO
        p.playlist = MagicMock()
        p.playlist.position = 0
        p.media = MagicMock()
        p.audio_service = MagicMock()
        p.video_service = MagicMock()
        p.web_service = MagicMock()
        p.current = None
        p.mpris = None
        p.bus = bus
        bus.on("ovos.common_play.status", p.handle_status)
    return p


def _make_next_track_player(entries, position=0, loop_state=LoopState.NONE,
                            shuffle=False, search_entries=None,
                            playback_type=PlaybackType.AUDIO,
                            now_playing_uri=None):
    """A real OCPMediaPlayer with a real PlayQueue (not a MagicMock), for
    exercising next_track_preview()/_merged_queue() against actual queue
    algebra instead of a synthetic status dict."""
    with patch("ovos_media.player.AudioService"), \
         patch("ovos_media.player.VideoService"), \
         patch("ovos_media.player.WebService"), \
         patch("ovos_media.player.OcpMprisExporter"), \
         patch("ovos_media.player.Configuration", return_value={"media": {}}), \
         patch("ovos_media.player.OCPMediaCatalog"):
        p = OCPMediaPlayer.__new__(OCPMediaPlayer)
        p._init_runtime_state()
        p.ocp_config = {}
        p.loop_state = loop_state
        p.shuffle = shuffle
        for e in entries:
            p._queue.add_entry(e)
        if entries:
            p._queue.set_position(position)
            p._queue.current = p._queue.entries[position]
        p.playlist = p._queue
        p.media = MagicMock()
        p.media.search_playlist.entries = search_entries or []
        p.now_playing = MagicMock()
        # a MagicMock().as_entry() called with no current entry set would
        # return None too, which the idle guard also treats as "nothing" -
        # falling back to an empty MediaEntry here keeps that test honest
        # about REPEAT_TRACK's own None-vs-empty-entry distinction instead
        # of passing for free
        p.now_playing.as_entry.return_value = (
            p._queue.current if p._queue.current is not None
            else MediaEntry(uri="", title=""))
        p.now_playing.playback = playback_type
        # falls back to the current entry's uri when playing, "" when idle -
        # matches NowPlaying tracking the loaded track's own uri
        default_uri = p._queue.current.uri if p._queue.current is not None else ""
        p.now_playing.uri = default_uri if now_playing_uri is None else now_playing_uri
        p.mpris = None
    return p


class TestRealPlayerStatusResponder(unittest.TestCase):
    """Proves the real OCPMediaPlayer.handle_status payload (not a canned
    lambda) carries the title/artist keys the intent handlers read."""

    def test_what_song_reads_real_player_payload(self):
        bus = FakeBus()
        _make_real_player(bus, title="Real Song", artist="Real Artist")
        skill = _make_skill(bus)
        spoken = []
        bus.on("speak", lambda m: spoken.append(m.data["utterance"]))

        skill.handle_what_song(Message("WhatSong"))

        self.assertEqual(len(spoken), 1)
        self.assertIn("Real Song", spoken[0])
        self.assertIn("Real Artist", spoken[0])


class TestFiveIntentsRegistered(unittest.TestCase):
    """False-green guard: if any of the five .intent files is ever
    removed or renamed, this must fail instead of
    passing silently. Asserts the actual padatious registration messages
    OVOSCommonPlaybackSkill.register_intent_file emits, with the expected
    intent file names, rather than relying on the handlers being reachable
    through a canned bus wiring."""

    def test_five_padatious_register_intent_messages_emitted(self):
        bus = FakeBus()
        registrations = []
        bus.on("padatious:register_intent",
              lambda m: registrations.append(m.data))

        _make_skill(bus)

        # ovos-workshop registers under the authoring name ("WhatSong.intent")
        # on older versions and the canonical name ("WhatSong") after the
        # canonical-topic switch — accept either spelling, reject absence.
        names = {r.get("name", "").split(":")[-1].removesuffix(".intent")
                 for r in registrations}
        for expected in ("WhatSong", "WhatAlbum", "WhatArtist",
                         "ShuffleOn", "ShuffleOff"):
            self.assertIn(expected, names,
                          f"expected {expected} to be registered; got {names}")
        self.assertGreaterEqual(len(registrations), 5)


class TestShuffleSessionGate(unittest.TestCase):
    """OCPMediaPlayer.handle_set_shuffle/handle_unset_shuffle are gated by
    session at the bus edge and silently drop the action on a non-default
    (e.g. HiveMind satellite) session. The shuffle intent handlers must not
    claim success (speak "shuffle.on"/"shuffle.off") when that's about to
    happen - they must mirror the gate themselves."""

    def _named_session_message(self, msg_type):
        m = Message(msg_type)
        m.context["session"] = Session(session_id="sat-42").serialize()
        return m

    def test_shuffle_on_does_not_claim_success_on_named_session(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.shuffle.set", lambda m: received.append(m))

        skill.handle_shuffle_on(self._named_session_message("ShuffleOn"))

        self.assertEqual(len(received), 0)
        self.assertEqual(len(spoken), 1)
        self.assertNotIn("shuffle is on", spoken[0].lower())
        for line in ("shuffle is now on", "shuffle enabled", "shuffling now"):
            self.assertNotEqual(spoken[0].lower(), line)

    def test_shuffle_off_does_not_claim_success_on_named_session(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.shuffle.unset", lambda m: received.append(m))

        skill.handle_shuffle_off(self._named_session_message("ShuffleOff"))

        self.assertEqual(len(received), 0)
        self.assertEqual(len(spoken), 1)

    def test_shuffle_on_still_acts_on_default_session(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.shuffle.set", lambda m: received.append(m))

        skill.handle_shuffle_on(Message("ShuffleOn"))

        self.assertEqual(len(received), 1)
        self.assertEqual(len(spoken), 1)

    def test_shuffle_on_acts_on_named_session_when_validate_source_false(self):
        # media.validate_source: false is the documented satellite config
        # (see ovos_media/utils.py / service.py): the player itself will
        # execute the shuffle.set on ANY session in that mode, so this
        # front-end's own gate must agree and not refuse it.
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS,
                                               validate_source=False)
        received = []
        bus.on("ovos.common_play.shuffle.set", lambda m: received.append(m))

        skill.handle_shuffle_on(self._named_session_message("ShuffleOn"))

        self.assertEqual(len(received), 1)
        self.assertEqual(len(spoken), 1)
        self.assertIn("shuffl", spoken[0].lower())
        self.assertNotIn("can't control", spoken[0].lower())


class TestDialogNotifications(unittest.TestCase):
    """The player never speaks: it notifies its catalog, and this skill is
    the listener that turns a notification into real speech. Without the
    skill attached the notification must be dropped silently."""

    def test_notified_dialog_is_spoken(self):
        bus = FakeBus()
        catalog = MediaCatalog(bus, _likes())
        _make_skill(bus, catalog=catalog)
        spoken = []
        bus.on("speak", lambda m: spoken.append(m.data["utterance"]))

        catalog.notify_dialog("track.failed")

        self.assertEqual(len(spoken), 1)
        self.assertTrue(spoken[0])

    def test_notification_without_a_listener_is_silent(self):
        bus = FakeBus()
        catalog = MediaCatalog(bus, _likes())
        spoken = []
        bus.on("speak", lambda m: spoken.append(m.data["utterance"]))

        catalog.notify_dialog("track.failed")

        self.assertEqual(spoken, [])

    def test_shutdown_stops_the_skill_speaking_notifications(self):
        bus = FakeBus()
        catalog = MediaCatalog(bus, _likes())
        skill = _make_skill(bus, catalog=catalog)
        spoken = []
        bus.on("speak", lambda m: spoken.append(m.data["utterance"]))

        skill.default_shutdown()
        catalog.notify_dialog("track.failed")

        self.assertEqual(spoken, [])


class TestConstructsWithoutAhocorasickNer(unittest.TestCase):
    """ahocorasick_ner ("ner" extra) is OPTIONAL, but it is NOT a pure
    matching-speed optimization: OVOSCommonPlaybackSkill.ocp_voc_match hard
    -depends on the local NER matcher it builds, so without it search_db
    ("play my liked songs" / "play my favorites") finds nothing. The five
    WhatSong/WhatAlbum/WhatArtist/ShuffleOn/ShuffleOff voice intents do not
    depend on it and must keep registering normally. The OCP pipeline
    classifier should still learn the keywords via the
    'ovos.common_play.register_keyword' bus message even without local NER
    (see ovos_media.catalog.keywords.KeywordRegistrar).

    Simulates ahocorasick_ner's absence by patching ovos_workshop's already-
    imported AhocorasickNER symbol to None (the same state
    ovos_workshop.skills.common_play ends up in when the real import
    fails), then constructs the skill."""

    def test_skill_constructs_and_registers_intents_without_ner(self):
        with patch("ovos_workshop.skills.common_play.AhocorasickNER", None):
            bus = FakeBus()
            registrations = []
            bus.on("padatious:register_intent",
                  lambda m: registrations.append(m.data))

            skill = _make_skill(bus)

            self.assertIsNotNone(skill)
            # accept both the authoring ("WhatSong.intent") and canonical
            # ("WhatSong") registration spellings across workshop versions
            names = {r.get("name", "").split(":")[-1].removesuffix(".intent")
                     for r in registrations}
            self.assertIn("WhatSong", names)
            self.assertIn("ShuffleOn", names)

    def test_search_db_finds_nothing_without_ner(self):
        """search_db depends on the local NER matcher; without ahocorasick
        it must not crash, but it also must not find liked-songs matches."""
        with patch("ovos_workshop.skills.common_play.AhocorasickNER", None):
            bus = FakeBus()
            skill = _make_skill(bus)
            results = list(skill.search_db("play my liked songs", MediaType.MUSIC))
            self.assertEqual(results, [])

    def test_classifier_still_learns_keywords_without_ner(self):
        """Even without local NER, the OCP pipeline classifier must still
        be informed of the keywords via the bus, so media-type
        disambiguation keeps working."""
        with patch("ovos_workshop.skills.common_play.AhocorasickNER", None):
            bus = FakeBus()
            registered = []
            bus.on("ovos.common_play.register_keyword",
                  lambda m: registered.append(m.data))

            _make_skill(bus)

            labels = {r["label"] for r in registered}
            self.assertIn("playlist_name", labels)

    def test_song_name_label_registers_even_with_no_liked_songs(self):
        """register_ocp_keyword (the real, NER-backed path) always emits
        'ovos.common_play.register_keyword' regardless of sample count - the
        classifier still needs to know the "song_name" label exists even
        when the liked-songs store is empty. The fallback used when
        ahocorasick_ner is missing must mirror that: a fresh install with no
        liked songs must still register the song_name label with an empty
        samples list, not silently skip it."""
        with patch("ovos_workshop.skills.common_play.AhocorasickNER", None):
            bus = FakeBus()
            registered = []
            bus.on("ovos.common_play.register_keyword",
                  lambda m: registered.append(m.data))

            _make_skill(bus, likes=_likes())

            song_name_msgs = [r for r in registered if r["label"] == "song_name"]
            self.assertEqual(len(song_name_msgs), 1)
            self.assertEqual(song_name_msgs[0]["samples"], [])
            self.assertEqual(set(song_name_msgs[0].keys()),
                              {"skill_id", "label", "samples", "media_type"})


class TestHistorySearchDb(unittest.TestCase):
    """search_db's "recently played"/"most played" playlist results, gated
    on a real (non-mocked) NER matcher exactly like the liked-songs case
    (see TestConstructsWithoutAhocorasickNer for the no-NER path)."""

    def test_empty_history_yields_nothing(self):
        bus = FakeBus()
        catalog = MediaCatalog(bus, _likes(), history=_history())
        skill = _make_skill(bus, catalog=catalog)

        results = list(skill.search_db("recently played", MediaType.MUSIC))

        self.assertEqual(results, [])

    def test_populated_history_yields_recently_played(self):
        bus = FakeBus()
        history = _history({
            "http://a.mp3": {"title": "Alpha", "last_played": 1, "play_count": 1},
            "http://b.mp3": {"title": "Beta", "last_played": 2, "play_count": 1},
        })
        catalog = MediaCatalog(bus, _likes(), history=history)
        skill = _make_skill(bus, catalog=catalog)

        results = list(skill.search_db("recently played", MediaType.MUSIC))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Recently Played")
        titles = [e["title"] for e in results[0]["playlist"]]
        self.assertEqual(titles, ["Beta", "Alpha"])

    def test_populated_history_yields_most_played(self):
        bus = FakeBus()
        history = _history({
            "http://a.mp3": {"title": "Alpha", "last_played": 1, "play_count": 5},
            "http://b.mp3": {"title": "Beta", "last_played": 2, "play_count": 1},
        })
        catalog = MediaCatalog(bus, _likes(), history=history)
        skill = _make_skill(bus, catalog=catalog)

        results = list(skill.search_db("most played", MediaType.MUSIC))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Most Played")
        titles = [e["title"] for e in results[0]["playlist"]]
        self.assertEqual(titles, ["Alpha", "Beta"])

    def test_history_disabled_yields_nothing(self):
        bus = FakeBus()
        history = _history({
            "http://a.mp3": {"title": "Alpha", "last_played": 1, "play_count": 1}})
        catalog = MediaCatalog(bus, _likes(), history=history)
        with patch("ovos_media.skill.Configuration") as cfg:
            cfg.return_value.get.return_value = {"history": {"enabled": False}}
            skill = _make_skill(bus, catalog=catalog)

        results = list(skill.search_db("recently played", MediaType.MUSIC))

        self.assertEqual(results, [])

    def test_capitalized_recently_played_phrase_still_matches(self):
        """ocp_voc_match returns the ORIGINAL-cased span from the
        utterance (whisper-family STT capitalizes); the matched-keyword
        membership check must lower() before comparing against the
        lowercase *_KEYWORDS lists."""
        bus = FakeBus()
        history = _history({
            "http://a.mp3": {"title": "Alpha", "last_played": 1, "play_count": 1}})
        catalog = MediaCatalog(bus, _likes(), history=history)
        skill = _make_skill(bus, catalog=catalog)

        with patch.object(skill, "ocp_voc_match",
                          side_effect=lambda phrase: {"playlist_name": "Recently Played"}):
            results = list(skill.search_db("Recently Played", MediaType.MUSIC))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Recently Played")
        self.assertEqual(results[0]["match_confidence"], 100)

    def test_capitalized_most_played_phrase_still_matches(self):
        bus = FakeBus()
        history = _history({
            "http://a.mp3": {"title": "Alpha", "last_played": 1, "play_count": 1}})
        catalog = MediaCatalog(bus, _likes(), history=history)
        skill = _make_skill(bus, catalog=catalog)

        with patch.object(skill, "ocp_voc_match",
                          side_effect=lambda phrase: {"playlist_name": "My Top Songs"}):
            results = list(skill.search_db("play my My Top Songs", MediaType.MUSIC))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Most Played")

    def test_capitalized_liked_songs_phrase_still_matches(self):
        bus = FakeBus()
        likes = _likes({"http://a.mp3": {"title": "Alpha"}})
        catalog = MediaCatalog(bus, likes)
        skill = _make_skill(bus, likes=likes, catalog=catalog)

        with patch.object(skill, "ocp_voc_match",
                          side_effect=lambda phrase: {"playlist_name": "Liked songs"}):
            results = list(skill.search_db("Liked songs", MediaType.MUSIC))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Liked Songs")
        self.assertEqual(results[0]["match_confidence"], 100)

    def test_bad_media_type_row_does_not_block_song_name_results(self):
        """search_db is a generator: the history block runs before the
        song_name branch, so an uncaught ValueError from a malformed
        media_type/playback in a history row would kill the whole
        generator on list() - not just the history playlists, but the
        unrelated liked-songs title results too. A single utterance that
        matches both a history playlist keyword and a liked song title
        exercises that ordering directly."""
        bus = FakeBus()
        likes = _likes({"http://liked.mp3": {"title": "Alpha", "play_count": 1}})
        history = _history({
            "http://bad.mp3": {"title": "Bad Row", "play_count": 1,
                               "media_type": 999, "playback": "music"}})
        catalog = MediaCatalog(bus, likes, history=history)
        skill = _make_skill(bus, likes=likes, catalog=catalog)

        with patch.object(skill, "ocp_voc_match", side_effect=lambda phrase: {
                "playlist_name": "recently played", "song_name": "alpha"}):
            results = list(skill.search_db("recently played alpha", MediaType.MUSIC))

        titles = [r["title"] for r in results]
        self.assertIn("Recently Played", titles)
        # the liked-songs song_name branch (a plain result dict, not a
        # playlist) must have run too - proof the generator was not
        # aborted by the bad row above it
        self.assertTrue(any(r.get("title") == "Alpha" for r in results))

    def test_history_keywords_registered_once(self):
        bus = FakeBus()
        registered = []
        bus.on("ovos.common_play.register_keyword",
              lambda m: registered.append(m.data))

        _make_skill(bus)

        playlist_msgs = [r for r in registered if r["label"] == "playlist_name"]
        # one for liked-songs synonyms, one for recently-played, one for
        # most-played
        self.assertEqual(len(playlist_msgs), 3)


class TestNewIntentsRegistered(unittest.TestCase):
    """Companion to TestFiveIntentsRegistered above, for the like/unlike,
    repeat, seek, shuffle-query and what's-next intents added alongside
    it."""

    def test_all_new_intents_registered(self):
        bus = FakeBus()
        registrations = []
        bus.on("padatious:register_intent",
              lambda m: registrations.append(m.data))

        _make_skill(bus)

        names = {r.get("name", "").split(":")[-1].removesuffix(".intent")
                 for r in registrations}
        for expected in ("Like", "Unlike", "RepeatOn", "RepeatOff", "RepeatTrack",
                         "SeekForward", "SeekBackward", "WhatShuffle",
                         "WhatNext"):
            self.assertIn(expected, names,
                          f"expected {expected} to be registered; got {names}")


class TestLikeUnlikeIntents(unittest.TestCase):
    def test_like_emits_like_message(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.like", lambda m: received.append(m))
        skill.handle_like(Message("Like"))
        self.assertEqual(len(received), 1)

    def test_unlike_emits_unlike_message(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.unlike", lambda m: received.append(m))
        skill.handle_unlike(Message("Unlike"))
        self.assertEqual(len(received), 1)

    def test_like_does_not_act_on_named_session(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.like", lambda m: received.append(m))
        m = Message("Like")
        m.context["session"] = Session(session_id="sat-42").serialize()
        skill.handle_like(m)
        self.assertEqual(len(received), 0)
        self.assertEqual(len(spoken), 1)

    def test_unlike_does_not_act_on_named_session(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.unlike", lambda m: received.append(m))
        m = Message("Unlike")
        m.context["session"] = Session(session_id="sat-42").serialize()
        skill.handle_unlike(m)
        self.assertEqual(len(received), 0)
        self.assertEqual(len(spoken), 1)


class TestRepeatIntents(unittest.TestCase):
    def test_repeat_on_emits_repeat_set_and_speaks(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.repeat.set", lambda m: received.append(m))
        skill.handle_repeat_on(Message("RepeatOn"))
        self.assertEqual(len(received), 1)
        self.assertEqual(len(spoken), 1)

    def test_repeat_off_emits_repeat_unset_and_speaks(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.repeat.unset", lambda m: received.append(m))
        skill.handle_repeat_off(Message("RepeatOff"))
        self.assertEqual(len(received), 1)
        self.assertEqual(len(spoken), 1)

    def test_repeat_on_does_not_claim_success_on_named_session(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.repeat.set", lambda m: received.append(m))
        m = Message("RepeatOn")
        m.context["session"] = Session(session_id="sat-42").serialize()
        skill.handle_repeat_on(m)
        self.assertEqual(len(received), 0)
        self.assertEqual(len(spoken), 1)

    def test_repeat_track_emits_repeat_set_with_track_mode_and_speaks(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.repeat.set", lambda m: received.append(m.data))
        skill.handle_repeat_track(Message("RepeatTrack"))
        self.assertEqual(received, [{"mode": "track"}])
        self.assertEqual(len(spoken), 1)

    def test_repeat_track_does_not_claim_success_on_named_session(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.repeat.set", lambda m: received.append(m))
        m = Message("RepeatTrack")
        m.context["session"] = Session(session_id="sat-42").serialize()
        skill.handle_repeat_track(m)
        self.assertEqual(len(received), 0)
        self.assertEqual(len(spoken), 1)


class TestSeekIntents(unittest.TestCase):
    def _utterance(self, msg_type, text):
        m = Message(msg_type, {"utterance": text})
        return m

    def test_seek_forward_with_explicit_seconds(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.seek", lambda m: received.append(m.data))
        skill.handle_seek_forward(self._utterance("SeekForward", "skip forward 30 seconds"))
        self.assertEqual(received, [{"seconds": 30}])

    def test_seek_forward_with_no_duration_defaults_to_ten_seconds(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.seek", lambda m: received.append(m.data))
        skill.handle_seek_forward(self._utterance("SeekForward", "skip forward"))
        self.assertEqual(received, [{"seconds": 10}])

    def test_seek_backward_emits_negative_seconds(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.seek", lambda m: received.append(m.data))
        skill.handle_seek_backward(self._utterance("SeekBackward", "skip back 20 seconds"))
        self.assertEqual(received, [{"seconds": -20}])

    def test_seek_backward_with_no_duration_defaults_to_negative_ten(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.seek", lambda m: received.append(m.data))
        skill.handle_seek_backward(self._utterance("SeekBackward", "rewind"))
        self.assertEqual(received, [{"seconds": -10}])

    def test_seek_forward_minutes_converted_to_seconds(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.seek", lambda m: received.append(m.data))
        skill.handle_seek_forward(self._utterance("SeekForward", "jump forward two minutes"))
        self.assertEqual(received, [{"seconds": 120}])

    def test_seek_forward_does_not_act_on_named_session(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.seek", lambda m: received.append(m))
        m = self._utterance("SeekForward", "skip forward 30 seconds")
        m.context["session"] = Session(session_id="sat-42").serialize()
        skill.handle_seek_forward(m)
        self.assertEqual(len(received), 0)

    def test_a_minute_is_understood_as_one_minute(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.seek", lambda m: received.append(m.data))
        skill.handle_seek_forward(self._utterance("SeekForward", "jump ahead a minute"))
        self.assertEqual(received, [{"seconds": 60}])

    def test_a_second_is_understood_as_one_second(self):
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.seek", lambda m: received.append(m.data))
        skill.handle_seek_backward(self._utterance("SeekBackward", "rewind a second"))
        self.assertEqual(received, [{"seconds": -1}])

    def test_a_bit_is_not_confused_with_a_minute_or_second(self):
        # "a bit" carries no amount at all - it must fall back to the plain
        # 10 second default, not the "a <unit>" -> 1 second idiom, or every
        # sample containing "a bit"/"a bunch"/etc would seek an inaudible
        # amount instead of a normal nudge.
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.seek", lambda m: received.append(m.data))
        skill.handle_seek_forward(self._utterance("SeekForward", "fast forward a bit"))
        self.assertEqual(received, [{"seconds": 10}])

    def test_composite_duration_keeps_the_first_number_and_larger_unit(self):
        # only a single number+unit is supported; "2 minutes 30 seconds"
        # keeps the leading "2" and resolves to minutes (the larger unit
        # mentioned), the same as a bare "2 minutes" would - not a NaN/
        # truncated/garbage result.
        skill, bus, spoken = _make_wired_skill(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.seek", lambda m: received.append(m.data))
        skill.handle_seek_forward(
            self._utterance("SeekForward", "skip forward 2 minutes 30 seconds"))
        self.assertEqual(received, [{"seconds": 120}])


class TestWhatShuffle(unittest.TestCase):
    def test_speaks_shuffle_on(self):
        status = dict(PLAYING_STATUS, shuffle=True)
        skill, bus, spoken = _make_wired_skill(status)
        skill.handle_what_shuffle(Message("WhatShuffle"))
        self.assertEqual(len(spoken), 1)
        self.assertTrue(any(w in spoken[0].lower() for w in ("on", "enabled")))

    def test_speaks_shuffle_off(self):
        status = dict(PLAYING_STATUS, shuffle=False)
        skill, bus, spoken = _make_wired_skill(status)
        skill.handle_what_shuffle(Message("WhatShuffle"))
        self.assertEqual(len(spoken), 1)
        self.assertTrue(any(w in spoken[0].lower() for w in ("off", "disabled")))


PLAYING_NEXT_BASE = dict(PLAYING_STATUS, player_state=PlayerState.PLAYING)


class TestWhatNext(unittest.TestCase):
    """handle_what_next reads the bounded 'next_track'/'next_track_hint'
    pair off the status response - never a full queue - see
    OCPMediaPlayer.next_track_preview for what actually computes them."""

    def test_speaks_next_track_with_artist(self):
        status = dict(PLAYING_NEXT_BASE,
                      next_track={"title": "Radio Ga Ga", "artist": "Queen"},
                      next_track_hint=None)
        skill, bus, spoken = _make_wired_skill(status)
        skill.handle_what_next(Message("WhatNext"))
        self.assertEqual(len(spoken), 1)
        self.assertIn("Radio Ga Ga", spoken[0])
        self.assertIn("Queen", spoken[0])

    def test_speaks_next_track_without_artist(self):
        status = dict(PLAYING_NEXT_BASE,
                      next_track={"title": "Mystery Track", "artist": ""},
                      next_track_hint=None)
        skill, bus, spoken = _make_wired_skill(status)
        skill.handle_what_next(Message("WhatNext"))
        self.assertEqual(len(spoken), 1)
        self.assertIn("Mystery Track", spoken[0])

    def test_speaks_nothing_queued_when_at_end_of_queue(self):
        status = dict(PLAYING_NEXT_BASE, next_track=None, next_track_hint=None)
        skill, bus, spoken = _make_wired_skill(status)
        skill.handle_what_next(Message("WhatNext"))
        self.assertEqual(len(spoken), 1)
        self.assertTrue(
            any(p in spoken[0].lower() for p in ("nothing", "last track")))

    def test_speaks_shuffle_hint_instead_of_a_track(self):
        status = dict(PLAYING_NEXT_BASE, next_track=None, next_track_hint="shuffle")
        skill, bus, spoken = _make_wired_skill(status)
        skill.handle_what_next(Message("WhatNext"))
        self.assertEqual(len(spoken), 1)
        self.assertTrue(any(w in spoken[0].lower() for w in ("surprise", "shuffle")))

    def test_speaks_external_hint_for_mpris_or_skill_playback(self):
        status = dict(PLAYING_NEXT_BASE, next_track=None, next_track_hint="external")
        skill, bus, spoken = _make_wired_skill(status)
        skill.handle_what_next(Message("WhatNext"))
        self.assertEqual(len(spoken), 1)
        self.assertTrue(any(w in spoken[0].lower() for w in ("connected player",)))

    def test_speaks_nothing_playing_when_stopped(self):
        # the idle guard must fire before next_track/next_track_hint are
        # even looked at, mirroring WhatSong/WhatAlbum/WhatArtist
        status = dict(PLAYING_STATUS, player_state=PlayerState.STOPPED,
                      next_track={"title": "Should Not Be Spoken", "artist": "X"},
                      next_track_hint=None)
        skill, bus, spoken = _make_wired_skill(status)
        skill.handle_what_next(Message("WhatNext"))
        self.assertEqual(len(spoken), 1)
        _assert_nothing_playing(self, spoken[0])


def _entry(title, uri=None):
    return MediaEntry(uri=uri or f"http://x/{title}", title=title, artist="")


class TestNextTrackPreview(unittest.TestCase):
    """OCPMediaPlayer.next_track_preview, driven against a real PlayQueue -
    not a synthetic status dict - to prove it actually mirrors play_next()'s
    own selection logic over _merged_queue()."""

    def test_sequential_mid_queue(self):
        a, b, c = _entry("A"), _entry("B"), _entry("C")
        p = _make_next_track_player([a, b, c], position=0)
        entry, hint = p.next_track_preview
        self.assertIs(entry, b)
        self.assertIsNone(hint)

    def test_repeat_track_replays_the_current_entry(self):
        a, b = _entry("A"), _entry("B")
        p = _make_next_track_player([a, b], position=0,
                                    loop_state=LoopState.REPEAT_TRACK)
        entry, hint = p.next_track_preview
        self.assertIs(entry, a)
        self.assertIsNone(hint)

    def test_repeat_set_mode_track_then_preview_replays_current_entry(self):
        # exercises the actual wire path: repeat.set {"mode": "track"} ->
        # handle_set_repeat -> LoopState.REPEAT_TRACK -> next_track_preview
        a, b = _entry("A"), _entry("B")
        p = _make_next_track_player([a, b], position=0)
        p.handle_set_repeat(Message("ovos.common_play.repeat.set", {"mode": "track"}))
        entry, hint = p.next_track_preview
        self.assertIs(entry, a)
        self.assertIsNone(hint)

    def test_repeat_at_end_wraps_to_the_first_entry(self):
        a, b = _entry("A"), _entry("B")
        p = _make_next_track_player([a, b], position=1,
                                    loop_state=LoopState.REPEAT)
        entry, hint = p.next_track_preview
        self.assertIs(entry, a)
        self.assertIsNone(hint)

    def test_shuffle_reports_a_hint_not_a_prediction(self):
        a, b, c = _entry("A"), _entry("B"), _entry("C")
        p = _make_next_track_player([a, b, c], position=0, shuffle=True)
        entry, hint = p.next_track_preview
        self.assertIsNone(entry)
        self.assertEqual(hint, "shuffle")

    def test_last_user_entry_falls_through_to_first_search_result(self):
        a = _entry("A")
        s1, s2 = _entry("S1"), _entry("S2")
        p = _make_next_track_player([a], position=0, search_entries=[s1, s2])
        entry, hint = p.next_track_preview
        self.assertIs(entry, s1)
        self.assertIsNone(hint)

    def test_end_of_queue_with_no_repeat_reports_nothing(self):
        a = _entry("A")
        p = _make_next_track_player([a], position=0)
        entry, hint = p.next_track_preview
        self.assertIsNone(entry)
        self.assertIsNone(hint)

    def test_idle_repeat_track_reports_nothing_not_an_empty_entry(self):
        # nothing has ever loaded: REPEAT_TRACK must not "repeat" an empty
        # NowPlaying entry (see B3/M2 review round)
        p = _make_next_track_player([], loop_state=LoopState.REPEAT_TRACK,
                                    now_playing_uri="")
        entry, hint = p.next_track_preview
        self.assertIsNone(entry)
        self.assertIsNone(hint)

    def test_mpris_playback_reports_external_hint(self):
        a, b = _entry("A"), _entry("B")
        p = _make_next_track_player([a, b], position=0,
                                    playback_type=PlaybackType.MPRIS)
        entry, hint = p.next_track_preview
        self.assertIsNone(entry)
        self.assertEqual(hint, "external")

    def test_skill_playback_reports_external_hint(self):
        a, b = _entry("A"), _entry("B")
        p = _make_next_track_player([a, b], position=0,
                                    playback_type=PlaybackType.SKILL)
        entry, hint = p.next_track_preview
        self.assertIsNone(entry)
        self.assertEqual(hint, "external")


class TestNextTrackPreviewIsSilent(unittest.TestCase):
    """select_next() runs from next_track_preview on every status query now
    (see M3 review round) - it must never log, or a chatty voice session
    would spam "Next track: ..." on nearly every utterance. play_next()
    itself, the one place a selection is actually acted on, keeps the log
    line at its own call site."""

    def test_a_hundred_previews_log_nothing(self):
        a, b, c = _entry("A"), _entry("B"), _entry("C")
        p = _make_next_track_player([a, b, c], position=0)
        with patch("ovos_media.player.queue.LOG") as mock_log:
            for _ in range(100):
                p.next_track_preview
        mock_log.info.assert_not_called()

    def test_play_next_logs_next_track_exactly_once(self):
        a, b = _entry("A"), _entry("B")
        p = _make_next_track_player([a, b], position=0)
        # isolate the log call site from set_now_playing()'s/play()'s own
        # side effects (MPRIS props, handle_status, backend startup) - none
        # of that is what this test is about
        p.set_now_playing = MagicMock()
        p.play = MagicMock()
        with patch("ovos_media.player.LOG") as mock_log:
            p.play_next()
        self.assertEqual(mock_log.info.call_count, 1)
        self.assertIn("Next track", mock_log.info.call_args[0][0])


if __name__ == "__main__":
    unittest.main()

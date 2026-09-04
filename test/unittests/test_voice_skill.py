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
from ovos_utils.ocp import LoopState, MediaState, MediaType, PlaybackType, PlayerState

from ovos_media.catalog import LikedSongsStore, MediaCatalog


class _FakeStore(dict):
    """The JsonStorageXDG surface LikedSongsStore writes through, without
    touching disk."""

    def store(self):
        pass


def _likes(entries=None):
    return LikedSongsStore(_FakeStore(entries or {}))
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


if __name__ == "__main__":
    unittest.main()

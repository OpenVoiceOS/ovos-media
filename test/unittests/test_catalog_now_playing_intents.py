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
"""Tests for the "what's playing" / shuffle voice intents on OCPMediaCatalog.

See https://github.com/OpenVoiceOS/ovos-media/issues/23

OCPMediaCatalog is a real OVOSCommonPlaybackSkill (ovos_media/player.py), so
it is instantiated directly (not mocked) with a real FakeBus, exactly the way
OCPMediaPlayer wires it up in production. Because the intent handlers query
now-playing state via the existing 'ovos.common_play.status' request/response
bus API (the same one OCPMediaPlayer.handle_status answers), a lightweight
FakeBus responder stands in for the player here — no bus messages beyond the
ones ovos-media already defines are used anywhere in this file.
"""
import unittest
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message
from ovos_bus_client.session import Session, SessionManager
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import LoopState, MediaState, MediaType, PlaybackType, PlayerState

from ovos_media.player import OCPMediaCatalog, OCPMediaPlayer


def _make_catalog(status: dict, validate_source: bool = True):
    """Return a real OCPMediaCatalog wired to a FakeBus that answers
    'ovos.common_play.status' requests with the given canned status dict.
    """
    bus = FakeBus()
    bus.on("ovos.common_play.status",
          lambda m: bus.emit(m.response(dict(status))))
    catalog = OCPMediaCatalog(bus=bus, skill_id="ovos.common_play.favorites",
                              validate_source=validate_source)

    spoken = []
    bus.on("speak", lambda m: spoken.append(m.data["utterance"]))
    return catalog, bus, spoken


_NOTHING_PLAYING_LINES = {
    "nothing is playing right now",
    "there's nothing playing at the moment",
    "i'm not playing anything right now",
}

_NOT_RESPONDING_LINES = {
    "the media player is not responding",
    "i can't reach the media player right now",
    "the media player didn't answer in time",
}


def _assert_nothing_playing(testcase, utterance):
    testcase.assertIn(utterance.lower(), _NOTHING_PLAYING_LINES)


PLAYING_STATUS = {"title": "Bohemian Rhapsody", "artist": "Queen", "shuffle": False}
NO_ARTIST_STATUS = {"title": "Unknown Track", "artist": "", "shuffle": False}
NOTHING_PLAYING_STATUS = {"title": "", "artist": "", "shuffle": False}


class TestWhatSong(unittest.TestCase):
    def test_speaks_title_and_artist_when_playing(self):
        catalog, bus, spoken = _make_catalog(PLAYING_STATUS)
        catalog.handle_what_song(Message("WhatSong"))
        self.assertEqual(len(spoken), 1)
        self.assertIn("Bohemian Rhapsody", spoken[0])
        self.assertIn("Queen", spoken[0])

    def test_speaks_title_only_when_no_artist(self):
        catalog, bus, spoken = _make_catalog(NO_ARTIST_STATUS)
        catalog.handle_what_song(Message("WhatSong"))
        self.assertEqual(len(spoken), 1)
        self.assertIn("Unknown Track", spoken[0])

    def test_speaks_nothing_playing_dialog_when_idle(self):
        catalog, bus, spoken = _make_catalog(NOTHING_PLAYING_STATUS)
        catalog.handle_what_song(Message("WhatSong"))
        self.assertEqual(len(spoken), 1)
        _assert_nothing_playing(self, spoken[0])

    def test_no_status_response_speaks_not_responding_not_nothing_playing(self):
        # No responder registered at all -> wait_for_response times out.
        # A timeout (player unreachable) must NOT be confused with an
        # answered-but-idle status (nothing playing) - see issue review.
        bus = FakeBus()
        catalog = OCPMediaCatalog(bus=bus, skill_id="ovos.common_play.favorites")
        spoken = []
        bus.on("speak", lambda m: spoken.append(m.data["utterance"]))
        catalog.handle_what_song(Message("WhatSong"))
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
        catalog = OCPMediaCatalog(bus=bus, skill_id="ovos.common_play.favorites")

        captured = []
        bus.on("ovos.common_play.status", lambda m: captured.append(m))

        incoming = Message("WhatSong")
        incoming.context["session"] = Session(session_id="sat-status-99").serialize()

        catalog.handle_what_song(incoming)

        self.assertEqual(len(captured), 1)
        self.assertEqual(SessionManager.get(captured[0]).session_id, "sat-status-99")


class TestWhatArtist(unittest.TestCase):
    def test_speaks_artist_when_playing(self):
        catalog, bus, spoken = _make_catalog(PLAYING_STATUS)
        catalog.handle_what_artist(Message("WhatArtist"))
        self.assertEqual(len(spoken), 1)
        self.assertIn("Queen", spoken[0])

    def test_speaks_no_artist_info_when_artist_missing(self):
        catalog, bus, spoken = _make_catalog(NO_ARTIST_STATUS)
        catalog.handle_what_artist(Message("WhatArtist"))
        self.assertEqual(len(spoken), 1)
        self.assertIn("don't", spoken[0].lower())

    def test_speaks_nothing_playing_dialog_when_idle(self):
        catalog, bus, spoken = _make_catalog(NOTHING_PLAYING_STATUS)
        catalog.handle_what_artist(Message("WhatArtist"))
        self.assertEqual(len(spoken), 1)
        _assert_nothing_playing(self, spoken[0])

    def test_no_status_response_speaks_not_responding(self):
        bus = FakeBus()
        catalog = OCPMediaCatalog(bus=bus, skill_id="ovos.common_play.favorites")
        spoken = []
        bus.on("speak", lambda m: spoken.append(m.data["utterance"]))
        catalog.handle_what_artist(Message("WhatArtist"))
        self.assertEqual(len(spoken), 1)
        self.assertIn(spoken[0].lower(), _NOT_RESPONDING_LINES)


class TestWhatAlbum(unittest.TestCase):
    """NowPlaying/MediaEntry does not track album metadata anywhere in
    ovos-media, so WhatAlbum always gracefully falls back while a track is
    playing (see handle_what_album docstring/comment)."""

    def test_speaks_no_album_info_when_playing(self):
        catalog, bus, spoken = _make_catalog(PLAYING_STATUS)
        catalog.handle_what_album(Message("WhatAlbum"))
        self.assertEqual(len(spoken), 1)
        self.assertIn("album", spoken[0].lower())

    def test_speaks_nothing_playing_dialog_when_idle(self):
        catalog, bus, spoken = _make_catalog(NOTHING_PLAYING_STATUS)
        catalog.handle_what_album(Message("WhatAlbum"))
        self.assertEqual(len(spoken), 1)
        _assert_nothing_playing(self, spoken[0])

    def test_no_status_response_speaks_not_responding(self):
        bus = FakeBus()
        catalog = OCPMediaCatalog(bus=bus, skill_id="ovos.common_play.favorites")
        spoken = []
        bus.on("speak", lambda m: spoken.append(m.data["utterance"]))
        catalog.handle_what_album(Message("WhatAlbum"))
        self.assertEqual(len(spoken), 1)
        self.assertIn(spoken[0].lower(), _NOT_RESPONDING_LINES)


class TestShuffleIntents(unittest.TestCase):
    def test_shuffle_on_emits_existing_shuffle_set_message_and_speaks(self):
        catalog, bus, spoken = _make_catalog(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.shuffle.set", lambda m: received.append(m))
        catalog.handle_shuffle_on(Message("ShuffleOn"))
        self.assertEqual(len(received), 1)
        self.assertEqual(len(spoken), 1)

    def test_shuffle_off_emits_existing_shuffle_unset_message_and_speaks(self):
        catalog, bus, spoken = _make_catalog(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.shuffle.unset", lambda m: received.append(m))
        catalog.handle_shuffle_off(Message("ShuffleOff"))
        self.assertEqual(len(received), 1)
        self.assertEqual(len(spoken), 1)

    def test_shuffle_on_flips_shuffle_flag_via_existing_handler(self):
        """The 'ovos.common_play.shuffle.set'/'unset' messages the intents
        emit are the same ones OCPMediaPlayer.handle_set_shuffle /
        handle_unset_shuffle already listen for (register_bus_handlers) —
        registering that exact pair here (without constructing a full
        OCPMediaPlayer, which needs a live service stack) demonstrates no
        new bus message types were introduced."""
        catalog, bus, spoken = _make_catalog(PLAYING_STATUS)
        state = {"shuffle": False}
        bus.on("ovos.common_play.shuffle.set", lambda m: state.update(shuffle=True))
        bus.on("ovos.common_play.shuffle.unset", lambda m: state.update(shuffle=False))

        catalog.handle_shuffle_on(Message("ShuffleOn"))
        self.assertTrue(state["shuffle"])

        catalog.handle_shuffle_off(Message("ShuffleOff"))
        self.assertFalse(state["shuffle"])


def _make_real_player(bus, title="Bohemian Rhapsody", artist="Queen"):
    """Construct a real OCPMediaPlayer (not a canned lambda) wired to the
    given FakeBus, so its real handle_status wires up "ovos.common_play.status"
    responses and the real payload shape (title/artist keys etc) is proven,
    per the backcompat audit's F5 finding. Mirrors the construction pattern
    used in test_player_handlers.py._make_player - external services are
    mocked, but handle_status/register_bus_handlers are real.
    """
    with patch("ovos_media.player.AudioService"), \
         patch("ovos_media.player.VideoService"), \
         patch("ovos_media.player.WebService"), \
         patch("ovos_media.player.OcpMprisExporter"), \
         patch("ovos_media.player.GUIInterface"), \
         patch("ovos_media.player.Configuration", return_value={"media": {}}), \
         patch("ovos_media.player.OCPMediaCatalog"):
        p = OCPMediaPlayer.__new__(OCPMediaPlayer)
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
        p.gui = MagicMock()
        bus.on("ovos.common_play.status", p.handle_status)
    return p


class TestRealPlayerStatusResponder(unittest.TestCase):
    """Proves the real OCPMediaPlayer.handle_status payload (not a canned
    lambda) carries the title/artist keys the catalog handlers read - see
    backcompat audit F5."""

    def test_what_song_reads_real_player_payload(self):
        bus = FakeBus()
        _make_real_player(bus, title="Real Song", artist="Real Artist")
        catalog = OCPMediaCatalog(bus=bus, skill_id="ovos.common_play.favorites")
        spoken = []
        bus.on("speak", lambda m: spoken.append(m.data["utterance"]))

        catalog.handle_what_song(Message("WhatSong"))

        self.assertEqual(len(spoken), 1)
        self.assertIn("Real Song", spoken[0])
        self.assertIn("Real Artist", spoken[0])


class TestFiveIntentsRegistered(unittest.TestCase):
    """False-green guard (backcompat audit finding #2): if any of the five
    new .intent files were ever removed/renamed, this must fail instead of
    passing silently. Asserts the actual padatious registration messages
    OVOSCommonPlaybackSkill.register_intent_file emits, with the expected
    intent file names, rather than relying on the handlers being reachable
    through a canned bus wiring."""

    def test_five_padatious_register_intent_messages_emitted(self):
        bus = FakeBus()
        registrations = []
        bus.on("padatious:register_intent",
              lambda m: registrations.append(m.data))

        OCPMediaCatalog(bus=bus, skill_id="ovos.common_play.favorites")

        names = {r.get("name", "").split(":")[-1] for r in registrations}
        for expected in ("WhatSong.intent", "WhatAlbum.intent",
                         "WhatArtist.intent", "ShuffleOn.intent",
                         "ShuffleOff.intent"):
            self.assertIn(expected, names,
                          f"expected {expected} to be registered; got {names}")
        self.assertGreaterEqual(len(registrations), 5)


class TestShuffleSessionGate(unittest.TestCase):
    """Backcompat audit F2: OCPMediaPlayer.handle_set_shuffle/handle_unset_shuffle
    are gated by @require_default_session() and silently drop the action on a
    non-default (e.g. HiveMind satellite) session. The catalog's shuffle
    intent handlers must not claim success (speak "shuffle.on"/"shuffle.off")
    when that's about to happen - they must mirror the gate themselves."""

    def _named_session_message(self, msg_type):
        m = Message(msg_type)
        m.context["session"] = Session(session_id="sat-42").serialize()
        return m

    def test_shuffle_on_does_not_claim_success_on_named_session(self):
        catalog, bus, spoken = _make_catalog(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.shuffle.set", lambda m: received.append(m))

        catalog.handle_shuffle_on(self._named_session_message("ShuffleOn"))

        self.assertEqual(len(received), 0)
        self.assertEqual(len(spoken), 1)
        self.assertNotIn("shuffle is on", spoken[0].lower())
        for line in ("shuffle is now on", "shuffle enabled", "shuffling now"):
            self.assertNotEqual(spoken[0].lower(), line)

    def test_shuffle_off_does_not_claim_success_on_named_session(self):
        catalog, bus, spoken = _make_catalog(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.shuffle.unset", lambda m: received.append(m))

        catalog.handle_shuffle_off(self._named_session_message("ShuffleOff"))

        self.assertEqual(len(received), 0)
        self.assertEqual(len(spoken), 1)

    def test_shuffle_on_still_acts_on_default_session(self):
        catalog, bus, spoken = _make_catalog(PLAYING_STATUS)
        received = []
        bus.on("ovos.common_play.shuffle.set", lambda m: received.append(m))

        catalog.handle_shuffle_on(Message("ShuffleOn"))

        self.assertEqual(len(received), 1)
        self.assertEqual(len(spoken), 1)

    def test_shuffle_on_acts_on_named_session_when_validate_source_false(self):
        # media.validate_source: false is the documented satellite config
        # (see ovos_media/utils.py:50-52 / service.py:55-60): the player
        # itself will execute the shuffle.set on ANY session in that mode,
        # so the catalog's own gate must agree and not refuse it.
        catalog, bus, spoken = _make_catalog(PLAYING_STATUS, validate_source=False)
        received = []
        bus.on("ovos.common_play.shuffle.set", lambda m: received.append(m))

        catalog.handle_shuffle_on(self._named_session_message("ShuffleOn"))

        self.assertEqual(len(received), 1)
        self.assertEqual(len(spoken), 1)
        self.assertIn("shuffl", spoken[0].lower())
        self.assertNotIn("can't control", spoken[0].lower())


class TestConstructsWithoutAhocorasickNer(unittest.TestCase):
    """ahocorasick_ner ("ner" extra) is OPTIONAL, but it is NOT a pure
    matching-speed optimization: OVOSCommonPlaybackSkill.ocp_voc_match hard
    -depends on the local NER matcher it builds, so without it search_db
    ("play my liked songs" / "play my favorites") finds nothing. The five
    WhatSong/WhatAlbum/WhatArtist/ShuffleOn/ShuffleOff voice intents do not
    depend on it and must keep registering normally. The OCP pipeline
    classifier should still learn the keywords via the
    'ovos.common_play.register_keyword' bus message even without local NER
    (see OCPMediaCatalog._emit_ocp_keyword_registration).

    Simulates ahocorasick_ner's absence by patching ovos_workshop's already-
    imported AhocorasickNER symbol to None (the same state
    ovos_workshop.skills.common_play ends up in when the real import
    fails), then constructs OCPMediaCatalog."""

    def test_catalog_constructs_and_registers_intents_without_ner(self):
        with patch("ovos_workshop.skills.common_play.AhocorasickNER", None):
            bus = FakeBus()
            registrations = []
            bus.on("padatious:register_intent",
                  lambda m: registrations.append(m.data))

            catalog = OCPMediaCatalog(bus=bus, skill_id="ovos.common_play.favorites")

            self.assertIsNotNone(catalog)
            names = {r.get("name", "").split(":")[-1] for r in registrations}
            self.assertIn("WhatSong.intent", names)
            self.assertIn("ShuffleOn.intent", names)

    def test_search_db_finds_nothing_without_ner(self):
        """search_db depends on the local NER matcher; without ahocorasick
        it must not crash, but it also must not find liked-songs matches."""
        with patch("ovos_workshop.skills.common_play.AhocorasickNER", None):
            bus = FakeBus()
            catalog = OCPMediaCatalog(bus=bus, skill_id="ovos.common_play.favorites")
            results = list(catalog.search_db("play my liked songs", MediaType.MUSIC))
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

            OCPMediaCatalog(bus=bus, skill_id="ovos.common_play.favorites")

            labels = {r["label"] for r in registered}
            self.assertIn("playlist_name", labels)


if __name__ == "__main__":
    unittest.main()

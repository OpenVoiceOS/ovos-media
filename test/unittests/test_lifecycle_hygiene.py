"""What a MediaService owes its own lifecycle.

shutdown() unbinds every listener it bound, so a shut-down service stops
answering ping/status. Liking with nothing playing persists nothing rather
than an empty-string store entry. opm.audio.query is never answerable
before self.ocp exists. Player and service both survive construction on a
plain FakeBus.
"""
import unittest
from unittest.mock import MagicMock, patch

from ovos_utils.fakebus import FakeBus
from ovos_bus_client.message import Message

from ovos_media.catalog import LikedSongsStore


def _listener_count(bus):
    return sum(len(bus.ee.listeners(e)) for e in bus.ee.event_names())


def _make_service(bus):
    from ovos_media.service import MediaService
    with patch("ovos_media.player.AudioService"), \
         patch("ovos_media.player.VideoService"), \
         patch("ovos_media.player.WebService"), \
         patch("ovos_media.player.OcpMprisExporter"), \
         patch("ovos_media.player.Configuration", return_value={"media": {}}), \
         patch("ovos_media.player.OCPMediaCatalog") as MockCatalog, \
         patch("ovos_media.service.OCPVoiceSkill"), \
         patch("ovos_media.service.ProcessStatus") as MockStatus, \
         patch("ovos_media.service.Configuration", return_value={"media": {}}):
        MockStatus.return_value = MagicMock()
        catalog = MagicMock()
        MockCatalog.return_value = catalog
        svc = MediaService(bus=bus)
    return svc


class TestZombieServiceShutdown(unittest.TestCase):
    """shutdown() must remove every listener the bus edge added, across both
    OCPMediaPlayer and MediaService, and a shut-down service must not answer
    ping."""

    def test_construct_shutdown_cycle_twice_unregisters_every_ocp_handler(self):
        # NOTE: this deliberately does NOT assert on aggregate FakeBus
        # listener counts. A handful of OCPMediaPlayer handlers are bound
        # to both a plain 'ovos.common_play.*' topic and a legacy
        # 'recognizer_loop:*' topic that FakeBus internally mirrors (see
        # ovos_utils.fakebus._translator); FakeBus tracks those mirrored
        # registrations in a handler-keyed dedup table that can shadow the
        # unrelated plain-topic removal for the same handler — a FakeBus
        # test-double quirk, not something the bus edge controls. What it
        # DOES own is its registration list, which is asserted directly
        # instead.
        bus = FakeBus()

        for _ in range(2):
            svc = _make_service(bus)
            registered = list(svc.ocp.bus_api._registrations)
            self.assertTrue(registered, "construction should have registered handlers")
            svc.shutdown()
            self.assertEqual(svc.ocp.bus_api._registrations, [],
                            "shutdown must clear the bus edge registration list")

    def test_shutdown_calls_bus_remove_for_every_registered_ocp_handler(self):
        """Same assertion via a MagicMock bus, which has no dedup-table
        quirk: every (event, handler) pair passed to bus.on() during
        construction must be passed to bus.remove() during shutdown."""
        from ovos_media.service import MediaService
        bus = MagicMock()
        bus.on = MagicMock()
        bus.remove = MagicMock()

        with patch("ovos_media.player.AudioService"), \
             patch("ovos_media.player.VideoService"), \
             patch("ovos_media.player.WebService"), \
             patch("ovos_media.player.OcpMprisExporter"), \
                 patch("ovos_media.player.Configuration", return_value={"media": {}}), \
             patch("ovos_media.player.OCPMediaCatalog"), \
             patch("ovos_media.service.OCPVoiceSkill"), \
             patch("ovos_media.service.ProcessStatus") as MockStatus, \
             patch("ovos_media.service.Configuration", return_value={"media": {}}):
            MockStatus.return_value = MagicMock()
            svc = MediaService(bus=bus)

        registered_pairs = {(c.args[0], c.args[1]) for c in bus.on.call_args_list}
        svc.shutdown()
        removed_pairs = {(c.args[0], c.args[1]) for c in bus.remove.call_args_list}
        # every OCPMediaPlayer/MediaService handler that was registered on
        # this bus must also have been removed
        self.assertTrue(registered_pairs, "construction should have registered handlers")
        self.assertEqual(registered_pairs - removed_pairs, set(),
                        "shutdown left some registered handlers un-removed")

    def test_ping_gets_no_pong_after_shutdown(self):
        bus = FakeBus()
        svc = _make_service(bus)
        svc.shutdown()

        replies = []
        bus.on("ovos.common_play.pong", lambda m: replies.append(m))
        bus.emit(Message("ovos.common_play.ping"))
        self.assertEqual(replies, [], "a shut-down service must not answer ping")

    def test_status_not_answered_after_shutdown(self):
        bus = FakeBus()
        svc = _make_service(bus)
        svc.shutdown()

        replies = []
        bus.on("ovos.common_play.status.response", lambda m: replies.append(m))
        msg = Message("ovos.common_play.status")
        bus.emit(msg)
        # handle_status replies on message.response(); nothing should react
        self.assertEqual(replies, [])


class TestEmptyLikeGuarded(unittest.TestCase):
    """Liking with nothing playing must not persist an empty-string entry."""

    def _make_player(self):
        from ovos_media.player import OCPMediaPlayer
        p = OCPMediaPlayer.__new__(OCPMediaPlayer)
        p.bus = FakeBus()
        p.validate_source = False  # bypass @require_default_session gating
        p.now_playing = MagicMock()
        p.now_playing.original_uri = ""
        p.now_playing.title = ""
        p.now_playing.image = ""
        p.now_playing.artist = ""
        p.media = MagicMock()
        # a real store over a plain dict subclass (not a bare MagicMock) so
        # assertIn/assertEqual against its contents are meaningful, with
        # .store() stubbed out like the real JsonStorageXDG.store()
        # (persist-to-disk) would be.
        class _FakeStore(dict):
            def store(self):
                pass
        self.store = _FakeStore()
        p.media.likes = LikedSongsStore(self.store)
        return p

    def test_like_with_nothing_playing_does_not_store_empty_key(self):
        p = self._make_player()
        msg = Message("ovos.common_play.like", {})
        p.handle_like(msg)
        self.assertNotIn("", self.store)
        self.assertEqual(dict(self.store), {})

    def test_like_with_explicit_uri_still_stores(self):
        p = self._make_player()
        msg = Message("ovos.common_play.like", {"uri": "http://x.mp3", "title": "X"})
        p.handle_like(msg)
        self.assertIn("http://x.mp3", self.store)


class TestLateQueryBinding(unittest.TestCase):
    """opm.audio.query must never be reachable before self.ocp exists."""

    def test_no_attribute_error_during_slow_construction_window(self):
        """A query arriving while OCPMediaPlayer is still being constructed
        reaches no handler at all: the topic is bound only after self.ocp is
        assigned, so nothing answers early and nothing raises."""
        bus = FakeBus()
        errors = []
        bus.on("error", lambda m: errors.append(m))

        from ovos_media.service import MediaService
        from ovos_media.player import OCPMediaPlayer

        queried_during_construction = {}

        def slow_player(*args, **kwargs):
            # mid-construction: self.ocp is not assigned yet, so
            # opm.audio.query must NOT be bound.
            queried_during_construction["bound"] = "opm.audio.query" in bus.ee.event_names()
            bus.emit(Message("opm.audio.query"))
            return OCPMediaPlayer(*args, **kwargs)

        with patch("ovos_media.player.AudioService"), \
             patch("ovos_media.player.VideoService"), \
             patch("ovos_media.player.WebService"), \
             patch("ovos_media.player.OcpMprisExporter"), \
                 patch("ovos_media.player.Configuration", return_value={"media": {}}), \
             patch("ovos_media.player.OCPMediaCatalog"), \
             patch("ovos_media.service.OCPVoiceSkill"), \
             patch("ovos_media.service.ProcessStatus") as MockStatus, \
             patch("ovos_media.service.Configuration", return_value={"media": {}}), \
             patch("ovos_media.service.OCPMediaPlayer", slow_player):
            MockStatus.return_value = MagicMock()
            svc = MediaService(bus=bus)
            svc.ocp.audio_service.available_backends.return_value = {}

        self.assertFalse(queried_during_construction["bound"],
                        "opm.audio.query must not be bound before self.ocp exists")
        self.assertEqual(errors, [])
        # after full construction it IS bound and answers without error
        self.assertIn("opm.audio.query", bus.ee.event_names())
        responses = []
        bus.on("opm.audio.query.response", lambda m: responses.append(m))
        bus.emit(Message("opm.audio.query"))
        self.assertEqual(len(responses), 1)
        self.assertEqual(errors, [])


class TestDaemonStartup(unittest.TestCase):
    """A real OCPMediaPlayer and a real MediaService both construct on a
    FakeBus, with no Playlist patching and no __new__ bypass."""

    def test_ocp_media_player_constructs_on_fakebus(self):
        from ovos_media.player import OCPMediaPlayer
        bus = FakeBus()
        # plugin loading talks to entrypoints on the real system; keep that
        # minimal external surface mocked out but construct everything else
        # (Playlist, NowPlaying, OCPMediaCatalog, the three BaseMediaService
        # subclasses...) for real.
        player = OCPMediaPlayer(bus, config={})
        self.assertEqual(player.playlist.title, "Search Results")
        self.assertEqual(len(player.playlist), 0)

    def test_media_service_constructs_on_fakebus(self):
        from ovos_media.service import MediaService
        bus = FakeBus()
        service = MediaService(bus=bus)
        self.assertIsNotNone(service.ocp)
        self.assertEqual(service.ocp.playlist.title, "Search Results")
        # validate_source must be plumbed through to the voice front-end
        # (#90), which mirrors the player's session gate on its shuffle
        # intents. No explicit override was given to MediaService, so it
        # stores None (unresolved) and lets each collaborator read
        # media.validate_source live -- the player and the voice skill must
        # still agree with each other.
        self.assertIsNone(service.validate_source)
        self.assertIs(service.voice_skill.validate_source,
                      service.ocp.validate_source)
        self.assertTrue(service.ocp.validate_source)  # unset config -> True
        service.shutdown()


class TestPlayerShutdownReachesBackends(unittest.TestCase):
    """OCPMediaPlayer.shutdown() shuts down the audio/video/web
    BaseMediaService instances, not just the higher-level objects."""

    def test_shutdown_calls_service_shutdown(self):
        from ovos_media.player import OCPMediaPlayer
        bus = FakeBus()
        player = OCPMediaPlayer(bus, config={})
        player.audio_service.shutdown = MagicMock()
        player.video_service.shutdown = MagicMock()
        player.web_service.shutdown = MagicMock()
        player.now_playing.shutdown = MagicMock()
        player.media.shutdown = MagicMock()

        player.shutdown()

        player.audio_service.shutdown.assert_called_once_with()
        player.video_service.shutdown.assert_called_once_with()
        player.web_service.shutdown.assert_called_once_with()

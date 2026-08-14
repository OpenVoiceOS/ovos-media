"""Regression tests for four lifecycle defects:

L1 - shutdown() left bus listeners bound (zombie service kept answering
     ping/status/etc. after "shutdown").
L2 - a malformed liked-songs store entry crashed daemon startup.
L3 - liking with nothing playing persisted an empty-string store entry.
L4 - opm.audio.query was bound before self.ocp existed.
"""
import threading
import unittest
from unittest.mock import MagicMock, patch

from ovos_utils.fakebus import FakeBus
from ovos_bus_client.message import Message


def _listener_count(bus):
    return sum(len(bus.ee.listeners(e)) for e in bus.ee.event_names())


def _make_service(bus):
    from ovos_media.service import MediaService
    with patch("ovos_media.player.AudioService"), \
         patch("ovos_media.player.VideoService"), \
         patch("ovos_media.player.WebService"), \
         patch("ovos_media.player.OcpMprisExporter"), \
         patch("ovos_media.player.GUIInterface"), \
         patch("ovos_media.player.Configuration", return_value={"media": {}}), \
         patch("ovos_media.player.OCPMediaCatalog") as MockCatalog, \
         patch("ovos_media.service.ProcessStatus") as MockStatus, \
         patch("ovos_media.service.Configuration", return_value={"media": {}}):
        MockStatus.return_value = MagicMock()
        catalog = MagicMock()
        MockCatalog.return_value = catalog
        svc = MediaService(bus=bus)
    return svc


class TestL1ZombieServiceShutdown(unittest.TestCase):
    """shutdown() must remove every listener register_bus_handlers() added,
    across both OCPMediaPlayer and MediaService, and a shut-down service
    must not answer ping."""

    def test_construct_shutdown_cycle_twice_unregisters_every_ocp_handler(self):
        # NOTE: this deliberately does NOT assert on aggregate FakeBus
        # listener counts. A handful of OCPMediaPlayer handlers are bound
        # to both a plain 'ovos.common_play.*' topic and a legacy
        # 'recognizer_loop:*' topic that FakeBus internally mirrors (see
        # ovos_utils.fakebus._translator); FakeBus tracks those mirrored
        # registrations in a handler-keyed dedup table that can shadow the
        # unrelated plain-topic removal for the same handler — a FakeBus
        # test-double quirk, not something register_bus_handlers()/
        # unregister_bus_handlers() control. What they DO own is
        # OCPMediaPlayer._bus_events, which is asserted directly instead.
        bus = FakeBus()

        for _ in range(2):
            svc = _make_service(bus)
            registered = list(svc.ocp._bus_events)
            self.assertTrue(registered, "construction should have registered handlers")
            svc.shutdown()
            self.assertEqual(svc.ocp._bus_events, [],
                            "shutdown must clear the OCPMediaPlayer registration list")

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
             patch("ovos_media.player.GUIInterface"), \
             patch("ovos_media.player.Configuration", return_value={"media": {}}), \
             patch("ovos_media.player.OCPMediaCatalog"), \
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
        # OCPMediaPlayer.shutdown() must also tear down its GUIInterface
        # (self.gui = GUIInterface("ovos.common_play", bus=bus)), which
        # registers its own 'ovos.common_play.set' listener on construction
        # via bus.on() outside of register_bus_handlers()/_bus_events. Without
        # gui.shutdown() that listener leaked for the lifetime of the process
        # — one per OCPMediaPlayer ever constructed.
        svc.ocp.gui.shutdown.assert_called_once()

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


class TestL2LikedSongsStoreTolerance(unittest.TestCase):
    """A malformed liked-songs store entry must not crash daemon startup."""

    def _construct_real(self, store_values):
        """Construct a real OCPMediaCatalog (a real OVOSCommonPlaybackSkill,
        wired to a real FakeBus - same pattern as
        test_catalog_now_playing_intents.py) against the given liked_songs
        store contents, and return it (or let the exception propagate)."""
        from ovos_media.player import OCPMediaCatalog
        bus = FakeBus()
        fake_store = MagicMock()
        fake_store.items.return_value = list(store_values.items())
        fake_store.values.return_value = list(store_values.values())
        fake_store.path = "/tmp/fake_liked_songs.json"

        with patch("ovos_media.player.JsonStorageXDG", return_value=fake_store):
            catalog = OCPMediaCatalog(bus=bus, skill_id="ovos.common_play.favorites")
        return catalog

    def test_entry_missing_title_is_skipped(self):
        catalog = self._construct_real({
            "http://a.mp3": {"uri": "http://a.mp3"},  # no "title"
            "http://b.mp3": {"uri": "http://b.mp3", "title": "Good Song"},
        })
        self.assertIsNotNone(catalog)

    def test_entry_non_dict_value_is_skipped(self):
        catalog = self._construct_real({
            "http://a.mp3": "notadict",
            "http://b.mp3": {"uri": "http://b.mp3", "title": "Good Song"},
        })
        self.assertIsNotNone(catalog)

    def test_entry_list_value_is_skipped(self):
        catalog = self._construct_real({
            "http://a.mp3": [1, 2, 3],
        })
        self.assertIsNotNone(catalog)


class TestL3EmptyLikeGuarded(unittest.TestCase):
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
        # a plain dict subclass (not a bare MagicMock) so assertIn/assertEqual
        # against its contents are meaningful, with .store() stubbed out like
        # the real JsonStorageXDG.store() (persist-to-disk) would be.
        class _FakeStore(dict):
            def store(self):
                pass
        p.media.liked_songs = _FakeStore()
        p.media.speak_dialog = MagicMock()
        p._update_gui = MagicMock()
        p._liked_songs_lock = threading.RLock()
        return p

    def test_like_with_nothing_playing_does_not_store_empty_key(self):
        p = self._make_player()
        msg = Message("ovos.common_play.like", {})
        p.handle_like(msg)
        self.assertNotIn("", p.media.liked_songs)
        self.assertEqual(p.media.liked_songs, {})

    def test_like_with_explicit_uri_still_stores(self):
        p = self._make_player()
        msg = Message("ovos.common_play.like", {"uri": "http://x.mp3", "title": "X"})
        p.handle_like(msg)
        self.assertIn("http://x.mp3", p.media.liked_songs)


class TestL4LateQueryBinding(unittest.TestCase):
    """opm.audio.query must never be reachable before self.ocp exists."""

    def test_no_attribute_error_during_slow_construction_window(self):
        """Simulate a query arriving while OCPMediaPlayer is still being
        constructed (the historical crash window): since the handler is now
        registered only after self.ocp is assigned, the query cannot even
        reach a handler that doesn't exist yet - so no error is raised and
        no listener answers early."""
        bus = FakeBus()
        errors = []
        bus.on("error", lambda m: errors.append(m))

        from ovos_media.service import MediaService
        original_init_messagebus = MediaService.init_messagebus

        queried_during_construction = {}

        def patched_init_messagebus(self):
            original_init_messagebus(self)
            # at this point (post init_messagebus, pre self.ocp assignment)
            # opm.audio.query must NOT be bound yet.
            queried_during_construction["bound"] = "opm.audio.query" in bus.ee.event_names()
            bus.emit(Message("opm.audio.query"))

        with patch("ovos_media.player.AudioService"), \
             patch("ovos_media.player.VideoService"), \
             patch("ovos_media.player.WebService"), \
             patch("ovos_media.player.OcpMprisExporter"), \
             patch("ovos_media.player.GUIInterface"), \
             patch("ovos_media.player.Configuration", return_value={"media": {}}), \
             patch("ovos_media.player.OCPMediaCatalog"), \
             patch("ovos_media.service.ProcessStatus") as MockStatus, \
             patch("ovos_media.service.Configuration", return_value={"media": {}}), \
             patch.object(MediaService, "init_messagebus", patched_init_messagebus):
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


if __name__ == "__main__":
    unittest.main()

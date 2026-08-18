"""Regression tests for defects found during the 2026-08 ecosystem audit.

A1: OCPMediaPlayer/MediaService failed to construct at all because
    ``Playlist("Search Results")`` passed the title as a positional entry
    instead of the ``title`` keyword, tripping an assertion inside
    ``Playlist.add_entry``.
A2: BaseMediaService did not implement get_track_length/get_track_position/
    set_track_position, so player.py's calls to
    ``self.audio_service.{get,set}_track_position``/``get_track_length``
    raised AttributeError.
A3: Exceptions raised inside BaseMediaService.play() (via
    ``selected_service.load_track``) or inside
    ``handle_media_state_change`` (via ``self.current.play()``) propagated
    out of a ``threading.Timer`` thread and were silently swallowed by the
    interpreter, leaving the player permanently stuck without ever emitting
    an error state. An unsupported uri_type also silently logged and
    returned without informing the bus.
A4: ``load_services`` crashed on malformed ``*_players`` config blocks
    (non-dict values, missing "module" key).
"""
import threading
import unittest
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import MediaState


class TestA1DaemonStartup(unittest.TestCase):
    """A1: construct a REAL OCPMediaPlayer / MediaService on a FakeBus,
    with no Playlist patching and no __new__ bypass, and confirm startup
    survives."""

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
        # intents
        self.assertIs(service.voice_skill.validate_source,
                      service.validate_source)
        service.shutdown()


def _make_base_service(current=None):
    from ovos_media.media_backends.base import BaseMediaService
    svc = BaseMediaService.__new__(BaseMediaService)
    svc._init_runtime_state()
    svc.bus = FakeBus()
    svc.services = []
    svc.current = current
    svc.volume_is_low = False
    svc.service_lock = threading.Lock()
    svc.play_start_time = 0.0
    svc.namespace = "audio"
    svc._loaded = threading.Event()
    svc._loaded.set()
    return svc


class TestA2SeekApi(unittest.TestCase):
    """A2: BaseMediaService.{get_track_length,get_track_position,
    set_track_position} delegate to self.current, in milliseconds, and are
    safe when there is no current backend."""

    def test_get_track_length_delegates_to_current(self):
        current = MagicMock()
        current.get_track_length.return_value = 123456
        svc = _make_base_service(current)
        self.assertEqual(svc.get_track_length(), 123456)
        current.get_track_length.assert_called_once_with()

    def test_get_track_length_no_current_returns_none(self):
        svc = _make_base_service(None)
        self.assertIsNone(svc.get_track_length())

    def test_get_track_position_delegates_to_current(self):
        current = MagicMock()
        current.get_track_position.return_value = 42000
        svc = _make_base_service(current)
        self.assertEqual(svc.get_track_position(), 42000)
        current.get_track_position.assert_called_once_with()

    def test_get_track_position_no_current_returns_none(self):
        svc = _make_base_service(None)
        self.assertIsNone(svc.get_track_position())

    def test_set_track_position_delegates_to_current(self):
        current = MagicMock()
        svc = _make_base_service(current)
        svc.set_track_position(5000)
        current.set_track_position.assert_called_once_with(5000)

    def test_set_track_position_no_current_is_noop(self):
        svc = _make_base_service(None)
        # must not raise
        svc.set_track_position(5000)


class TestA3SilentPlayFailures(unittest.TestCase):
    """A3: exceptions raised by a crashing backend during play() or during
    handle_media_state_change() must emit MediaState.INVALID_MEDIA and clear
    self.current, rather than dying silently."""

    def test_play_load_track_exception_emits_invalid_media_and_clears_current(self):
        crashing = MagicMock()
        crashing.supported_uris.return_value = ["file"]
        crashing.load_track.side_effect = RuntimeError("boom")
        crashing.__class__.__name__ = "CrashingBackend"

        svc = _make_base_service(None)
        svc.services = [crashing]

        received = []
        svc.bus.on("ovos.common_play.media.state", lambda m: received.append(m))

        svc.play("file:///tmp/track.mp3")

        self.assertIsNone(svc.current)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].data["state"], MediaState.INVALID_MEDIA)

    def test_play_unsupported_uri_emits_invalid_media(self):
        svc = _make_base_service(None)
        svc.services = []

        received = []
        svc.bus.on("ovos.common_play.media.state", lambda m: received.append(m))

        svc.play("weirdscheme://nope")

        self.assertIsNone(svc.current)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].data["state"], MediaState.INVALID_MEDIA)

    def test_handle_media_state_change_play_exception_emits_invalid_media_and_clears_current(self):
        crashing = MagicMock()
        crashing.play.side_effect = RuntimeError("boom")

        svc = _make_base_service(crashing)

        received = []
        svc.bus.on("ovos.common_play.media.state", lambda m: received.append(m))

        svc.handle_media_state_change(Message("ovos.common_play.media.state",
                                             {"state": MediaState.LOADED_MEDIA}))

        self.assertIsNone(svc.current)
        # first call is the incoming LOADED_MEDIA-triggering message is not
        # re-emitted by us; only the INVALID_MEDIA emission we make matters
        invalid = [m for m in received if m.data.get("state") == MediaState.INVALID_MEDIA]
        self.assertEqual(len(invalid), 1)


class TestA4ConfigRobustness(unittest.TestCase):
    """A4: load_services must survive malformed *_players config blocks."""

    def _make_service_for_load(self, players_cfg):
        from ovos_media.media_backends.base import BaseMediaService
        svc = BaseMediaService.__new__(BaseMediaService)
        svc._init_runtime_state()
        svc.bus = FakeBus()
        svc.namespace = "audio"
        svc.config = {"audio_players": players_cfg}
        svc.plugin_loader = lambda: {}
        svc.service_lock = threading.Lock()
        svc.current = None
        svc.play_start_time = 0.0
        svc.volume_is_low = False
        svc._loaded = threading.Event()
        return svc

    def test_players_cfg_none_survives(self):
        svc = self._make_service_for_load(None)
        services = svc.load_services()
        self.assertEqual(services, [])

    def test_players_cfg_list_survives(self):
        svc = self._make_service_for_load(["not", "a", "dict"])
        services = svc.load_services()
        self.assertEqual(services, [])

    def test_players_cfg_string_survives(self):
        svc = self._make_service_for_load("oops")
        services = svc.load_services()
        self.assertEqual(services, [])

    def test_players_entry_missing_module_survives(self):
        svc = self._make_service_for_load({"myplayer": {"active": True}})
        services = svc.load_services()
        self.assertEqual(services, [])

    def test_players_entry_non_dict_value_survives(self):
        svc = self._make_service_for_load({"myplayer": "not-a-dict"})
        services = svc.load_services()
        self.assertEqual(services, [])

    def test_valid_entry_still_loads(self):
        plug = MagicMock()
        instance = MagicMock()
        instance.supported_uris.return_value = ["file"]
        plug.return_value = instance
        svc = self._make_service_for_load(
            {"myplayer": {"module": "ovos-fake-plugin", "active": True}})
        svc.plugin_loader = lambda: {"ovos-fake-plugin": plug}
        services = svc.load_services()
        self.assertEqual(len(services), 1)


class TestA5Shutdown(unittest.TestCase):
    """A5: OCPMediaPlayer.shutdown() shuts down the audio/video/web
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


if __name__ == "__main__":
    unittest.main()

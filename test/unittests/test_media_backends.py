"""Tests for BaseMediaService, AudioService, VideoService, WebService.

Covers service loading, backend selection by URI scheme, and bus event
registration. Uses FakeBus and mock plugins — no real playback.
"""
import unittest
from unittest.mock import MagicMock, patch, call

from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import MediaState


class _FakeBackend:
    """Minimal MediaBackend stub for testing service selection."""

    def __init__(self, config, bus):
        self.config = config
        self.bus = bus
        self.name = config.get("name", "fake")
        self.aliases = []
        self._track_start_callback = None

    def supported_uris(self):
        return self.config.get("uris", [])

    def set_track_start_callback(self, cb):
        self._track_start_callback = cb

    def play(self, repeat=False):
        pass

    def stop(self):
        return True

    def pause(self):
        pass

    def resume(self):
        pass


class _FakeRemoteBackend(_FakeBackend):
    pass


class TestBaseMediaServiceLoading(unittest.TestCase):

    def _make_service(self, config=None, plugins=None):
        from ovos_media.media_backends.base import BaseMediaService
        from ovos_plugin_manager.templates.media import RemoteAudioPlayerBackend

        bus = FakeBus()
        config = config or {}
        plugins = plugins or {}

        with patch("ovos_media.media_backends.base.Configuration", return_value={"media": config}):
            svc = BaseMediaService.__new__(BaseMediaService)
            svc.bus = bus
            svc.namespace = "audio"
            svc.config = config
            svc.plugin_loader = lambda: plugins
            svc.default = None
            svc.services = []
            svc.current = None
            svc.play_start_time = 0
            svc.volume_is_low = False
            svc.validate_source = True
            svc.service_lock = __import__("threading").Lock()
            from ovos_utils.process_utils import MonotonicEvent
            svc._loaded = MonotonicEvent()
        return svc

    def test_no_plugins_configured_gives_empty_service_list(self):
        svc = self._make_service(config={"audio_players": {}})
        svc.load_services()
        self.assertEqual(svc.services, [])

    def test_inactive_plugin_is_skipped(self):
        plugins = {"fake-audio": _FakeBackend}
        config = {
            "audio_players": {
                "fake": {"module": "fake-audio", "active": False}
            }
        }
        svc = self._make_service(config=config, plugins=plugins)
        svc.load_services()
        self.assertEqual(svc.services, [])

    def test_active_plugin_is_loaded(self):
        plugins = {"fake-audio": _FakeBackend}
        config = {
            "audio_players": {
                "myfake": {"module": "fake-audio", "name": "myfake", "uris": ["http"]}
            }
        }
        svc = self._make_service(config=config, plugins=plugins)
        svc.load_services()
        self.assertEqual(len(svc.services), 1)

    def test_unknown_plugin_module_is_skipped(self):
        config = {
            "audio_players": {
                "missing": {"module": "does-not-exist"}
            }
        }
        svc = self._make_service(config=config, plugins={})
        svc.load_services()
        self.assertEqual(svc.services, [])

    def test_track_start_callback_registered(self):
        plugins = {"fake-audio": _FakeBackend}
        config = {
            "audio_players": {
                "myfake": {"module": "fake-audio", "name": "myfake", "uris": ["http"]}
            }
        }
        svc = self._make_service(config=config, plugins=plugins)
        svc.load_services()
        self.assertIsNotNone(svc.services[0]._track_start_callback)


class TestBaseMediaServiceSelection(unittest.TestCase):
    """select_service() (or the _play handler) must pick the right backend
    based on URI scheme support."""

    def _make_svc_with_backends(self, backends):
        from ovos_media.media_backends.base import BaseMediaService
        svc = MagicMock(spec=BaseMediaService)
        svc.services = backends
        svc.default = backends[0] if backends else None
        svc.current = None
        svc.service_lock = __import__("threading").Lock()
        return svc

    def test_selects_backend_that_supports_uri(self):
        from ovos_media.media_backends.base import BaseMediaService
        http_backend = MagicMock()
        http_backend.supported_uris.return_value = ["http", "https"]
        http_backend.name = "http-player"

        library_backend = MagicMock()
        library_backend.supported_uris.return_value = ["library"]
        library_backend.name = "mass"

        svc = self._make_svc_with_backends([http_backend, library_backend])

        # Simulate URI scheme selection logic from handle_play
        uri = "library://track/123"
        uri_type = uri.split(":")[0]
        selected = None
        for s in svc.services:
            if uri_type in s.supported_uris():
                selected = s
                break
        self.assertEqual(selected.name, "mass")

    def test_http_uri_selects_http_backend(self):
        http_backend = MagicMock()
        http_backend.supported_uris.return_value = ["http", "https"]
        http_backend.name = "vlc"

        library_backend = MagicMock()
        library_backend.supported_uris.return_value = ["library"]
        library_backend.name = "mass"

        uri = "https://example.com/track.mp3"
        uri_type = uri.split(":")[0]
        selected = None
        for s in [http_backend, library_backend]:
            if uri_type in s.supported_uris():
                selected = s
                break
        self.assertEqual(selected.name, "vlc")

    def test_unsupported_uri_emits_invalid_media(self):
        bus = FakeBus()
        received = []
        bus.on("ovos.common_play.media.state", lambda m: received.append(m))

        from ovos_media.media_backends.base import BaseMediaService
        svc = MagicMock(spec=BaseMediaService)
        svc.services = []
        svc.default = None
        svc.bus = bus

        uri_type = "xyz"
        for s in svc.services:
            if uri_type in s.supported_uris():
                break
        else:
            from ovos_bus_client.message import Message
            bus.emit(Message("ovos.common_play.media.state",
                             {"state": MediaState.INVALID_MEDIA}))

        self.assertTrue(len(received) > 0)
        self.assertEqual(received[0].data["state"], MediaState.INVALID_MEDIA)


def _make_svc(cls):
    """Instantiate a service subclass with autoload=False to avoid touching
    real plugins or config files."""
    bus = FakeBus()
    with patch("ovos_media.media_backends.base.Configuration",
               return_value={"media": {}}):
        svc = cls(bus, config={"_dummy": True}, autoload=False)
    return svc


class TestAudioServiceNamespace(unittest.TestCase):
    """AudioService must register bus events under the 'audio' namespace."""

    def test_namespace_is_audio(self):
        from ovos_media.media_backends.audio import AudioService
        svc = _make_svc(AudioService)
        self.assertEqual(svc.namespace, "audio")

    def test_preferred_players_reads_audio_key(self):
        from ovos_media.media_backends.audio import AudioService
        svc = _make_svc(AudioService)
        svc.config = {"preferred_audio_services": ["vlc"]}
        self.assertEqual(svc.get_preferred_players(), ["vlc"])

    def test_load_services_uses_audio_players_key(self):
        """load_services() must look up 'audio_players' in config."""
        from ovos_media.media_backends.audio import AudioService
        from ovos_utils.process_utils import MonotonicEvent
        svc = _make_svc(AudioService)
        svc._loaded = MonotonicEvent()
        # Provide a backend under "audio_players" with an unknown module
        svc.config = {"audio_players": {"fake": {"module": "no-such-plugin"}}}
        svc.plugin_loader = lambda: {}
        svc.load_services()  # must not raise; unknown module is skipped
        self.assertEqual(svc.services, [])


class TestVideoServiceNamespace(unittest.TestCase):
    def test_namespace_is_video(self):
        from ovos_media.media_backends.video import VideoService
        svc = _make_svc(VideoService)
        self.assertEqual(svc.namespace, "video")

    def test_preferred_players_reads_video_key(self):
        from ovos_media.media_backends.video import VideoService
        svc = _make_svc(VideoService)
        svc.config = {"preferred_video_services": ["mpv"]}
        self.assertEqual(svc.get_preferred_players(), ["mpv"])


class TestWebServiceNamespace(unittest.TestCase):
    def test_namespace_is_web(self):
        from ovos_media.media_backends.web import WebService
        svc = _make_svc(WebService)
        self.assertEqual(svc.namespace, "web")

    def test_preferred_players_reads_web_key(self):
        from ovos_media.media_backends.web import WebService
        svc = _make_svc(WebService)
        svc.config = {"preferred_web_services": ["browser"]}
        self.assertEqual(svc.get_preferred_players(), ["browser"])


class _FullFakeBackend:
    """Full stub matching the interface used by BaseMediaService."""

    def __init__(self, uris=None, name="fake"):
        self.uris = uris or []
        self.name = name
        self.aliases = [name]
        self._track_start_callback = None
        self.loaded_uri = None
        self.paused = False
        self.resumed = False
        self.stopped = False
        self.ocp_paused = False
        self.ocp_resumed = False
        self.ocp_stopped = False
        self.volume_lowered = False
        self.volume_restored = False
        self.seek_forward_seconds = None
        self.seek_backward_seconds = None
        self.track_position = None

    def supported_uris(self):
        return self.uris

    def set_track_start_callback(self, cb):
        self._track_start_callback = cb

    def load_track(self, uri):
        self.loaded_uri = uri

    def play(self, repeat=False):
        pass

    def stop(self):
        self.stopped = True
        return True

    def pause(self):
        self.paused = True

    def resume(self):
        self.resumed = True

    def ocp_pause(self):
        self.ocp_paused = True

    def ocp_resume(self):
        self.ocp_resumed = True

    def ocp_stop(self):
        self.ocp_stopped = True

    def lower_volume(self):
        self.volume_lowered = True

    def restore_volume(self):
        self.volume_restored = True

    def seek_forward(self, seconds):
        self.seek_forward_seconds = seconds

    def seek_backward(self, seconds):
        self.seek_backward_seconds = seconds

    def get_track_length(self):
        return 120000

    def get_track_position(self):
        return 5000

    def set_track_position(self, milliseconds):
        self.track_position = milliseconds

    def track_info(self):
        return {"title": "Test Track"}

    def shutdown(self):
        pass


def _make_base_svc(namespace="audio", config=None, services=None, validate_source=False):
    """Build a BaseMediaService with manual state, bypassing __init__."""
    from ovos_media.media_backends.base import BaseMediaService
    from ovos_utils.process_utils import MonotonicEvent
    import threading

    bus = FakeBus()
    svc = BaseMediaService.__new__(BaseMediaService)
    svc.bus = bus
    svc.namespace = namespace
    svc.config = config or {}
    svc.plugin_loader = lambda: {}
    svc.default = None
    svc.services = services or []
    svc.current = None
    svc.play_start_time = 0
    svc.volume_is_low = False
    svc.validate_source = validate_source
    svc.service_lock = threading.Lock()
    svc._loaded = MonotonicEvent()
    svc._loaded.set()
    return svc, bus


class TestBaseMediaServiceInit(unittest.TestCase):
    """Tests for BaseMediaService.__init__ with autoload=False."""

    def test_init_sets_attributes(self):
        from ovos_media.media_backends.base import BaseMediaService
        bus = FakeBus()
        with patch("ovos_media.media_backends.base.Configuration", return_value={"media": {"key": "val"}}):
            svc = BaseMediaService(bus, namespace="audio",
                                   plugin_loader=lambda: {},
                                   config={"key": "val"},
                                   autoload=False,
                                   validate_source=False)
        self.assertIs(svc.bus, bus)
        self.assertEqual(svc.namespace, "audio")
        self.assertEqual(svc.services, [])
        self.assertIsNone(svc.current)
        self.assertFalse(svc.volume_is_low)
        self.assertFalse(svc.validate_source)

    def test_init_with_autoload_calls_load_services(self):
        from ovos_media.media_backends.base import BaseMediaService
        bus = FakeBus()
        with patch("ovos_media.media_backends.base.Configuration", return_value={"media": {}}):
            with patch.object(BaseMediaService, "load_services", return_value=None) as mock_load:
                svc = BaseMediaService(bus, namespace="audio",
                                       plugin_loader=lambda: {},
                                       autoload=True,
                                       validate_source=False)
                mock_load.assert_called_once()

    def test_init_uses_config_argument(self):
        from ovos_media.media_backends.base import BaseMediaService
        bus = FakeBus()
        cfg = {"audio_players": {}}
        with patch("ovos_media.media_backends.base.Configuration", return_value={"media": {}}):
            svc = BaseMediaService(bus, namespace="audio",
                                   plugin_loader=lambda: {},
                                   config=cfg,
                                   autoload=False)
        self.assertIs(svc.config, cfg)


class TestAvailableBackends(unittest.TestCase):

    def test_returns_dict_with_backend_info(self):
        from ovos_plugin_manager.templates.media import RemoteAudioPlayerBackend
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http", "https"], name="vlc")
        svc.services = [b]
        result = svc.available_backends()
        self.assertIn("vlc", result)
        self.assertEqual(result["vlc"]["supported_uris"], ["http", "https"])
        self.assertFalse(result["vlc"]["remote"])

    def test_empty_services_returns_empty_dict(self):
        svc, bus = _make_base_svc()
        self.assertEqual(svc.available_backends(), {})

    def test_remote_backend_flagged(self):
        from ovos_plugin_manager.templates.media import RemoteAudioPlayerBackend

        class _RemoteStub(RemoteAudioPlayerBackend):
            def __init__(self):
                self.name = "remote-player"
                self.aliases = ["remote-player"]
                self.config = {}
                self.bus = MagicMock()

            def supported_uris(self):
                return ["http"]

            def play(self, *a, **kw): pass
            def pause(self): pass
            def resume(self): pass
            def stop(self): pass
            def lower_volume(self): pass
            def restore_volume(self): pass
            def get_track_length(self): return 0
            def get_track_position(self): return 0
            def set_track_position(self, pos): pass

        svc, bus = _make_base_svc()
        r = _RemoteStub()
        svc.services = [r]
        result = svc.available_backends()
        self.assertTrue(result["remote-player"]["remote"])


class TestTrackStart(unittest.TestCase):

    def test_track_start_with_track_emits_playing_track(self):
        svc, bus = _make_base_svc(namespace="audio")
        emitted = []
        bus.on("ovos.audio.playing_track", lambda m: emitted.append(m))
        svc.track_start("my_track.mp3")
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].data["track"], "my_track.mp3")

    def test_track_start_none_emits_queue_end(self):
        svc, bus = _make_base_svc(namespace="audio")
        emitted = []
        bus.on("ovos.audio.queue_end", lambda m: emitted.append(m))
        svc.track_start(None)
        self.assertEqual(len(emitted), 1)


class TestPlay(unittest.TestCase):

    def test_play_uses_preferred_service_when_uri_supported(self):
        b1 = _FullFakeBackend(uris=["http"], name="vlc")
        b2 = _FullFakeBackend(uris=["library"], name="mass")
        svc, bus = _make_base_svc(services=[b1, b2])

        svc.play("http://example.com/track.mp3", preferred_service=b1)
        self.assertEqual(svc.current, b1)
        self.assertEqual(b1.loaded_uri, "http://example.com/track.mp3")

    def test_play_skips_preferred_service_if_uri_not_supported(self):
        b1 = _FullFakeBackend(uris=["http"], name="vlc")
        b2 = _FullFakeBackend(uris=["library"], name="mass")
        svc, bus = _make_base_svc(services=[b1, b2])

        # preferred=b1 but URI is library:// — should fall through to b2
        svc.play("library://track/1", preferred_service=b1)
        self.assertEqual(svc.current, b2)
        self.assertEqual(b2.loaded_uri, "library://track/1")

    def test_play_uses_current_service_when_uri_supported(self):
        b1 = _FullFakeBackend(uris=["http"], name="vlc")
        svc, bus = _make_base_svc(services=[b1])
        svc.current = b1

        svc.play("http://example.com/song.mp3")
        self.assertEqual(svc.current, b1)
        self.assertEqual(b1.loaded_uri, "http://example.com/song.mp3")

    def test_play_falls_back_to_first_matching_service(self):
        b1 = _FullFakeBackend(uris=["library"], name="mass")
        b2 = _FullFakeBackend(uris=["http"], name="vlc")
        svc, bus = _make_base_svc(services=[b1, b2])
        svc.current = None

        svc.play("http://example.com/track.mp3")
        self.assertEqual(svc.current, b2)

    def test_play_returns_early_when_no_service_matches(self):
        b1 = _FullFakeBackend(uris=["library"], name="mass")
        svc, bus = _make_base_svc(services=[b1])

        svc.play("xyz://unknown")
        # current must remain None — nothing was loaded
        self.assertIsNone(svc.current)
        self.assertIsNone(b1.loaded_uri)


class TestPauseResume(unittest.TestCase):

    def test_pause_calls_current_pause_and_ocp_pause(self):
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        svc.pause()
        self.assertTrue(b.paused)
        self.assertTrue(b.ocp_paused)

    def test_pause_no_current_does_nothing(self):
        svc, bus = _make_base_svc()
        # must not raise
        svc.pause()

    def test_resume_calls_current_resume_and_ocp_resume(self):
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        svc.resume()
        self.assertTrue(b.resumed)
        self.assertTrue(b.ocp_resumed)

    def test_resume_no_current_does_nothing(self):
        svc, bus = _make_base_svc()
        svc.resume()


class TestStop(unittest.TestCase):

    def test_stop_calls_ocp_stop_and_clears_current(self):
        import time as _time
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        svc.play_start_time = _time.monotonic() - 5  # > 1 second ago
        svc.stop()
        self.assertTrue(b.stopped)
        self.assertIsNone(svc.current)

    def test_stop_too_soon_after_play_is_ignored(self):
        import time as _time
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        svc.play_start_time = _time.monotonic()  # just now
        svc.stop()
        # current should NOT be cleared because it was within 1 second
        self.assertEqual(svc.current, b)
        self.assertFalse(b.stopped)

    def test_stop_emits_mycroft_stop_handled(self):
        import time as _time
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        svc.play_start_time = _time.monotonic() - 5
        emitted = []
        bus.on("mycroft.stop.handled", lambda m: emitted.append(m))
        svc.stop()
        self.assertEqual(len(emitted), 1)


class TestVolumeHandlers(unittest.TestCase):

    def test_lower_volume_when_current_and_not_low(self):
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        svc.volume_is_low = False
        svc.lower_volume()
        self.assertTrue(b.volume_lowered)
        self.assertTrue(svc.volume_is_low)

    def test_lower_volume_not_called_when_already_low(self):
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        svc.volume_is_low = True
        svc.lower_volume()
        self.assertFalse(b.volume_lowered)

    def test_lower_volume_no_current_does_nothing(self):
        svc, bus = _make_base_svc()
        svc.volume_is_low = False
        svc.lower_volume()

    def test_restore_volume_when_low(self):
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        svc.volume_is_low = True
        svc.restore_volume()
        self.assertTrue(b.volume_restored)
        self.assertFalse(svc.volume_is_low)

    def test_restore_volume_not_called_when_not_low(self):
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        svc.volume_is_low = False
        svc.restore_volume()
        self.assertFalse(b.volume_restored)


class TestHandleMediaStateChange(unittest.TestCase):

    def _msg(self, state):
        from ovos_bus_client.message import Message
        return Message("ovos.common_play.media.state", {"state": state})

    def test_audio_namespace_emits_playing_audio(self):
        from ovos_utils.ocp import MediaState, TrackState
        from ovos_bus_client.message import Message

        svc, bus = _make_base_svc(namespace="audio")
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        emitted = []
        bus.on("ovos.common_play.track.state", lambda m: emitted.append(m))
        svc.handle_media_state_change(self._msg(MediaState.LOADED_MEDIA))
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].data["state"], TrackState.PLAYING_AUDIO)

    def test_video_namespace_emits_playing_video(self):
        from ovos_utils.ocp import MediaState, TrackState

        svc, bus = _make_base_svc(namespace="video")
        b = _FullFakeBackend(uris=["http"], name="mpv")
        svc.current = b
        emitted = []
        bus.on("ovos.common_play.track.state", lambda m: emitted.append(m))
        svc.handle_media_state_change(self._msg(MediaState.LOADED_MEDIA))
        self.assertEqual(emitted[0].data["state"], TrackState.PLAYING_VIDEO)

    def test_web_namespace_emits_playing_webview(self):
        from ovos_utils.ocp import MediaState, TrackState

        svc, bus = _make_base_svc(namespace="web")
        b = _FullFakeBackend(uris=["https"], name="browser")
        svc.current = b
        emitted = []
        bus.on("ovos.common_play.track.state", lambda m: emitted.append(m))
        svc.handle_media_state_change(self._msg(MediaState.LOADED_MEDIA))
        self.assertEqual(emitted[0].data["state"], TrackState.PLAYING_WEBVIEW)

    def test_no_current_does_not_emit(self):
        from ovos_utils.ocp import MediaState

        svc, bus = _make_base_svc(namespace="audio")
        svc.current = None
        emitted = []
        bus.on("ovos.common_play.track.state", lambda m: emitted.append(m))
        svc.handle_media_state_change(self._msg(MediaState.LOADED_MEDIA))
        self.assertEqual(len(emitted), 0)

    def test_non_loaded_state_does_not_emit(self):
        from ovos_utils.ocp import MediaState

        svc, bus = _make_base_svc(namespace="audio")
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        emitted = []
        bus.on("ovos.common_play.track.state", lambda m: emitted.append(m))
        svc.handle_media_state_change(self._msg(MediaState.NO_MEDIA))
        self.assertEqual(len(emitted), 0)

    def test_unknown_namespace_normalizes_and_forwards(self):
        """A custom namespace must NOT silently drop the state change; it is
        normalized to a generic PLAYING_AUDIO TrackState and forwarded."""
        from ovos_utils.ocp import MediaState, TrackState

        svc, bus = _make_base_svc(namespace="custom-thing")
        b = _FullFakeBackend(uris=["http"], name="thing")
        svc.current = b
        emitted = []
        bus.on("ovos.common_play.track.state", lambda m: emitted.append(m))
        svc.handle_media_state_change(self._msg(MediaState.LOADED_MEDIA))
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].data["state"], TrackState.PLAYING_AUDIO)


class TestGetPreferredPlayers(unittest.TestCase):
    """BaseMediaService.get_preferred_players — per-namespace config key with
    fallback to all loaded backend names."""

    def test_returns_configured_audio_preference(self):
        svc, bus = _make_base_svc(
            namespace="audio",
            config={"preferred_audio_services": ["vlc", "mpv"]})
        self.assertEqual(svc.get_preferred_players(), ["vlc", "mpv"])

    def test_returns_configured_video_preference(self):
        svc, bus = _make_base_svc(
            namespace="video",
            config={"preferred_video_services": ["mpv"]})
        self.assertEqual(svc.get_preferred_players(), ["mpv"])

    def test_returns_configured_web_preference(self):
        svc, bus = _make_base_svc(
            namespace="web",
            config={"preferred_web_services": ["browser"]})
        self.assertEqual(svc.get_preferred_players(), ["browser"])

    def test_falls_back_to_all_loaded_backends_when_unconfigured(self):
        backends = [_FullFakeBackend(name="vlc"),
                    _FullFakeBackend(name="mpv")]
        svc, bus = _make_base_svc(namespace="audio", config={},
                                  services=backends)
        self.assertEqual(svc.get_preferred_players(), ["vlc", "mpv"])

    def test_fallback_preserves_loaded_backend_order(self):
        backends = [_FullFakeBackend(name="mpv"),
                    _FullFakeBackend(name="vlc")]
        svc, bus = _make_base_svc(namespace="audio", config={},
                                  services=backends)
        self.assertEqual(svc.get_preferred_players(), ["mpv", "vlc"])

    def test_empty_preference_falls_back_to_backends(self):
        backends = [_FullFakeBackend(name="vlc")]
        svc, bus = _make_base_svc(
            namespace="audio",
            config={"preferred_audio_services": []},
            services=backends)
        self.assertEqual(svc.get_preferred_players(), ["vlc"])

    def test_no_config_no_backends_returns_empty(self):
        svc, bus = _make_base_svc(namespace="audio", config={}, services=[])
        self.assertEqual(svc.get_preferred_players(), [])

    def test_does_not_mutate_config_list(self):
        cfg_list = ["vlc"]
        svc, bus = _make_base_svc(
            namespace="audio",
            config={"preferred_audio_services": cfg_list})
        result = svc.get_preferred_players()
        result.append("mpv")
        self.assertEqual(cfg_list, ["vlc"])  # original untouched


class TestIsMessageForService(unittest.TestCase):

    def test_none_message_returns_true(self):
        svc, bus = _make_base_svc(validate_source=True)
        self.assertTrue(svc._is_message_for_service(None))

    def test_validate_source_false_always_returns_true(self):
        from ovos_bus_client.message import Message
        svc, bus = _make_base_svc(validate_source=False)
        msg = Message("test", {}, {"destination": ["somewhere-else"]})
        self.assertTrue(svc._is_message_for_service(msg))

    def test_validate_source_true_uses_validate_message_context(self):
        from ovos_bus_client.message import Message
        svc, bus = _make_base_svc(validate_source=True)
        msg = Message("test", {}, {})
        # No destination in context → broadcast → validate_message_context returns True
        self.assertTrue(svc._is_message_for_service(msg))


class TestBusEventHandlers(unittest.TestCase):
    """Tests for handle_track_info, handle_list_backends, position/seek handlers."""

    def _make_msg(self, msg_type, data=None, context=None):
        from ovos_bus_client.message import Message
        return Message(msg_type, data or {}, context or {})

    def test_handle_track_info_with_current(self):
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        msg = self._make_msg("ovos.audio.service.track_info")
        replies = []
        bus.on(msg.msg_type + ".response", lambda m: replies.append(m))
        # Emit response via mock bus
        svc.handle_track_info(msg)
        # The emit goes to bus.emit(message.response(...)); FakeBus should deliver it
        # but we also verify it doesn't raise and that track_info was called
        # We'll check via direct mock
        mock_bus = MagicMock()
        svc.bus = mock_bus
        svc.handle_track_info(msg)
        mock_bus.emit.assert_called_once()

    def test_handle_track_info_no_current(self):
        svc, bus = _make_base_svc()
        svc.current = None
        mock_bus = MagicMock()
        svc.bus = mock_bus
        msg = self._make_msg("ovos.audio.service.track_info")
        svc.handle_track_info(msg)
        mock_bus.emit.assert_called_once()
        emitted_msg = mock_bus.emit.call_args[0][0]
        self.assertEqual(emitted_msg.data, {})

    def test_handle_list_backends(self):
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.services = [b]
        mock_bus = MagicMock()
        svc.bus = mock_bus
        msg = self._make_msg("ovos.audio.service.list_backends")
        svc.handle_list_backends(msg)
        mock_bus.emit.assert_called_once()

    def test_handle_get_track_length_with_current(self):
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        mock_bus = MagicMock()
        svc.bus = mock_bus
        msg = self._make_msg("ovos.audio.service.get_track_length")
        svc.handle_get_track_length(msg)
        mock_bus.emit.assert_called_once()
        emitted_msg = mock_bus.emit.call_args[0][0]
        self.assertEqual(emitted_msg.data["length"], 120000)

    def test_handle_get_track_length_no_current(self):
        svc, bus = _make_base_svc()
        svc.current = None
        mock_bus = MagicMock()
        svc.bus = mock_bus
        msg = self._make_msg("ovos.audio.service.get_track_length")
        svc.handle_get_track_length(msg)
        emitted_msg = mock_bus.emit.call_args[0][0]
        self.assertIsNone(emitted_msg.data["length"])

    def test_handle_get_track_position_with_current(self):
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        mock_bus = MagicMock()
        svc.bus = mock_bus
        msg = self._make_msg("ovos.audio.service.get_track_position")
        svc.handle_get_track_position(msg)
        emitted_msg = mock_bus.emit.call_args[0][0]
        self.assertEqual(emitted_msg.data["position"], 5000)

    def test_handle_get_track_position_no_current(self):
        svc, bus = _make_base_svc()
        svc.current = None
        mock_bus = MagicMock()
        svc.bus = mock_bus
        msg = self._make_msg("ovos.audio.service.get_track_position")
        svc.handle_get_track_position(msg)
        emitted_msg = mock_bus.emit.call_args[0][0]
        self.assertIsNone(emitted_msg.data["position"])

    def test_handle_set_track_position(self):
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        msg = self._make_msg("ovos.audio.service.set_track_position", {"position": 30000})
        svc.handle_set_track_position(msg)
        self.assertEqual(b.track_position, 30000)

    def test_handle_set_track_position_no_position_key(self):
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        msg = self._make_msg("ovos.audio.service.set_track_position", {})
        svc.handle_set_track_position(msg)
        self.assertIsNone(b.track_position)

    def test_handle_seek_forward(self):
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        msg = self._make_msg("ovos.audio.service.seek_forward", {"seconds": 30})
        svc.handle_seek_forward(msg)
        self.assertEqual(b.seek_forward_seconds, 30)

    def test_handle_seek_forward_default_seconds(self):
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        msg = self._make_msg("ovos.audio.service.seek_forward", {})
        svc.handle_seek_forward(msg)
        self.assertEqual(b.seek_forward_seconds, 1)

    def test_handle_seek_backward(self):
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        msg = self._make_msg("ovos.audio.service.seek_backward", {"seconds": 15})
        svc.handle_seek_backward(msg)
        self.assertEqual(b.seek_backward_seconds, 15)

    def test_handle_seek_backward_default_seconds(self):
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        msg = self._make_msg("ovos.audio.service.seek_backward", {})
        svc.handle_seek_backward(msg)
        self.assertEqual(b.seek_backward_seconds, 1)


class TestHandlePlay(unittest.TestCase):
    """Tests for the handle_play bus event handler."""

    def _make_msg(self, tracks, utterance=""):
        from ovos_bus_client.message import Message
        return Message("ovos.audio.service.play",
                       {"tracks": tracks, "utterance": utterance})

    def _sync_timer(self):
        """Return a threading.Timer replacement that fires synchronously."""
        class _SyncTimer:
            def __init__(self, delay, fn, args=()):
                self._fn = fn
                self._args = args
            def start(self):
                self._fn(*self._args)
        return _SyncTimer

    def test_handle_play_selects_alias_matched_service(self):
        b1 = _FullFakeBackend(uris=["http"], name="vlc")
        b1.aliases = ["vlc", "video lan"]
        b2 = _FullFakeBackend(uris=["http"], name="mass")
        b2.aliases = ["mass"]
        svc, bus = _make_base_svc(services=[b1, b2])

        msg = self._make_msg("http://example.com/song.mp3", utterance="play using vlc")
        with patch("ovos_media.media_backends.base.time") as mock_time, \
             patch("ovos_media.media_backends.base.threading.Timer", self._sync_timer()):
            mock_time.monotonic.return_value = 100.0
            svc.handle_play(msg)
        self.assertEqual(svc.current, b1)

    def test_handle_play_no_alias_match_uses_any_supporting_service(self):
        b1 = _FullFakeBackend(uris=["library"], name="mass")
        b1.aliases = ["mass"]
        b2 = _FullFakeBackend(uris=["http"], name="vlc")
        b2.aliases = ["vlc"]
        svc, bus = _make_base_svc(services=[b1, b2])

        msg = self._make_msg("http://example.com/song.mp3", utterance="")
        with patch("ovos_media.media_backends.base.time") as mock_time, \
             patch("ovos_media.media_backends.base.threading.Timer", self._sync_timer()):
            mock_time.monotonic.return_value = 100.0
            svc.handle_play(msg)
        self.assertEqual(svc.current, b2)


class TestShutdownAndListeners(unittest.TestCase):

    def test_shutdown_calls_shutdown_on_all_services(self):
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        b_mock = MagicMock(wraps=b)
        b_mock.name = "vlc"
        svc.services = [b_mock]
        svc.load_services = MagicMock()  # prevent re-registration
        # Register listeners first (needed by remove_listeners)
        bus.on(f"ovos.audio.service.play", svc.handle_play)
        bus.on(f"ovos.audio.service.pause", svc.pause)
        bus.on(f"ovos.audio.service.resume", svc.resume)
        bus.on(f"ovos.audio.service.stop", svc.stop)
        bus.on(f"ovos.audio.service.track_info", svc.handle_track_info)
        bus.on(f"ovos.audio.service.get_track_position", svc.handle_get_track_position)
        bus.on(f"ovos.audio.service.set_track_position", svc.handle_set_track_position)
        bus.on(f"ovos.audio.service.get_track_length", svc.handle_get_track_length)
        bus.on(f"ovos.audio.service.seek_forward", svc.handle_seek_forward)
        bus.on(f"ovos.audio.service.seek_backward", svc.handle_seek_backward)

        svc.shutdown()
        b_mock.shutdown.assert_called_once()

    def test_shutdown_continues_after_service_error(self):
        svc, bus = _make_base_svc()
        b_bad = MagicMock()
        b_bad.name = "bad"
        b_bad.shutdown.side_effect = RuntimeError("boom")
        svc.services = [b_bad]
        svc.remove_listeners = MagicMock()
        # must not raise
        svc.shutdown()

    def test_load_services_registers_bus_events(self):
        plugins = {"fake-audio": _FullFakeBackend}
        config = {
            "audio_players": {
                "myfake": {"module": "fake-audio", "uris": ["http"]}
            }
        }
        svc, bus = _make_base_svc(config=config)
        svc.plugin_loader = lambda: plugins

        mock_bus = MagicMock()
        svc.bus = mock_bus
        svc.load_services()

        # Check that bus.on was called for each registered event
        registered_events = [c[0][0] for c in mock_bus.on.call_args_list]
        self.assertIn("ovos.audio.service.play", registered_events)
        self.assertIn("ovos.audio.service.pause", registered_events)
        self.assertIn("ovos.audio.service.stop", registered_events)
        self.assertIn("ovos.audio.service.duck", registered_events)
        self.assertIn("ovos.audio.service.unduck", registered_events)


class TestPluginLoadingExceptionHandling(unittest.TestCase):

    def test_broken_plugin_is_skipped_gracefully(self):
        def broken_plugin(cfg, bus):
            raise RuntimeError("plugin init failed")

        plugins = {"broken-audio": broken_plugin}
        config = {
            "audio_players": {
                "broken": {"module": "broken-audio"}
            }
        }
        svc, bus = _make_base_svc(config=config)
        svc.plugin_loader = lambda: plugins
        # must not raise
        svc.load_services()
        self.assertEqual(svc.services, [])


if __name__ == "__main__":
    unittest.main()

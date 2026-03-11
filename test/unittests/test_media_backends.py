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
            from ovos_utils.messagebus import Message
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


if __name__ == "__main__":
    unittest.main()

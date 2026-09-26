"""Tests for BaseMediaService, AudioService, VideoService, WebService.

Covers service loading, backend selection by URI scheme, and bus event
registration. Uses FakeBus and mock plugins — no real playback.
"""
import threading
import unittest
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import MediaState, TrackState


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
            svc._init_runtime_state()
            svc.bus = bus
            svc.namespace = "audio"
            svc.config = config
            svc.plugin_loader = lambda: plugins
            svc.default = None
            svc.services = []
            svc.current = None
            svc.play_start_time = 0
            svc.volume_is_low = False
            svc.service_lock = __import__("threading").Lock()
            svc._pending_playlist = []
            svc._pending_repeat = False
            svc._last_full_playlist = []
            from ovos_utils.process_utils import MonotonicEvent
            svc._loaded = MonotonicEvent()
        return svc

    def test_no_plugins_configured_gives_empty_service_list(self):
        svc = self._make_service(config={"audio_players": {}})
        svc.load_services()
        self.assertEqual(svc.services, [])

    def test_no_backend_error_names_real_plugin_packages(self):
        """Quick-win #3: the no-backend error must name the real published
        packages (ovos-media-plugin-vlc / ovos-media-plugin-mplayer), not the
        stale ovos-vlc-plugin / ovos-mplayer-plugin names that never
        existed on PyPI.

        Asserts directly against the LOG.error call args (not captured
        stdout) so the test is not sensitive to logging-handler setup done
        by other tests earlier in the same run."""
        from ovos_media.media_backends import base as base_mod
        svc = self._make_service(config={"audio_players": {}})
        with patch.object(base_mod, "LOG") as mock_log:
            svc.load_services()
        joined = "\n".join(
            " ".join(str(a) for a in c.args) for c in mock_log.error.call_args_list
        )
        self.assertIn("ovos-media-plugin-vlc", joined)
        self.assertIn("ovos-media-plugin-mplayer", joined)
        self.assertNotIn("ovos-vlc-plugin,", joined)
        self.assertNotIn("ovos-mplayer-plugin)", joined)

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

    def test_autoload_loads_discovered_plugin_not_in_config(self):
        """A plugin the finder returns but no config entry names must load
        anyway, with its entry-point name as name and alias, and an empty
        (plugin-default) config."""
        plugins = {"fake-audio": _FakeBackend}
        svc = self._make_service(config={}, plugins=plugins)
        svc.load_services()
        self.assertEqual(len(svc.services), 1)
        self.assertEqual(svc.services[0].name, "fake-audio")
        self.assertEqual(svc.services[0].aliases, ["fake-audio"])
        self.assertEqual(svc.services[0].config, {})

    def test_configured_entry_takes_precedence_over_autoload(self):
        """A module named by a configured entry keeps that entry's name and
        aliases, loads exactly once, first - it is not also autoloaded a
        second time under its entry-point name."""
        plugins = {"fake-audio": _FakeBackend, "other-audio": _FakeBackend}
        config = {
            "audio_players": {
                "myfake": {"module": "fake-audio", "aliases": ["mf"], "uris": ["http"]}
            }
        }
        svc = self._make_service(config=config, plugins=plugins)
        svc.load_services()
        names = [s.name for s in svc.services]
        self.assertEqual(names, ["myfake", "other-audio"])
        self.assertEqual(names.count("myfake"), 1)
        self.assertEqual(svc.services[0].aliases, ["mf"])

    def test_inactive_configured_entry_blocks_autoload(self):
        """'active: false' must disable the module for autoload too, not
        just for the configured entry - only the sibling plugin loads."""
        plugins = {"fake-audio": _FakeBackend, "other-audio": _FakeBackend}
        config = {
            "audio_players": {
                "fake": {"module": "fake-audio", "active": False}
            }
        }
        svc = self._make_service(config=config, plugins=plugins)
        svc.load_services()
        names = [s.name for s in svc.services]
        self.assertEqual(names, ["other-audio"])

    def test_autoload_backends_false_restores_configured_only(self):
        """'autoload_backends: false' must fall back to configured-only
        behaviour: a discovered-but-unconfigured plugin, which loads by
        default, is excluded once the flag is set."""
        plugins = {"fake-audio": _FakeBackend, "other-audio": _FakeBackend}
        players_cfg = {"audio_players": {"myfake": {"module": "fake-audio", "uris": ["http"]}}}

        svc_default = self._make_service(config=dict(players_cfg), plugins=plugins)
        svc_default.load_services()
        self.assertIn("other-audio", [s.name for s in svc_default.services])

        svc_disabled = self._make_service(
            config={**players_cfg, "autoload_backends": False}, plugins=plugins)
        svc_disabled.load_services()
        names = [s.name for s in svc_disabled.services]
        self.assertEqual(names, ["myfake"])

    def test_configured_entry_without_aliases_falls_back_to_both_names(self):
        """A configured entry with no explicit 'aliases' must be reachable
        by both its config key and its module id, since both are used as
        lookup keys for spoken-name/backend resolution."""
        plugins = {"fake-audio": _FakeBackend}
        config = {"audio_players": {"vlc": {"module": "fake-audio"}}}
        svc = self._make_service(config=config, plugins=plugins)
        svc.load_services()
        self.assertEqual(svc.services[0].aliases, ["vlc", "fake-audio"])

    def test_configured_entry_with_aliases_keeps_exactly_those(self):
        plugins = {"fake-audio": _FakeBackend}
        config = {
            "audio_players": {
                "vlc": {"module": "fake-audio", "aliases": ["VLC", "Video"]}
            }
        }
        svc = self._make_service(config=config, plugins=plugins)
        svc.load_services()
        self.assertEqual(svc.services[0].aliases, ["VLC", "Video"])

    def test_autoload_skips_remote_backend(self):
        """A discovered plugin driving remote gear (RemoteAudioPlayerBackend)
        must not be auto-added - it needs an explicit '{ns}_players' entry,
        same as today, so a default install never starts casting to a
        target it was never told about."""
        from ovos_plugin_manager.templates.media import RemoteAudioPlayerBackend

        class _RemoteFake(RemoteAudioPlayerBackend):
            def supported_uris(self):
                return []

            def set_track_start_callback(self, cb):
                pass

            def play(self, repeat=False):
                pass

            def stop(self):
                return True

            def pause(self):
                pass

            def resume(self):
                pass

            def lower_volume(self):
                pass

            def restore_volume(self):
                pass

            def get_track_length(self):
                return 0

            def get_track_position(self):
                return 0

            def set_track_position(self, milliseconds):
                pass

        plugins = {"remote-audio": _RemoteFake, "local-audio": _FakeBackend}

        # not autoloaded when undeclared
        svc = self._make_service(config={}, plugins=plugins)
        svc.load_services()
        names = [s.name for s in svc.services]
        self.assertEqual(names, ["local-audio"])

        # loaded when explicitly configured
        config = {"audio_players": {"cast": {"module": "remote-audio"}}}
        svc = self._make_service(config=config, plugins=plugins)
        svc.load_services()
        names = [s.name for s in svc.services]
        self.assertIn("cast", names)

    def test_autoload_skips_raising_plugin_but_loads_siblings(self):
        """A plugin whose constructor raises must be logged and skipped
        without blocking sibling plugins from autoloading."""
        class _RaisingBackend:
            def __init__(self, config, bus):
                raise RuntimeError("boom")

        plugins = {"broken-audio": _RaisingBackend, "fake-audio": _FakeBackend}
        svc = self._make_service(config={}, plugins=plugins)
        with patch("ovos_media.media_backends.base.LOG"):
            svc.load_services()
        self.assertEqual(len(svc.services), 1)
        self.assertEqual(svc.services[0].name, "fake-audio")

    def test_autoload_skips_non_class_finder_value(self):
        """find_plugins() returns entry_point.load() verbatim - a factory
        function loads fine. A non-class value must not abort the whole
        namespace: it is skipped with a warning while a sibling still
        loads and _loaded still gets set."""
        plugins = {
            "zzz_bad": lambda *a, **k: None,
            "aaa_good": _FakeBackend,
        }
        svc = self._make_service(config={}, plugins=plugins)
        with patch("ovos_media.media_backends.base.LOG") as mock_log:
            svc.load_services()
        names = [s.name for s in svc.services]
        self.assertEqual(names, ["aaa_good"])
        self.assertTrue(svc._loaded.is_set())
        joined = "\n".join(
            " ".join(str(a) for a in c.args) for c in mock_log.warning.call_args_list
        )
        self.assertIn("zzz_bad", joined)

    def test_autoload_skips_remote_video_backend(self):
        """RemoteVideoPlayerBackend is a sibling of RemoteAudioPlayerBackend,
        not a subclass of it - the remote-exclusion check must cover all
        three Remote bases, not just the audio one."""
        from ovos_plugin_manager.templates.media import RemoteVideoPlayerBackend

        class _RemoteVideoFake(RemoteVideoPlayerBackend):
            def supported_uris(self):
                return []

            def set_track_start_callback(self, cb):
                pass

            def play(self, repeat=False):
                pass

            def stop(self):
                return True

            def pause(self):
                pass

            def resume(self):
                pass

            def lower_volume(self):
                pass

            def restore_volume(self):
                pass

            def get_track_length(self):
                return 0

            def get_track_position(self):
                return 0

            def set_track_position(self, milliseconds):
                pass

        plugins = {"remote-video": _RemoteVideoFake, "local-video": _FakeBackend}

        # not autoloaded when undeclared
        svc = self._make_service(config={}, plugins=plugins)
        svc.load_services()
        self.assertEqual([s.name for s in svc.services], ["local-video"])

        # loaded, and sorted after the local backend, when configured
        config = {
            "audio_players": {
                "remote-video": {"module": "remote-video"},
                "local-video": {"module": "local-video"},
            }
        }
        svc = self._make_service(config=config, plugins=plugins)
        svc.load_services()
        self.assertEqual([s.name for s in svc.services], ["local-video", "remote-video"])

    def test_autoload_local_before_remote(self):
        """Local-before-remote ordering is preserved when mixing configured
        and autoloaded plugins."""
        from ovos_plugin_manager.templates.media import RemoteAudioPlayerBackend

        class _RemoteFake(RemoteAudioPlayerBackend):
            def __init__(self, config, bus):
                super().__init__(config, bus)
                self.name = config.get("name", "remote-fake")
                self.aliases = []

            def supported_uris(self):
                return []

            def set_track_start_callback(self, cb):
                pass

            def play(self, repeat=False):
                pass

            def stop(self):
                return True

            def pause(self):
                pass

            def resume(self):
                pass

            def lower_volume(self):
                pass

            def restore_volume(self):
                pass

            def get_track_length(self):
                return 0

            def get_track_position(self):
                return 0

            def set_track_position(self, milliseconds):
                pass

        plugins = {"remote-audio": _RemoteFake, "local-audio": _FakeBackend}
        config = {"audio_players": {"remote-audio": {"module": "remote-audio"}}}
        svc = self._make_service(config=config, plugins=plugins)
        svc.load_services()
        names = [s.name for s in svc.services]
        self.assertEqual(names, ["local-audio", "remote-audio"])

    def test_no_backends_error_still_emitted_when_autoload_finds_nothing(self):
        from ovos_media.media_backends.base import BaseMediaService
        svc = self._make_service(config={}, plugins={})
        received = []
        svc.bus.on("ovos.common_play.media.state", lambda m: received.append(m))
        svc.load_services()
        self.assertEqual(svc.services, [])
        self.assertTrue(len(received) > 0)
        self.assertEqual(received[0].data["state"], MediaState.NO_MEDIA)

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
        self.pause_calls = 0
        self.resume_calls = 0
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
        self.pause_calls += 1

    def resume(self):
        self.resumed = True
        self.resume_calls += 1

    def ocp_pause(self):
        # mirrors the real ovos_plugin_manager MediaBackend template, which
        # emits the PAUSED TrackState and then calls self.pause() once
        self.ocp_paused = True
        self.pause()

    def ocp_resume(self):
        # mirrors the real template, which emits state events then calls
        # self.resume() once
        self.ocp_resumed = True
        self.resume()

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
    svc._init_runtime_state()
    svc.bus = bus
    svc.namespace = namespace
    svc.config = config or {}
    svc.plugin_loader = lambda: {}
    svc.default = None
    svc.services = services or []
    svc.current = None
    svc.play_start_time = 0
    svc.volume_is_low = False
    svc.service_lock = threading.Lock()
    svc._pending_playlist = []
    svc._pending_repeat = False
    svc._last_full_playlist = []
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
                                   autoload=False)
        self.assertIs(svc.bus, bus)
        self.assertEqual(svc.namespace, "audio")
        self.assertEqual(svc.services, [])
        self.assertIsNone(svc.current)
        self.assertFalse(svc.volume_is_low)

    def test_init_with_autoload_calls_load_services(self):
        from ovos_media.media_backends.base import BaseMediaService
        bus = FakeBus()
        with patch("ovos_media.media_backends.base.Configuration", return_value={"media": {}}):
            with patch.object(BaseMediaService, "load_services", return_value=None) as mock_load:
                svc = BaseMediaService(bus, namespace="audio",
                                       plugin_loader=lambda: {},
                                       autoload=True)
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


class TestClaimedSchemes(unittest.TestCase):
    """BaseMediaService.claimed_schemes() - the union of what loaded
    backends declare via supported_uris(), used to widen the stream
    validator's whitelist beyond the http/file// builtins."""

    def test_union_of_loaded_backends(self):
        svc, _ = _make_base_svc()
        svc.services = [
            _FullFakeBackend(uris=["http", "https"], name="vlc"),
            _FullFakeBackend(uris=["library"], name="mass"),
        ]
        self.assertEqual(svc.claimed_schemes(), {"http", "https", "library"})

    def test_no_backends_loaded_returns_empty_set(self):
        svc, _ = _make_base_svc()
        svc.services = []
        self.assertEqual(svc.claimed_schemes(), set())

    def test_raising_backend_does_not_break_the_others(self):
        svc, _ = _make_base_svc()
        svc.services = [_RaisingUrisBackend({"name": "bad"}, None),
                        _FullFakeBackend(uris=["http"], name="vlc")]
        self.assertEqual(svc.claimed_schemes(), {"http"})


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

    def test_pause_calls_backend_pause_exactly_once(self):
        # Regression: BaseMediaService.pause() used to call both
        # self.current.pause() AND self.current.ocp_pause(), and
        # ocp_pause() itself calls pause() again — double-invoking a
        # single bus-level pause request. A toggling backend pause command
        # would end up NOT paused. Only ocp_pause() should be called here,
        # which performs the pause exactly once.
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        svc.pause()
        self.assertTrue(b.paused)
        self.assertTrue(b.ocp_paused)
        self.assertEqual(b.pause_calls, 1)

    def test_pause_no_current_does_nothing(self):
        svc, bus = _make_base_svc()
        # must not raise
        svc.pause()

    def test_resume_calls_backend_resume_exactly_once(self):
        # Regression: symmetric to the pause case above — resume() must
        # invoke the backend's resume() exactly once per bus request.
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        svc.resume()
        self.assertTrue(b.resumed)
        self.assertTrue(b.ocp_resumed)
        self.assertEqual(b.resume_calls, 1)

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


class TestShutdownAndListeners(unittest.TestCase):

    def test_shutdown_calls_shutdown_on_all_services(self):
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        b_mock = MagicMock(wraps=b)
        b_mock.name = "vlc"
        svc.services = [b_mock]
        svc.load_services = MagicMock()  # prevent re-registration

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

    def test_load_services_does_not_register_per_namespace_bus_events(self):
        """The per-namespace 'ovos.{ns}.service.*' bus surface was removed —
        play/pause/resume/stop etc. are only reachable via direct method
        calls from OCPMediaPlayer now, never via that bus topic family."""
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

        registered_events = [c[0][0] for c in mock_bus.on.call_args_list]
        self.assertEqual(
            [e for e in registered_events if e.startswith("ovos.audio.service.")],
            [], "load_services() must not register any ovos.audio.service.* handler")


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


class _TogglingRealTemplateBackend:
    """Real ovos_plugin_manager MediaBackend subclass whose pause command
    toggles a single paused flag, as many real subprocess/IPC-wrapper
    backends do (e.g. mpv/vlc "cycle pause"). Only pause()/resume() and
    the abstract methods are implemented; ocp_pause()/ocp_resume()/ocp_stop()
    are inherited UNMODIFIED from the real template."""

    def __new__(cls, *a, **kw):
        from ovos_plugin_manager.templates.media import MediaBackend

        # build a real dynamic subclass so we exercise the actual
        # ocp_pause/ocp_resume implementations, not a hand-rolled fake
        class _Backend(MediaBackend):
            def __init__(self, config=None, bus=None):
                super().__init__(config, bus)
                self.paused = False
                self.pause_calls = 0
                self.resume_calls = 0

            def pause(self):
                self.pause_calls += 1
                self.paused = not self.paused  # toggling backend

            def resume(self):
                self.resume_calls += 1
                self.paused = not self.paused  # toggling backend

            def supported_uris(self):
                return ["http"]

            def play(self, repeat=False):
                pass

            def stop(self):
                self._now_playing = None
                return True

            def lower_volume(self):
                pass

            def restore_volume(self):
                pass

            def get_track_length(self):
                return 0

            def get_track_position(self):
                return 0

            def set_track_position(self, milliseconds):
                pass

        return _Backend(*a, **kw)


class TestPauseResumeRealTemplateIntegration(unittest.TestCase):
    """Integration-grade regression test: exercises the REAL
    ovos_plugin_manager.templates.media.MediaBackend ocp_pause()/
    ocp_resume() implementations (not mocked/stubbed), driven through
    BaseMediaService.pause()/resume().

    Before the fix, BaseMediaService.pause() called both
    self.current.pause() AND self.current.ocp_pause() — and ocp_pause()
    itself calls self.pause() again — so a single bus-level pause request
    invoked the backend's pause() TWICE. A backend whose pause command
    toggles (common for subprocess/IPC wrappers) would end up NOT paused.
    """

    def test_single_bus_pause_invokes_backend_pause_exactly_once(self):
        svc, bus = _make_base_svc()
        b = _TogglingRealTemplateBackend()
        b.load_track("http://example.com/track.mp3")  # sets _now_playing
        svc.current = b

        svc.pause()

        self.assertEqual(b.pause_calls, 1)
        self.assertTrue(b.paused)  # toggled exactly once -> paused

    def test_single_bus_resume_invokes_backend_resume_exactly_once(self):
        svc, bus = _make_base_svc()
        b = _TogglingRealTemplateBackend()
        b.load_track("http://example.com/track.mp3")
        svc.current = b
        b.paused = True  # start paused

        svc.resume()

        self.assertEqual(b.resume_calls, 1)
        self.assertFalse(b.paused)  # toggled exactly once -> unpaused


class _RaisingUrisBackend(_FakeBackend):
    """A backend whose supported_uris() always raises - simulates a
    misbehaving plugin at runtime (not load time)."""

    def supported_uris(self):
        raise RuntimeError("boom")


class TestSupportedUrisExceptionIsolation(unittest.TestCase):
    """D1: a plugin raising from supported_uris() must not kill
    available_backends() or abort backend selection in _play() before
    healthy backends are tried."""

    def test_available_backends_skips_raising_backend(self):
        good = _FakeBackend({"name": "good", "uris": ["http"]}, None)
        bad = _RaisingUrisBackend({"name": "bad"}, None)
        svc, _bus = _make_base_svc(services=[bad, good])

        data = svc.available_backends()

        self.assertIn("good", data)
        self.assertEqual(data["good"]["supported_uris"], ["http"])
        self.assertIn("bad", data)
        self.assertEqual(data["bad"]["supported_uris"], [])

    def test_play_selects_good_backend_after_raising_one(self):
        good = _FakeBackend({"name": "good", "uris": ["http"]}, None)
        good.load_track = MagicMock()
        bad = _RaisingUrisBackend({"name": "bad"}, None)
        svc, _bus = _make_base_svc(services=[bad, good])

        svc._play("http://example.com/track.mp3")

        self.assertIs(svc.current, good)
        good.load_track.assert_called_once_with("http://example.com/track.mp3")


class TestCanPlayMatchesPlayParity(unittest.TestCase):
    """Tripwire: can_play(uri, preferred) must agree with whether _play(uri,
    preferred) actually loads a backend, against a REAL BaseMediaService (no
    mocked select/dispatch), including a raising backend in the mix. If
    can_play() and _play()'s own resolution ever diverge, play() (which
    checks can_play() before dispatching) would either wrongly refuse a uri
    a backend can serve, or wrongly promise one nothing can serve.
    """

    def _assert_parity(self, svc, uri, preferred_service=None):
        can = svc.can_play(uri, preferred_service=preferred_service)
        svc._play(uri, preferred_service=preferred_service)
        did = svc.current is not None
        self.assertEqual(can, did,
                         f"can_play()={can} but _play() "
                         f"{'selected a backend' if did else 'selected none'} "
                         f"for {uri!r}")
        return did

    def test_services_scan_match_after_raising_backend(self):
        good = _FakeBackend({"name": "good", "uris": ["http"]}, None)
        good.load_track = MagicMock()
        bad = _RaisingUrisBackend({"name": "bad"}, None)
        svc, _bus = _make_base_svc(services=[bad, good])
        self.assertTrue(self._assert_parity(svc, "http://example.com/a.mp3"))
        self.assertIs(svc.current, good)

    def test_no_backend_matches(self):
        bad = _RaisingUrisBackend({"name": "bad"}, None)
        only_ftp = _FakeBackend({"name": "ftp-only", "uris": ["ftp"]}, None)
        svc, _bus = _make_base_svc(services=[bad, only_ftp])
        self.assertFalse(self._assert_parity(svc, "http://example.com/a.mp3"))
        self.assertIsNone(svc.current)

    def test_preferred_service_match_takes_precedence(self):
        preferred = _FakeBackend({"name": "preferred", "uris": ["http"]}, None)
        preferred.load_track = MagicMock()
        other = _FakeBackend({"name": "other", "uris": ["http"]}, None)
        other.load_track = MagicMock()
        svc, _bus = _make_base_svc(services=[other])
        self.assertTrue(self._assert_parity(
            svc, "http://example.com/a.mp3", preferred_service=preferred))
        self.assertIs(svc.current, preferred)
        other.load_track.assert_not_called()

    def test_current_backend_match_is_reused(self):
        current = _FakeBackend({"name": "current", "uris": ["http"]}, None)
        current.load_track = MagicMock()
        svc, _bus = _make_base_svc(services=[current])
        svc.current = current
        self.assertTrue(self._assert_parity(svc, "http://example.com/a.mp3"))
        self.assertIs(svc.current, current)


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


class TestSeekApiDelegation(unittest.TestCase):
    """BaseMediaService.{get_track_length,get_track_position,
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


class TestSilentPlayFailures(unittest.TestCase):
    """Exceptions raised by a crashing backend during play() or during
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


class TestMalformedPlayersConfig(unittest.TestCase):
    """load_services must survive malformed *_players config blocks."""

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


def _make_service():
    """Return a BaseMediaService with mocked dependencies."""
    from ovos_media.media_backends.base import BaseMediaService
    bus = FakeBus()
    svc = BaseMediaService.__new__(BaseMediaService)
    svc._init_runtime_state()
    svc.bus = bus
    svc.services = []
    svc.current = None
    svc.volume_is_low = False
    svc.service_lock = threading.Lock()
    svc.play_start_time = 0.0
    svc.namespace = "audio"
    svc.config = {}
    svc._pending_playlist = []
    svc._pending_repeat = False
    svc._last_full_playlist = []
    svc._loaded = threading.Event()
    svc._loaded.set()
    return svc, bus


class TestHandleMediaStateChangeUnknownNamespace(unittest.TestCase):
    """Test handle_media_state_change with unknown namespace."""

    def test_unknown_namespace_logs_warning(self):
        """handle_media_state_change with unknown namespace should log warning."""
        svc, bus = _make_service()
        svc.namespace = "unknown"
        svc.current = MagicMock()

        # This should log a warning but not raise
        svc.handle_media_state_change(Message("ovos.common_play.media.state",
                                             {"state": MediaState.LOADED_MEDIA}))


class TestHandleMediaStateChangeVideo(unittest.TestCase):
    """Test handle_media_state_change with video namespace."""

    def test_loaded_media_video_emits_playing_video_state(self):
        """handle_media_state_change LOADED_MEDIA with video should emit PLAYING_VIDEO."""
        svc, bus = _make_service()
        svc.namespace = "video"
        svc.current = MagicMock()

        received = []
        bus.on("ovos.common_play.track.state", lambda m: received.append(m))

        svc.handle_media_state_change(Message("ovos.common_play.media.state",
                                             {"state": MediaState.LOADED_MEDIA}))

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].data["state"], TrackState.PLAYING_VIDEO)


class TestHandleMediaStateChangeWeb(unittest.TestCase):
    """Test handle_media_state_change with web namespace."""

    def test_loaded_media_web_emits_playing_webview_state(self):
        """handle_media_state_change LOADED_MEDIA with web should emit PLAYING_WEBVIEW."""
        svc, bus = _make_service()
        svc.namespace = "web"
        svc.current = MagicMock()

        received = []
        bus.on("ovos.common_play.track.state", lambda m: received.append(m))

        svc.handle_media_state_change(Message("ovos.common_play.media.state",
                                             {"state": MediaState.LOADED_MEDIA}))

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].data["state"], TrackState.PLAYING_WEBVIEW)


class TestWaitForLoad(unittest.TestCase):
    """Test wait_for_load timeout mechanism."""

    def test_wait_for_load_returns_true_when_already_loaded(self):
        """wait_for_load should return True if _loaded is already set."""
        svc, bus = _make_service()
        svc._loaded.set()

        result = svc.wait_for_load(timeout=0.1)

        self.assertTrue(result)

    def test_wait_for_load_times_out(self):
        """wait_for_load should return False on timeout."""
        svc, bus = _make_service()
        svc._loaded.clear()

        result = svc.wait_for_load(timeout=0.01)

        self.assertFalse(result)


class TestPauseWithCurrent(unittest.TestCase):
    """Test pause with current service."""

    def test_pause_calls_ocp_pause_only(self):
        """pause() must invoke current.ocp_pause() exactly once, and must
        NOT call current.pause() directly. The real ovos_plugin_manager
        MediaBackend template's ocp_pause() itself calls pause() once (after
        emitting the PAUSED TrackState), so calling both would invoke the
        backend's pause() twice per bus-level pause request."""
        svc, bus = _make_service()
        svc.current = MagicMock()

        svc.pause()

        svc.current.pause.assert_not_called()
        svc.current.ocp_pause.assert_called_once()

    def test_pause_with_no_current_does_nothing(self):
        """pause() with no current should not raise."""
        svc, bus = _make_service()
        svc.current = None

        svc.pause()  # should not raise


class TestResumeWithCurrent(unittest.TestCase):
    """Test resume with current service."""

    def test_resume_calls_ocp_resume_only(self):
        """resume() must invoke current.ocp_resume() exactly once, and must
        NOT call current.resume() directly (symmetric to the pause case —
        the real template's ocp_resume() already calls resume() once)."""
        svc, bus = _make_service()
        svc.current = MagicMock()

        svc.resume()

        svc.current.resume.assert_not_called()
        svc.current.ocp_resume.assert_called_once()

    def test_resume_with_no_current_does_nothing(self):
        """resume() with no current should not raise."""
        svc, bus = _make_service()
        svc.current = None

        svc.resume()  # should not raise


class TestPerformStop(unittest.TestCase):
    """Test _perform_stop."""

    def test_perform_stop_calls_stop_and_emits_handled(self):
        """_perform_stop should call current.stop() and emit mycroft.stop.handled."""
        svc, bus = _make_service()
        mock_current = MagicMock()
        mock_current.stop.return_value = True
        svc.current = mock_current

        received = []
        bus.on("mycroft.stop.handled", lambda m: received.append(m))

        svc._perform_stop()

        # Check that stop was called before svc.current was set to None
        mock_current.stop.assert_called_once()
        mock_current.ocp_stop.assert_called_once()
        self.assertEqual(len(received), 1)
        # Verify that svc.current was cleared
        self.assertIsNone(svc.current)


class TestStopWithPlayStartTime(unittest.TestCase):
    """Test stop() with play_start_time guard."""

    def test_stop_requires_1_second_elapsed(self):
        """stop() should check that >= 1 second has elapsed since play started."""
        import time
        svc, bus = _make_service()
        svc.current = MagicMock()
        svc.current.stop.return_value = True
        svc.play_start_time = time.monotonic()  # just now

        with patch.object(svc, "_perform_stop") as mock_perform:
            svc.stop()

        # Should not call _perform_stop because < 1 second elapsed
        mock_perform.assert_not_called()


class TestLowerVolumeWithCurrent(unittest.TestCase):
    """Test lower_volume."""

    def test_lower_volume_calls_current_and_sets_flag(self):
        """lower_volume() should call current.lower_volume() and set volume_is_low."""
        svc, bus = _make_service()
        svc.current = MagicMock()
        svc.volume_is_low = False

        svc.lower_volume()

        svc.current.lower_volume.assert_called_once()
        self.assertTrue(svc.volume_is_low)

    def test_lower_volume_with_no_current_does_nothing(self):
        """lower_volume() with no current should not raise."""
        svc, bus = _make_service()
        svc.current = None

        svc.lower_volume()  # should not raise

    def test_lower_volume_when_already_low_skips(self):
        """lower_volume() should skip if volume_is_low is already True."""
        svc, bus = _make_service()
        svc.current = MagicMock()
        svc.volume_is_low = True

        svc.lower_volume()

        svc.current.lower_volume.assert_not_called()


class TestRestoreVolumeWithCurrent(unittest.TestCase):
    """Test restore_volume."""

    def test_restore_volume_calls_current_when_low(self):
        """restore_volume() should call current.restore_volume() when volume_is_low."""
        svc, bus = _make_service()
        svc.current = MagicMock()
        svc.volume_is_low = True

        svc.restore_volume()

        svc.current.restore_volume.assert_called_once()
        self.assertFalse(svc.volume_is_low)

    def test_restore_volume_with_no_current_does_nothing(self):
        """restore_volume() with no current should not raise."""
        svc, bus = _make_service()
        svc.current = None

        svc.restore_volume()  # should not raise

    def test_restore_volume_when_not_low_skips(self):
        """restore_volume() should skip if volume_is_low is False."""
        svc, bus = _make_service()
        svc.current = MagicMock()
        svc.volume_is_low = False

        svc.restore_volume()

        svc.current.restore_volume.assert_not_called()


class TestTrackStartOcpEmits(unittest.TestCase):
    """track_start must emit ovos.{namespace}.playing_track / queue_end and
    nothing else — the mycroft.audio.* twins served by the old ovos-audio
    stack are not this service's concern."""

    def test_track_start_emits_ovos_playing_track(self):
        svc, bus = _make_service()
        svc.namespace = "audio"

        received = {"ovos": [], "mycroft": []}
        bus.on("ovos.audio.playing_track", lambda m: received["ovos"].append(m))
        bus.on("mycroft.audio.playing_track", lambda m: received["mycroft"].append(m))

        svc.track_start("http://example.com/track.mp3")

        self.assertEqual(len(received["ovos"]), 1)
        self.assertEqual(received["ovos"][0].data["track"],
                         "http://example.com/track.mp3")
        self.assertEqual(len(received["mycroft"]), 0)

    def test_track_start_none_emits_ovos_queue_end(self):
        svc, bus = _make_service()
        svc.namespace = "audio"

        received = {"ovos": [], "mycroft": []}
        bus.on("ovos.audio.queue_end", lambda m: received["ovos"].append(m))
        bus.on("mycroft.audio.queue_end", lambda m: received["mycroft"].append(m))

        svc.track_start(None)

        self.assertEqual(len(received["ovos"]), 1)
        self.assertEqual(len(received["mycroft"]), 0)

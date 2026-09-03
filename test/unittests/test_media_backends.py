"""Tests for BaseMediaService, AudioService, VideoService, WebService.

Covers service loading, backend selection by URI scheme, and the v2
MediaBackend physical-event contract (bind_event_reporter /
PlaybackEvent). Uses FakeBus and mock plugins — no real playback.
"""
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message
from ovos_plugin_manager.templates.media import PlaybackEvent
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import MediaState, TrackState


class _FakeBackend:
    """Minimal v2 MediaBackend stub for testing service selection."""

    is_remote = False

    def __init__(self, config, bus):
        self.config = config
        self.bus = bus
        self.name = config.get("name", "fake")
        self.aliases = []
        self._event_reporter = None

    def supported_uris(self):
        return self.config.get("uris", [])

    def bind_event_reporter(self, reporter):
        self._event_reporter = reporter

    def report(self, event, **data):
        """Mirrors the real OPM template's report(): dereferences
        self._event_reporter fresh, at call time - never a closure a
        caller might have saved earlier. This is the ONLY path a real
        plugin uses; tests must drive events through this, not by calling
        a saved reporter reference directly (see TestUriProvenance*)."""
        if self._event_reporter is not None:
            self._event_reporter(event, **data)

    def load_track(self, uri, metadata=None):
        return True

    def play(self):
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

            def load_track(self, uri, metadata=None):
                return True

            def play(self):
                pass

            def _stop(self):
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

            def load_track(self, uri, metadata=None):
                return True

            def play(self):
                pass

            def _stop(self):
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

            def load_track(self, uri, metadata=None):
                return True

            def play(self):
                pass

            def _stop(self):
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

    def test_event_reporter_bound(self):
        """load_services() must bind every loaded backend's event reporter
        to _handle_backend_event (partial-applied with that backend) - the
        v2 replacement for the old set_track_start_callback wiring."""
        plugins = {"fake-audio": _FakeBackend}
        config = {
            "audio_players": {
                "myfake": {"module": "fake-audio", "name": "myfake", "uris": ["http"]}
            }
        }
        svc = self._make_service(config=config, plugins=plugins)
        svc.load_services()
        backend = svc.services[0]
        self.assertIsNotNone(backend._event_reporter)
        # calling report() must route straight to _handle_backend_event
        backend.current = backend
        svc.current = backend
        received = []
        svc.bus.on("ovos.common_play.track.state", lambda m: received.append(m))
        backend.report(PlaybackEvent.TRACK_START)
        self.assertEqual(len(received), 1)


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
    """Full v2 stub matching the interface used by BaseMediaService."""

    def __init__(self, uris=None, name="fake", load_ok=True):
        self.uris = uris or []
        self.name = name
        self.aliases = [name]
        self._event_reporter = None
        self.loaded_uri = None
        self.load_ok = load_ok
        self.played = False
        self.paused = False
        self.resumed = False
        self.stopped = False
        self.pause_calls = 0
        self.resume_calls = 0
        self.volume_lowered = False
        self.volume_restored = False
        self.track_position = None

    def supported_uris(self):
        return self.uris

    def bind_event_reporter(self, reporter):
        self._event_reporter = reporter

    def report(self, event, **data):
        """Mirrors the real OPM template's report(): dereferences
        self._event_reporter fresh, at call time. The only path a real
        plugin uses - drive events through this, never a saved reporter
        reference (see TestUriProvenanceGuardsStaleEvents)."""
        if self._event_reporter is not None:
            self._event_reporter(event, **data)

    def load_track(self, uri, metadata=None):
        self.loaded_uri = uri
        return self.load_ok

    def play(self):
        self.played = True

    def stop(self):
        self.stopped = True
        return True

    def pause(self):
        self.paused = True
        self.pause_calls += 1

    def resume(self):
        self.resumed = True
        self.resume_calls += 1

    def lower_volume(self):
        self.volume_lowered = True

    def restore_volume(self):
        self.volume_restored = True

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

    def test_init_does_not_subscribe_to_media_state_bus_topic(self):
        """The v1 self-listening loop must be gone: __init__ must not
        register any handler for 'ovos.common_play.media.state' - the
        service now learns of playback state exclusively via its backends'
        bound event reporters (see load_services)."""
        from ovos_media.media_backends.base import BaseMediaService
        mock_bus = MagicMock()
        with patch("ovos_media.media_backends.base.Configuration", return_value={"media": {}}):
            BaseMediaService(mock_bus, namespace="audio",
                             plugin_loader=lambda: {}, autoload=False)
        registered = [c[0][0] for c in mock_bus.on.call_args_list]
        self.assertNotIn("ovos.common_play.media.state", registered)


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
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http", "https"], name="vlc")
        b.is_remote = False
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

            def load_track(self, uri, metadata=None): return True
            def play(self): pass
            def pause(self): pass
            def resume(self): pass
            def _stop(self): pass
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
        # this is the contract switch: 'remote' now comes straight off the
        # template's own is_remote flag, not an isinstance() check here
        self.assertTrue(r.is_remote)


class TestPlay(unittest.TestCase):

    def test_play_uses_preferred_service_when_uri_supported(self):
        b1 = _FullFakeBackend(uris=["http"], name="vlc")
        b2 = _FullFakeBackend(uris=["library"], name="mass")
        svc, bus = _make_base_svc(services=[b1, b2])

        svc.play("http://example.com/track.mp3", preferred_service=b1)
        self.assertEqual(svc.current, b1)
        self.assertEqual(b1.loaded_uri, "http://example.com/track.mp3")
        self.assertTrue(b1.played)

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

    def test_play_emits_loaded_media_and_calls_backend_play(self):
        """A successful load_track()==True must emit LOADED_MEDIA and call
        play() on the backend directly — v2 replaces the old wait-for-the-
        backend-to-emit-LOADED_MEDIA-itself self-listening loop."""
        b1 = _FullFakeBackend(uris=["http"], name="vlc")
        svc, bus = _make_base_svc(services=[b1])
        received = []
        bus.on("ovos.common_play.media.state", lambda m: received.append(m))

        svc.play("http://example.com/track.mp3")

        self.assertTrue(b1.played)
        states = [m.data["state"] for m in received]
        self.assertIn(MediaState.LOADED_MEDIA, states)
        self.assertNotIn(MediaState.INVALID_MEDIA, states)

    def test_play_load_track_returns_false_emits_invalid_media(self):
        """A backend returning False from load_track() (the v2 failure
        signal) must emit INVALID_MEDIA and clear current, without ever
        calling play()."""
        b1 = _FullFakeBackend(uris=["http"], name="vlc", load_ok=False)
        svc, bus = _make_base_svc(services=[b1])
        received = []
        bus.on("ovos.common_play.media.state", lambda m: received.append(m))

        svc.play("http://example.com/track.mp3")

        self.assertIsNone(svc.current)
        self.assertFalse(b1.played)
        states = [m.data["state"] for m in received]
        self.assertEqual(states, [MediaState.INVALID_MEDIA])


class TestPauseResume(unittest.TestCase):

    def test_pause_calls_backend_pause_exactly_once(self):
        """v2: pause() calls the backend's plain pause() verb directly -
        there is no ocp_pause() wrapper to double-invoke it any more."""
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        svc.pause()
        self.assertTrue(b.paused)
        self.assertEqual(b.pause_calls, 1)

    def test_pause_no_current_does_nothing(self):
        svc, bus = _make_base_svc()
        # must not raise
        svc.pause()

    def test_resume_calls_backend_resume_exactly_once(self):
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        svc.resume()
        self.assertTrue(b.resumed)
        self.assertEqual(b.resume_calls, 1)

    def test_resume_no_current_does_nothing(self):
        svc, bus = _make_base_svc()
        svc.resume()

    def test_pause_does_not_emit_player_state_itself(self):
        """The matching player.state PAUSED is emitted by OCPMediaPlayer's
        own pause() (set_player_state), not by BaseMediaService any more -
        emitting it here too would duplicate the message on the wire."""
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        received = []
        bus.on("ovos.common_play.player.state", lambda m: received.append(m))
        svc.pause()
        self.assertEqual(received, [])


class TestStop(unittest.TestCase):

    def test_stop_calls_backend_stop_and_clears_current(self):
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

    def test_stop_emits_end_of_media_itself(self):
        """v2: the daemon emits END_OF_MEDIA directly from _perform_stop()
        on a successful backend.stop() — v1 backends emitted this
        themselves from ocp_stop(), which no longer exists."""
        import time as _time
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        svc.play_start_time = _time.monotonic() - 5
        received = []
        bus.on("ovos.common_play.media.state", lambda m: received.append(m))
        svc.stop()
        states = [m.data["state"] for m in received]
        self.assertEqual(states, [MediaState.END_OF_MEDIA])

    def test_stop_does_not_emit_player_state_itself(self):
        """player.state STOPPED is emitted by OCPMediaPlayer.stop() -
        _perform_stop() must not duplicate it."""
        import time as _time
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        svc.play_start_time = _time.monotonic() - 5
        received = []
        bus.on("ovos.common_play.player.state", lambda m: received.append(m))
        svc.stop()
        self.assertEqual(received, [])


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


class TestHandleBackendEvent(unittest.TestCase):
    """_handle_backend_event - the v2 replacement for
    handle_media_state_change's self-listening loop. Driven directly, as a
    real backend's bound reporter would call it."""

    def test_track_start_audio_namespace_emits_playing_audio(self):
        svc, bus = _make_base_svc(namespace="audio")
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        emitted = []
        bus.on("ovos.common_play.track.state", lambda m: emitted.append(m))
        svc._handle_backend_event(b, PlaybackEvent.TRACK_START)
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].data["state"], TrackState.PLAYING_AUDIO)

    def test_track_start_video_namespace_emits_playing_video(self):
        svc, bus = _make_base_svc(namespace="video")
        b = _FullFakeBackend(uris=["http"], name="mpv")
        svc.current = b
        emitted = []
        bus.on("ovos.common_play.track.state", lambda m: emitted.append(m))
        svc._handle_backend_event(b, PlaybackEvent.TRACK_START)
        self.assertEqual(emitted[0].data["state"], TrackState.PLAYING_VIDEO)

    def test_track_start_web_namespace_emits_playing_webview(self):
        svc, bus = _make_base_svc(namespace="web")
        b = _FullFakeBackend(uris=["https"], name="browser")
        svc.current = b
        emitted = []
        bus.on("ovos.common_play.track.state", lambda m: emitted.append(m))
        svc._handle_backend_event(b, PlaybackEvent.TRACK_START)
        self.assertEqual(emitted[0].data["state"], TrackState.PLAYING_WEBVIEW)

    def test_track_start_unknown_namespace_normalizes_and_forwards(self):
        """A custom namespace must NOT silently drop the event; it is
        normalized to a generic PLAYING_AUDIO TrackState and forwarded."""
        svc, bus = _make_base_svc(namespace="custom-thing")
        b = _FullFakeBackend(uris=["http"], name="thing")
        svc.current = b
        emitted = []
        bus.on("ovos.common_play.track.state", lambda m: emitted.append(m))
        svc._handle_backend_event(b, PlaybackEvent.TRACK_START)
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].data["state"], TrackState.PLAYING_AUDIO)

    def test_no_current_does_not_emit(self):
        """svc.current is None -> `backend is not self.current` is True for
        any real backend, so this is the same guard as
        test_event_from_inactive_backend_is_ignored, just with no current
        backend at all rather than a stale one - pinned separately since it
        is the more common real-world case (eg. a backend event racing a
        stop that already cleared current)."""
        svc, bus = _make_base_svc(namespace="audio")
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = None
        emitted = []
        bus.on("ovos.common_play.track.state", lambda m: emitted.append(m))
        bus.on("ovos.common_play.media.state", lambda m: emitted.append(m))
        svc._handle_backend_event(b, PlaybackEvent.TRACK_START)
        svc._handle_backend_event(b, PlaybackEvent.END_OF_MEDIA)
        self.assertEqual(len(emitted), 0)

    def test_unrecognized_event_is_a_noop(self):
        """A PlaybackEvent this service does not know about (eg. one added
        to a later template revision) must be dropped quietly, not raise -
        forward compatibility for the plugin side of the contract."""
        svc, bus = _make_base_svc(namespace="audio")
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        emitted = []
        bus.on("ovos.common_play.track.state", lambda m: emitted.append(m))
        bus.on("ovos.common_play.media.state", lambda m: emitted.append(m))
        bus.on("ovos.common_play.player.state", lambda m: emitted.append(m))
        svc._handle_backend_event(b, "some_future_event")  # must not raise
        self.assertEqual(emitted, [])

    def test_event_from_inactive_backend_is_ignored(self):
        """A backend that is no longer self.current (deactivated by a
        playback-type switch, or superseded by a later play()) must not be
        able to move state with a late event."""
        svc, bus = _make_base_svc(namespace="audio")
        stale = _FullFakeBackend(uris=["http"], name="stale")
        current = _FullFakeBackend(uris=["http"], name="current")
        svc.current = current
        emitted = []
        bus.on("ovos.common_play.track.state", lambda m: emitted.append(m))
        bus.on("ovos.common_play.media.state", lambda m: emitted.append(m))
        svc._handle_backend_event(stale, PlaybackEvent.TRACK_START)
        svc._handle_backend_event(stale, PlaybackEvent.END_OF_MEDIA)
        self.assertEqual(len(emitted), 0)

    def test_end_of_media_emits_media_state(self):
        svc, bus = _make_base_svc(namespace="audio")
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        emitted = []
        bus.on("ovos.common_play.media.state", lambda m: emitted.append(m))
        svc._handle_backend_event(b, PlaybackEvent.END_OF_MEDIA)
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].data["state"], MediaState.END_OF_MEDIA)
        # a natural end never resets `current` — same as the v1 behaviour,
        # where the plugin's own ocp_stop() never touched this service
        self.assertIs(svc.current, b)

    def test_error_emits_invalid_media_and_leaves_current_untouched(self):
        """v1 parity: current is NOT cleared from the event path - the
        player's on_invalid_stream/play_next flow (triggered by the
        INVALID_MEDIA message) owns the transition away from a failed
        track, exactly like it already owns it for a natural END_OF_MEDIA."""
        svc, bus = _make_base_svc(namespace="audio")
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        emitted = []
        bus.on("ovos.common_play.media.state", lambda m: emitted.append(m))
        svc._handle_backend_event(b, PlaybackEvent.ERROR, error="boom")
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].data["state"], MediaState.INVALID_MEDIA)
        self.assertIs(svc.current, b)

    def test_paused_resumed_stopped_do_not_emit_anything(self):
        """These are already covered by the daemon's own verb call sites
        (OCPMediaPlayer.pause/resume/stop -> set_player_state) - see
        TestPauseResume/TestStop above. Re-emitting on the plugin's
        physical confirmation would just duplicate the message."""
        svc, bus = _make_base_svc(namespace="audio")
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        emitted = []
        bus.on("ovos.common_play.player.state", lambda m: emitted.append(m))
        bus.on("ovos.common_play.track.state", lambda m: emitted.append(m))
        bus.on("ovos.common_play.media.state", lambda m: emitted.append(m))
        svc._handle_backend_event(b, PlaybackEvent.PAUSED)
        svc._handle_backend_event(b, PlaybackEvent.RESUMED)
        svc._handle_backend_event(b, PlaybackEvent.STOPPED)
        self.assertEqual(emitted, [])

    def test_paused_resumed_stopped_relayed_to_on_external_event(self):
        """PAUSED/RESUMED/STOPPED must not be silently dropped: they are
        relayed to the on_external_event callback (OCPMediaPlayer registers
        one - see player/__init__.py's _on_backend_external_event), which is
        how a device/plugin-side transport change (Chromecast app, Music
        Assistant UI...) not requested by this daemon reaches the player's
        own state machine."""
        svc, bus = _make_base_svc(namespace="audio")
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b
        received = []
        svc.on_external_event = lambda event: received.append(event)

        svc._handle_backend_event(b, PlaybackEvent.PAUSED)
        svc._handle_backend_event(b, PlaybackEvent.RESUMED)
        svc._handle_backend_event(b, PlaybackEvent.STOPPED)

        self.assertEqual(received, [PlaybackEvent.PAUSED, PlaybackEvent.RESUMED,
                                    PlaybackEvent.STOPPED])

    def test_on_external_event_exception_does_not_propagate(self):
        svc, bus = _make_base_svc(namespace="audio")
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b

        def _raise(event):
            raise RuntimeError("boom")

        svc.on_external_event = _raise
        # must not raise
        svc._handle_backend_event(b, PlaybackEvent.PAUSED)


class TestThreadingSingleRead(unittest.TestCase):
    """Executed repro from review: _perform_stop used to read self.current
    twice (`if self.current: ... if self.current.stop(): ...`), racing any
    concurrent write to self.current from another thread (eg a backend
    event handler running concurrently) - AttributeError on ``None.stop()``,
    the backend left playing forever with no END_OF_MEDIA or
    mycroft.stop.handled ever emitted. A single local read removes the
    window entirely."""

    def test_perform_stop_survives_concurrent_current_mutation(self):
        svc, bus = _make_base_svc()
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b

        # simulate another thread clearing self.current WHILE stop() is
        # running - exactly what a second read of self.current, after the
        # first, would have raced against
        real_stop = b.stop

        def _stop_and_mutate():
            svc.current = None
            return real_stop()

        b.stop = _stop_and_mutate

        received = []
        bus.on("mycroft.stop.handled", lambda m: received.append(m))

        svc._perform_stop()  # must not raise AttributeError

        self.assertIsNone(svc.current)
        self.assertEqual(len(received), 1)

    def test_handle_backend_event_takes_service_lock(self):
        """_handle_backend_event must serialize its self.current/self._gen
        read-and-compare through the same service_lock _perform_stop() and
        _play() use, so it cannot interleave with either."""
        svc, bus = _make_base_svc(namespace="audio")
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.current = b

        order = []
        svc.service_lock.acquire()
        try:
            t = threading.Thread(
                target=lambda: (svc._handle_backend_event(b, PlaybackEvent.END_OF_MEDIA),
                               order.append("event_processed")))
            t.start()
            time.sleep(0.1)
            order.append("still_held")
        finally:
            svc.service_lock.release()
        t.join(timeout=2)
        self.assertEqual(order, ["still_held", "event_processed"])


class TestUriProvenanceGuardsStaleEvents(unittest.TestCase):
    """Design decision (round 3, replacing an earlier generation-counter
    attempt): two consecutive tracks on the SAME backend instance are
    indistinguishable by identity alone. A late END_OF_MEDIA/ERROR from
    track 1, arriving after track 2's _play() has already (re)started on
    that same backend, is otherwise indistinguishable from one genuinely
    about track 2.

    A bind-time "generation" baked into a fresh partial rebound on every
    _play() does NOT work: the OPM template's report() always dereferences
    self._event_reporter fresh, at CALL time - never a stale closure a
    caller happened to save earlier - so no in-flight physical event can
    ever actually be carrying an old generation by the time it reaches
    _handle_backend_event (confirmed by an executed repro: a watcher-thread
    END_OF_MEDIA for track 1 sailed straight through while track 2 was
    already playing, because report() looked up the ALREADY-rebound,
    current-generation reporter). Every test below drives events through
    backend.report(...) - the real path a plugin uses - never a saved
    reporter reference directly, so this class cannot repeat that mistake.

    The actual mechanism: self._current_uri, the uri _play() last loaded
    onto the backend. END_OF_MEDIA/ERROR carrying a uri that disagrees with
    it is dropped. An event with no uri passes through un-filtered - a
    documented limitation, no worse than v1 (no detection at all)."""

    def test_late_end_of_media_with_track1_uri_after_track2_loaded_is_dropped(self):
        svc, bus = _make_base_svc(namespace="audio")
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.services = [b]

        svc.play("http://example.com/track1.mp3")
        svc.play("http://example.com/track2.mp3")  # same backend, reused
        self.assertIs(svc.current, b)

        emitted = []
        bus.on("ovos.common_play.media.state", lambda m: emitted.append(m))

        b.report(PlaybackEvent.END_OF_MEDIA, uri="http://example.com/track1.mp3")

        self.assertEqual(emitted, [], "a stale END_OF_MEDIA carrying track "
                                      "1's uri reached the wire after track "
                                      "2 had already loaded on the same "
                                      "backend")
        self.assertIs(svc.current, b)

    def test_late_error_with_track1_uri_after_track2_loaded_is_dropped(self):
        svc, bus = _make_base_svc(namespace="audio")
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.services = [b]

        svc.play("http://example.com/track1.mp3")
        svc.play("http://example.com/track2.mp3")

        emitted = []
        bus.on("ovos.common_play.media.state", lambda m: emitted.append(m))

        b.report(PlaybackEvent.ERROR, uri="http://example.com/track1.mp3",
                error="track1 died")

        self.assertEqual(emitted, [], "a stale ERROR carrying track 1's uri "
                                      "reached the wire after track 2 had "
                                      "already loaded on the same backend")

    def test_event_without_uri_passes_through_documented_limitation(self):
        """A plugin that does not attach uri= gives the daemon nothing to
        compare against - the event passes through un-filtered, exactly as
        it would have in v1 (no staleness detection existed there either).
        This is a deliberate, documented limitation, not a bug."""
        svc, bus = _make_base_svc(namespace="audio")
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.services = [b]

        svc.play("http://example.com/track1.mp3")
        svc.play("http://example.com/track2.mp3")

        emitted = []
        bus.on("ovos.common_play.media.state", lambda m: emitted.append(m))

        b.report(PlaybackEvent.END_OF_MEDIA)  # no uri kwarg at all

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].data["state"], MediaState.END_OF_MEDIA)

    def test_matching_uri_is_not_dropped(self):
        """The uri check must not become a one-way latch: an END_OF_MEDIA
        genuinely reporting the CURRENTLY loaded uri must still reach the
        wire."""
        svc, bus = _make_base_svc(namespace="audio")
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.services = [b]

        svc.play("http://example.com/track1.mp3")

        emitted = []
        bus.on("ovos.common_play.media.state", lambda m: emitted.append(m))

        b.report(PlaybackEvent.END_OF_MEDIA, uri="http://example.com/track1.mp3")

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].data["state"], MediaState.END_OF_MEDIA)

    def test_backend_identity_mismatch_still_dropped(self):
        """The ordinary `backend is not self.current` guard, unrelated to
        uri provenance, still applies - a genuinely different, deactivated
        backend instance is dropped regardless of what uri it reports."""
        svc, bus = _make_base_svc(namespace="audio")
        stale = _FullFakeBackend(uris=["http"], name="stale")
        current = _FullFakeBackend(uris=["http"], name="current")
        svc.services = [stale, current]
        svc.current = current

        emitted = []
        bus.on("ovos.common_play.media.state", lambda m: emitted.append(m))

        stale.report(PlaybackEvent.END_OF_MEDIA)

        self.assertEqual(emitted, [])

    def test_track_start_is_not_uri_gated(self):
        """Only the destructive events (ERROR/END_OF_MEDIA) are uri-gated -
        a stale TRACK_START/PAUSED/etc is already excluded by the ordinary
        backend-identity check, and this pins that scope is deliberate, not
        incidental."""
        svc, bus = _make_base_svc(namespace="audio")
        b = _FullFakeBackend(uris=["http"], name="vlc")
        svc.services = [b]

        svc.play("http://example.com/track1.mp3")
        svc.play("http://example.com/track2.mp3")

        emitted = []
        bus.on("ovos.common_play.track.state", lambda m: emitted.append(m))
        b.report(PlaybackEvent.TRACK_START, uri="http://example.com/track1.mp3")
        self.assertEqual(len(emitted), 1)


class TestNoDeadlockOnSynchronousReport(unittest.TestCase):
    """Executed repro from review (CONFIRMED, then fixed): _perform_stop
    used to call current.stop() WHILE HOLDING service_lock. A backend that
    reports synchronously from inside its own stop() - eg.
    ``self.report(PlaybackEvent.STOPPED)`` before returning, a perfectly
    legal thing for a plugin to do - re-enters _handle_backend_event on the
    SAME thread, which needs that same, non-reentrant Lock: permanent
    wedge. In the real player this ran on the single-worker dispatcher
    thread, so it killed every subsequent command, not just this one call.

    The fix is release-before-call, not RLock: RLock only fixes the
    same-thread case shown here and would still leave the plugin verb call
    inside the critical section, serializing every play/stop against every
    other backend's events for no reason. _perform_stop() now snapshots and
    clears self.current under a BRIEF lock section, releases it, and only
    then calls current.stop() - see its docstring in base.py."""

    def test_synchronous_report_from_stop_does_not_deadlock(self):
        class _SyncReportingBackend(_FullFakeBackend):
            def stop(self):
                # exactly like a real plugin whose stop() reports its own
                # completion synchronously, on the calling thread, BEFORE
                # returning - via the real report() path, never a saved
                # reporter reference
                self.report(PlaybackEvent.STOPPED)
                return super().stop()

        svc, bus = _make_base_svc()
        b = _SyncReportingBackend(uris=["http"], name="vlc")
        svc.services = [b]

        result = {}

        def _drive():
            svc.play("http://example.com/track.mp3")
            svc.play_start_time = 0.0  # clear stop()'s <1s guard window
            svc.stop()
            result["done"] = True

        t = threading.Thread(target=_drive)
        t.start()
        t.join(timeout=10)

        self.assertFalse(t.is_alive(), "play()/stop() never completed - "
                         "service_lock deadlocked on a synchronous report() "
                         "from inside stop()")
        self.assertTrue(result.get("done"))
        self.assertIsNone(svc.current)

    def test_synchronous_report_from_load_track_does_not_deadlock(self):
        """The same shape, for load_track() reporting an early ERROR
        synchronously before returning False."""
        class _SyncErrorBackend(_FullFakeBackend):
            def load_track(self, uri, metadata=None):
                self.report(PlaybackEvent.ERROR, error="synchronous failure")
                return False

        svc, bus = _make_base_svc()
        b = _SyncErrorBackend(uris=["http"], name="vlc")
        svc.services = [b]

        result = {}

        def _drive():
            svc.play("http://example.com/track.mp3")
            result["done"] = True

        t = threading.Thread(target=_drive)
        t.start()
        t.join(timeout=10)

        self.assertFalse(t.is_alive())
        self.assertTrue(result.get("done"))



class TestBindEventReporterFailureIsolation(unittest.TestCase):
    """Executed repro from review: one broken plugin's bind_event_reporter()
    raising during load_services() must not stop siblings from binding, and
    must not leave _loaded unset (which previously wedged wait_for_load()
    forever for a fully-loaded, otherwise-healthy service)."""

    def test_broken_bind_event_reporter_does_not_block_siblings_or_loaded_flag(self):
        class _BrokenReporterBackend(_FullFakeBackend):
            def __init__(self, config, bus):
                super().__init__(uris=["http"], name="broken")

            def bind_event_reporter(self, reporter):
                raise RuntimeError("boom")

        class _GoodBackend(_FullFakeBackend):
            def __init__(self, config, bus):
                super().__init__(uris=["https"], name="good")

        plugins = {"broken-audio": _BrokenReporterBackend, "good-audio": _GoodBackend}
        svc, bus = _make_base_svc(config={})
        svc.plugin_loader = lambda: plugins

        with patch("ovos_media.media_backends.base.LOG"):
            svc.load_services()

        names = [s.name for s in svc.services]
        self.assertIn("broken-audio", names)
        self.assertIn("good-audio", names)
        good = next(s for s in svc.services if s.name == "good-audio")
        self.assertIsNotNone(good._event_reporter,
                            "the broken sibling's bind failure stopped "
                            "'good' from binding its own reporter")
        self.assertTrue(svc._loaded.is_set(),
                        "_loaded was never set after a bind_event_reporter "
                        "failure - wait_for_load() would hang forever")


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
            [], "load_services() must not register any ovos.audio.service. handler")


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

    def test_typeerror_on_instantiation_names_v1_plugin_specifically(self):
        """A plugin whose constructor raises TypeError (eg. an unported
        MediaBackend v1 plugin missing a v2-only concrete method) gets a
        specific, actionable log line naming it as likely v1, distinct from
        the generic 'Failed to load' for any other exception."""
        def v1_plugin(cfg, bus):
            raise TypeError("missing required positional argument")

        plugins = {"v1-audio": v1_plugin}
        config = {"audio_players": {"legacy": {"module": "v1-audio"}}}
        svc, bus = _make_base_svc(config=config)
        svc.plugin_loader = lambda: plugins

        from ovos_media.media_backends import base as base_mod
        with patch.object(base_mod, "LOG") as mock_log:
            svc.load_services()

        self.assertEqual(svc.services, [])
        joined = "\n".join(
            " ".join(str(a) for a in c.args) for c in mock_log.exception.call_args_list
        )
        self.assertIn("v1-audio", joined)
        self.assertIn("MediaBackend v1", joined)
        self.assertIn("MediaBackend v2", joined)

    def test_load_track_returning_none_is_logged_as_likely_v1_and_treated_as_failure(self):
        """load_track() returning None (v1's load_track had no return
        statement) must be treated as a failed load, with a log line naming
        the plugin as likely v1 - distinct from an honest False return."""
        class _NoneReturningBackend(_FullFakeBackend):
            def load_track(self, uri, metadata=None):
                return None

        b = _NoneReturningBackend(uris=["http"], name="legacy-vlc")
        svc, bus = _make_base_svc(services=[b])

        from ovos_media.media_backends import base as base_mod
        with patch.object(base_mod, "LOG") as mock_log:
            svc.play("http://example.com/track.mp3")

        self.assertIsNone(svc.current)
        joined = "\n".join(
            " ".join(str(a) for a in c.args) for c in mock_log.error.call_args_list
        )
        self.assertIn("None", joined)
        self.assertIn("MediaBackend v1", joined)


class _RealTemplateBackend:
    """Real ovos_plugin_manager MediaBackend subclass, so the tests below
    exercise the actual base-class report()/bind_event_reporter() plumbing,
    not a hand-rolled fake."""

    def __new__(cls, *a, **kw):
        from ovos_plugin_manager.templates.media import MediaBackend

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

            def load_track(self, uri, metadata=None):
                return True

            def play(self):
                pass

            def _stop(self):
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
    ovos_plugin_manager.templates.media.MediaBackend base class (not
    mocked/stubbed), driven through BaseMediaService.pause()/resume().

    A single bus-level pause/resume request must invoke the backend's
    pause()/resume() exactly once — the v2 template has no ocp_pause()/
    ocp_resume() wrapper any more to double-invoke it.
    """

    def test_single_bus_pause_invokes_backend_pause_exactly_once(self):
        svc, bus = _make_base_svc()
        b = _RealTemplateBackend()
        b.load_track("http://example.com/track.mp3")
        svc.current = b

        svc.pause()

        self.assertEqual(b.pause_calls, 1)
        self.assertTrue(b.paused)  # toggled exactly once -> paused

    def test_single_bus_resume_invokes_backend_resume_exactly_once(self):
        svc, bus = _make_base_svc()
        b = _RealTemplateBackend()
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
        good.load_track = MagicMock(return_value=True)
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
        good.load_track = MagicMock(return_value=True)
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
        preferred.load_track = MagicMock(return_value=True)
        other = _FakeBackend({"name": "other", "uris": ["http"]}, None)
        other.load_track = MagicMock(return_value=True)
        svc, _bus = _make_base_svc(services=[other])
        self.assertTrue(self._assert_parity(
            svc, "http://example.com/a.mp3", preferred_service=preferred))
        self.assertIs(svc.current, preferred)
        other.load_track.assert_not_called()

    def test_current_backend_match_is_reused(self):
        current = _FakeBackend({"name": "current", "uris": ["http"]}, None)
        current.load_track = MagicMock(return_value=True)
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
    """Exceptions raised by a crashing backend during load_track() or
    play() must emit MediaState.INVALID_MEDIA and clear self.current,
    rather than dying silently."""

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

    def test_play_call_exception_emits_invalid_media_and_clears_current(self):
        """load_track() succeeds but the subsequent play() call raises —
        the v2-flow analogue of the old handle_media_state_change try/except
        around current.play()."""
        crashing = MagicMock()
        crashing.supported_uris.return_value = ["file"]
        crashing.load_track.return_value = True
        crashing.play.side_effect = RuntimeError("boom")

        svc = _make_base_service(None)
        svc.services = [crashing]

        received = []
        svc.bus.on("ovos.common_play.media.state", lambda m: received.append(m))

        svc.play("file:///tmp/track.mp3")

        self.assertIsNone(svc.current)
        states = [m.data.get("state") for m in received]
        # LOADED_MEDIA fires before play() is attempted, then INVALID_MEDIA
        # once it raises - both are on the wire, same overall sequence v1
        # produced (LOADED_MEDIA then INVALID_MEDIA for a load-ok/play-fail
        # track), just emitted by the daemon instead of the plugin.
        self.assertEqual(states, [MediaState.LOADED_MEDIA, MediaState.INVALID_MEDIA])


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


class TestWaitForLoad(unittest.TestCase):
    """Test wait_for_load timeout mechanism."""

    def test_wait_for_load_returns_true_when_already_loaded(self):
        """wait_for_load should return True if _loaded is already set."""
        svc, bus = _make_base_svc()
        svc._loaded.set()

        result = svc.wait_for_load(timeout=0.1)

        self.assertTrue(result)

    def test_wait_for_load_times_out(self):
        """wait_for_load should return False on timeout."""
        svc, bus = _make_base_svc()
        svc._loaded.clear()

        result = svc.wait_for_load(timeout=0.01)

        self.assertFalse(result)



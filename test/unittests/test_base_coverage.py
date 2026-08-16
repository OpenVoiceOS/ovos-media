"""Coverage tests for ovos_media/media_backends/base.py.

Targets uncovered handler methods and state transitions.
"""
import threading
import unittest
from unittest.mock import MagicMock, patch, call

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import MediaState, TrackState


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
        emitting the PAUSED TrackState), so calling both here would invoke
        the backend's pause() twice per bus-level pause request — the
        original defect."""
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


if __name__ == "__main__":
    unittest.main()

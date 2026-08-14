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
    svc.validate_source = False
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

        svc.pause(Message("x"))

        svc.current.pause.assert_not_called()
        svc.current.ocp_pause.assert_called_once()

    def test_pause_with_no_current_does_nothing(self):
        """pause() with no current should not raise."""
        svc, bus = _make_service()
        svc.current = None

        svc.pause(Message("x"))  # should not raise


class TestResumeWithCurrent(unittest.TestCase):
    """Test resume with current service."""

    def test_resume_calls_ocp_resume_only(self):
        """resume() must invoke current.ocp_resume() exactly once, and must
        NOT call current.resume() directly (symmetric to the pause case —
        the real template's ocp_resume() already calls resume() once)."""
        svc, bus = _make_service()
        svc.current = MagicMock()

        svc.resume(Message("x"))

        svc.current.resume.assert_not_called()
        svc.current.ocp_resume.assert_called_once()

    def test_resume_with_no_current_does_nothing(self):
        """resume() with no current should not raise."""
        svc, bus = _make_service()
        svc.current = None

        svc.resume(Message("x"))  # should not raise


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

        msg = Message("mycroft.stop")
        svc._perform_stop(msg)

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
            svc.stop(Message("mycroft.stop"))

        # Should not call _perform_stop because < 1 second elapsed
        mock_perform.assert_not_called()


class TestLowerVolumeWithCurrent(unittest.TestCase):
    """Test lower_volume."""

    def test_lower_volume_calls_current_and_sets_flag(self):
        """lower_volume() should call current.lower_volume() and set volume_is_low."""
        svc, bus = _make_service()
        svc.current = MagicMock()
        svc.volume_is_low = False

        svc.lower_volume(Message("x"))

        svc.current.lower_volume.assert_called_once()
        self.assertTrue(svc.volume_is_low)

    def test_lower_volume_with_no_current_does_nothing(self):
        """lower_volume() with no current should not raise."""
        svc, bus = _make_service()
        svc.current = None

        svc.lower_volume(Message("x"))  # should not raise

    def test_lower_volume_when_already_low_skips(self):
        """lower_volume() should skip if volume_is_low is already True."""
        svc, bus = _make_service()
        svc.current = MagicMock()
        svc.volume_is_low = True

        svc.lower_volume(Message("x"))

        svc.current.lower_volume.assert_not_called()


class TestRestoreVolumeWithCurrent(unittest.TestCase):
    """Test restore_volume."""

    def test_restore_volume_calls_current_when_low(self):
        """restore_volume() should call current.restore_volume() when volume_is_low."""
        svc, bus = _make_service()
        svc.current = MagicMock()
        svc.volume_is_low = True

        svc.restore_volume(Message("x"))

        svc.current.restore_volume.assert_called_once()
        self.assertFalse(svc.volume_is_low)

    def test_restore_volume_with_no_current_does_nothing(self):
        """restore_volume() with no current should not raise."""
        svc, bus = _make_service()
        svc.current = None

        svc.restore_volume(Message("x"))  # should not raise

    def test_restore_volume_when_not_low_skips(self):
        """restore_volume() should skip if volume_is_low is False."""
        svc, bus = _make_service()
        svc.current = MagicMock()
        svc.volume_is_low = False

        svc.restore_volume(Message("x"))

        svc.current.restore_volume.assert_not_called()


class TestHandleTrackInfo(unittest.TestCase):
    """Test handle_track_info."""

    def test_handle_track_info_with_current(self):
        """handle_track_info should emit response with current track info."""
        svc, bus = _make_service()
        svc.current = MagicMock()
        svc.current.track_info.return_value = {"title": "Test", "artist": "Artist"}

        received = []
        bus.on("ovos.common_play.track_info.response", lambda m: received.append(m))

        svc.handle_track_info(Message("ovos.common_play.track_info"))

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].data["title"], "Test")

    def test_handle_track_info_without_current(self):
        """handle_track_info without current should emit empty response."""
        svc, bus = _make_service()
        svc.current = None

        received = []
        bus.on("ovos.common_play.track_info.response", lambda m: received.append(m))

        svc.handle_track_info(Message("ovos.common_play.track_info"))

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].data, {})


class TestHandleListBackends(unittest.TestCase):
    """Test handle_list_backends."""

    def test_handle_list_backends_emits_response(self):
        """handle_list_backends should emit response with available backends."""
        svc, bus = _make_service()

        with patch.object(svc, "available_backends", return_value={"vlc": {}}):
            received = []
            bus.on("ovos.common_play.list_backends.response", lambda m: received.append(m))

            svc.handle_list_backends(Message("ovos.common_play.list_backends"))

            self.assertEqual(len(received), 1)
            self.assertIn("vlc", received[0].data)


class TestHandleGetTrackLength(unittest.TestCase):
    """Test handle_get_track_length."""

    def test_get_track_length_with_current(self):
        """handle_get_track_length should emit current.get_track_length()."""
        svc, bus = _make_service()
        svc.current = MagicMock()
        svc.current.get_track_length.return_value = 180000

        received = []
        bus.on("ovos.common_play.get_track_length.response", lambda m: received.append(m))

        svc.handle_get_track_length(Message("ovos.common_play.get_track_length"))

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].data["length"], 180000)

    def test_get_track_length_without_current(self):
        """handle_get_track_length without current should emit None."""
        svc, bus = _make_service()
        svc.current = None

        received = []
        bus.on("ovos.common_play.get_track_length.response", lambda m: received.append(m))

        svc.handle_get_track_length(Message("ovos.common_play.get_track_length"))

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].data["length"], None)


class TestHandleGetTrackPosition(unittest.TestCase):
    """Test handle_get_track_position."""

    def test_get_track_position_with_current(self):
        """handle_get_track_position should emit current.get_track_position()."""
        svc, bus = _make_service()
        svc.current = MagicMock()
        svc.current.get_track_position.return_value = 45000

        received = []
        bus.on("ovos.common_play.get_track_position.response", lambda m: received.append(m))

        svc.handle_get_track_position(Message("ovos.common_play.get_track_position"))

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].data["position"], 45000)

    def test_get_track_position_without_current(self):
        """handle_get_track_position without current should emit None."""
        svc, bus = _make_service()
        svc.current = None

        received = []
        bus.on("ovos.common_play.get_track_position.response", lambda m: received.append(m))

        svc.handle_get_track_position(Message("ovos.common_play.get_track_position"))

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].data["position"], None)


class TestHandleSetTrackPosition(unittest.TestCase):
    """Test handle_set_track_position."""

    def test_set_track_position_with_current(self):
        """handle_set_track_position should call current.set_track_position()."""
        svc, bus = _make_service()
        svc.current = MagicMock()

        svc.handle_set_track_position(Message("ovos.common_play.set_track_position",
                                             {"position": 90000}))

        svc.current.set_track_position.assert_called_with(90000)

    def test_set_track_position_without_current_does_nothing(self):
        """handle_set_track_position without current should not raise."""
        svc, bus = _make_service()
        svc.current = None

        svc.handle_set_track_position(Message("ovos.common_play.set_track_position",
                                             {"position": 90000}))


class TestHandleSeekForward(unittest.TestCase):
    """Test handle_seek_forward."""

    def test_seek_forward_with_current(self):
        """handle_seek_forward should call current.seek_forward()."""
        svc, bus = _make_service()
        svc.current = MagicMock()

        svc.handle_seek_forward(Message("ovos.common_play.seek_forward",
                                       {"seconds": 10}))

        svc.current.seek_forward.assert_called_with(10)


class TestHandleSeekBackward(unittest.TestCase):
    """Test handle_seek_backward."""

    def test_seek_backward_with_current(self):
        """handle_seek_backward should call current.seek_backward()."""
        svc, bus = _make_service()
        svc.current = MagicMock()

        svc.handle_seek_backward(Message("ovos.common_play.seek_backward",
                                        {"seconds": 10}))

        svc.current.seek_backward.assert_called_with(10)


class TestTrackStartLegacyTwins(unittest.TestCase):
    """track_start must emit mycroft.audio.* twins alongside ovos.audio.*
    so legacy skills blocking on mycroft.audio.playing_track /
    mycroft.audio.queue_end do not hang forever."""

    def test_track_start_emits_ovos_and_mycroft_playing_track(self):
        svc, bus = _make_service()
        svc.namespace = "audio"

        received = {"ovos": [], "mycroft": []}
        bus.on("ovos.audio.playing_track", lambda m: received["ovos"].append(m))
        bus.on("mycroft.audio.playing_track", lambda m: received["mycroft"].append(m))

        svc.track_start("http://example.com/track.mp3")

        self.assertEqual(len(received["ovos"]), 1)
        self.assertEqual(len(received["mycroft"]), 1)
        self.assertEqual(received["mycroft"][0].data["track"],
                         "http://example.com/track.mp3")

    def test_track_start_none_emits_ovos_and_mycroft_queue_end(self):
        svc, bus = _make_service()
        svc.namespace = "audio"

        received = {"ovos": [], "mycroft": []}
        bus.on("ovos.audio.queue_end", lambda m: received["ovos"].append(m))
        bus.on("mycroft.audio.queue_end", lambda m: received["mycroft"].append(m))

        svc.track_start(None)

        self.assertEqual(len(received["ovos"]), 1)
        self.assertEqual(len(received["mycroft"]), 1)

    def test_track_start_video_namespace_does_not_emit_mycroft_twin(self):
        """mycroft.audio.* is audio-specific legacy — video/web must not
        emit it (there is no mycroft.video.*/mycroft.web.* legacy type)."""
        svc, bus = _make_service()
        svc.namespace = "video"

        received = []
        bus.on("mycroft.audio.playing_track", lambda m: received.append(m))

        svc.track_start("http://example.com/movie.mp4")

        self.assertEqual(len(received), 0)

    def test_track_start_video_namespace_does_not_emit_mycroft_queue_end(self):
        svc, bus = _make_service()
        svc.namespace = "video"

        received = []
        bus.on("mycroft.audio.queue_end", lambda m: received.append(m))

        svc.track_start(None)

        self.assertEqual(len(received), 0)


class TestHandlePlayUriExtraction(unittest.TestCase):
    """handle_play must pass a single uri string to self.play(), not the
    raw tracks list — self.play() does uri.split(':') and would raise
    AttributeError on a list, killing the Timer thread silently."""

    def _run_handle_play(self, svc, data):
        """Call handle_play with threading.Timer patched to fire synchronously
        so we can inspect what was passed to self.play()."""
        from ovos_bus_client.message import Message
        with patch("threading.Timer") as mock_timer:
            svc.handle_play(Message("ovos.audio.service.play", data))
            self.assertTrue(mock_timer.called)
            _args, kwargs = mock_timer.call_args
            # threading.Timer(0.5, self.play, args=[...])
            target = mock_timer.call_args[0][1]
            call_args = mock_timer.call_args[1].get("args") or mock_timer.call_args[0][2]
            return target, call_args

    def test_handle_play_extracts_uri_from_string_track(self):
        svc, bus = _make_service()
        svc.services = []

        target, call_args = self._run_handle_play(
            svc, {"tracks": ["http://example.com/a.mp3"]})

        # handle_play defers to _play so the tracklist it just queued
        # is not cleared by the public play() entry point.
        self.assertEqual(target, svc._play)
        self.assertEqual(call_args[0], "http://example.com/a.mp3")

    def test_handle_play_extracts_uri_from_tuple_track(self):
        svc, bus = _make_service()
        svc.services = []

        target, call_args = self._run_handle_play(
            svc, {"tracks": [("http://example.com/b.mp3", "audio/mpeg")]})

        self.assertEqual(call_args[0], "http://example.com/b.mp3")

    def test_handle_play_with_no_tracks_does_not_start_timer(self):
        svc, bus = _make_service()
        svc.services = []
        from ovos_bus_client.message import Message
        with patch("threading.Timer") as mock_timer:
            svc.handle_play(Message("ovos.audio.service.play", {"tracks": []}))
            mock_timer.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""Tests for GUI integration via GUIInterface.show_media_player().

These tests verify the _update_gui() contract on OCPMediaPlayer: that
show_media_player() is called with the correct state= value after play,
pause, stop, and related state changes.
"""
import unittest
from unittest.mock import MagicMock, patch, call

from ovos_utils.ocp import PlayerState, LoopState, MediaState, PlaybackType
from ovos_utils.fakebus import FakeBus


def _make_player():
    """Return a minimal OCPMediaPlayer with all deps mocked."""
    from ovos_media.player import OCPMediaPlayer
    with patch("ovos_media.player.AudioService"), \
         patch("ovos_media.player.VideoService"), \
         patch("ovos_media.player.WebService"), \
         patch("ovos_media.player.OcpMprisExporter"), \
         patch("ovos_media.player.GUIInterface"), \
         patch("ovos_media.player.Configuration", return_value={"media": {}}), \
         patch("ovos_media.player.OVOSAbstractApplication.__init__", return_value=None):
        p = OCPMediaPlayer.__new__(OCPMediaPlayer)
        p.ocp_config = {}
        p.state = PlayerState.STOPPED
        p.loop_state = LoopState.NONE
        p.media_state = MediaState.NO_MEDIA
        p.shuffle = False
        p.track_history = {}
        p._paused_on_duck = False
        p._last_search_results = []
        p.now_playing = MagicMock()
        p.now_playing.uri = "http://example.com/track.mp3"
        p.playlist = MagicMock()
        p.playlist.as_list.return_value = []
        p.media = MagicMock()
        p.audio_service = MagicMock()
        p.video_service = MagicMock()
        p.web_service = MagicMock()
        p.current = None
        p.mpris = None
        p._bus = FakeBus()
        p.gui = MagicMock()
    return p


class TestUpdateGuiCallsShowMediaPlayer(unittest.TestCase):
    """_update_gui() must call gui.show_media_player() with the right state."""

    def test_update_gui_playing_state(self):
        """When player state is PLAYING, show_media_player receives state='playing'."""
        p = _make_player()
        p.state = PlayerState.PLAYING
        p._update_gui()
        p.gui.show_media_player.assert_called_once()
        kwargs = p.gui.show_media_player.call_args[1]
        self.assertEqual(kwargs["state"], "playing")

    def test_update_gui_paused_state(self):
        """When player state is PAUSED, show_media_player receives state='paused'."""
        p = _make_player()
        p.state = PlayerState.PAUSED
        p._update_gui()
        kwargs = p.gui.show_media_player.call_args[1]
        self.assertEqual(kwargs["state"], "paused")

    def test_update_gui_stopped_state(self):
        """When player state is STOPPED, show_media_player receives state='stopped'."""
        p = _make_player()
        p.state = PlayerState.STOPPED
        p._update_gui()
        kwargs = p.gui.show_media_player.call_args[1]
        self.assertEqual(kwargs["state"], "stopped")

    def test_update_gui_passes_playlist(self):
        """_update_gui() passes playlist from self.playlist.as_list()."""
        p = _make_player()
        p.playlist.as_list.return_value = [{"title": "Track A"}]
        p._update_gui()
        kwargs = p.gui.show_media_player.call_args[1]
        self.assertEqual(kwargs["playlist"], [{"title": "Track A"}])

    def test_update_gui_passes_search_results(self):
        """_update_gui() passes _last_search_results as search_results."""
        p = _make_player()
        p._last_search_results = [{"title": "Result 1"}]
        p._update_gui()
        kwargs = p.gui.show_media_player.call_args[1]
        self.assertEqual(kwargs["search_results"], [{"title": "Result 1"}])

    def test_update_gui_now_playing_with_uri(self):
        """_update_gui() calls now_playing.as_dict() when uri is set."""
        p = _make_player()
        p.now_playing.uri = "http://example.com/track.mp3"
        p.now_playing.as_dict.return_value = {"title": "Test Track"}
        p._update_gui()
        kwargs = p.gui.show_media_player.call_args[1]
        self.assertIsNotNone(kwargs["now_playing"])

    def test_update_gui_now_playing_none_when_no_uri(self):
        """_update_gui() passes now_playing=None when now_playing.uri is falsy."""
        p = _make_player()
        p.now_playing.uri = ""
        p._update_gui()
        kwargs = p.gui.show_media_player.call_args[1]
        self.assertIsNone(kwargs["now_playing"])


class TestGuiCalledAfterPlaybackCommands(unittest.TestCase):
    """show_media_player must be called after play, pause, resume, stop."""

    def test_pause_calls_update_gui(self):
        """pause() must call _update_gui() after setting PAUSED state."""
        p = _make_player()
        p.state = PlayerState.PLAYING
        p.playback_type = PlaybackType.AUDIO
        p.set_player_state = MagicMock()
        p._update_gui = MagicMock()
        p.pause()
        p._update_gui.assert_called()

    def test_stop_calls_update_gui(self):
        """stop() must call _update_gui() after setting STOPPED state."""
        p = _make_player()
        p.state = PlayerState.PLAYING
        p.playback_type = PlaybackType.AUDIO
        p.set_player_state = MagicMock()
        p._update_gui = MagicMock()
        p.stop()
        p._update_gui.assert_called()

    def test_resume_calls_update_gui(self):
        """resume() must call _update_gui() after setting PLAYING state."""
        p = _make_player()
        p.state = PlayerState.PAUSED
        p.playback_type = PlaybackType.AUDIO
        p.set_player_state = MagicMock()
        p._update_gui = MagicMock()
        p.resume()
        p._update_gui.assert_called()


class TestSearchStartShowsLoadingState(unittest.TestCase):
    """handle_search_start must call show_media_player with state='loading'."""

    def test_handle_search_start_loading(self):
        p = _make_player()
        msg = MagicMock()
        p.handle_search_start(msg)
        p.gui.show_media_player.assert_called_once_with(
            now_playing=None,
            playlist=[],
            search_results=[],
            state="loading",
        )


class TestInvalidMediaShowsErrorState(unittest.TestCase):
    """handle_invalid_media must call show_media_player with state='error'."""

    def test_handle_invalid_media_error(self):
        p = _make_player()
        msg = MagicMock()
        p.handle_invalid_media(msg)
        p.gui.show_media_player.assert_called_once()
        kwargs = p.gui.show_media_player.call_args[1]
        self.assertEqual(kwargs["state"], "error")
        self.assertIsNone(kwargs["now_playing"])


class TestShuffleRepeatCallsUpdateGui(unittest.TestCase):
    """Shuffle/repeat handlers must call _update_gui()."""

    def test_set_shuffle_calls_update_gui(self):
        p = _make_player()
        p._update_gui = MagicMock()
        p.handle_set_shuffle(MagicMock())
        p._update_gui.assert_called_once()

    def test_unset_shuffle_calls_update_gui(self):
        p = _make_player()
        p._update_gui = MagicMock()
        p.handle_unset_shuffle(MagicMock())
        p._update_gui.assert_called_once()

    def test_set_repeat_calls_update_gui(self):
        p = _make_player()
        p._update_gui = MagicMock()
        p.handle_set_repeat(MagicMock())
        p._update_gui.assert_called_once()

    def test_unset_repeat_calls_update_gui(self):
        p = _make_player()
        p._update_gui = MagicMock()
        p.handle_unset_repeat(MagicMock())
        p._update_gui.assert_called_once()


if __name__ == "__main__":
    unittest.main()

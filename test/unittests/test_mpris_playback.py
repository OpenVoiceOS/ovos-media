"""Tests for the virtual player's MPRIS paths.

When playback is external (PlaybackType.MPRIS) the transport verbs are
forwarded to the MPRIS facade instead of a local backend, and only while
external-player management is enabled. Local playback still updates the
exporter's properties.
"""
import unittest
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message
from ovos_utils.ocp import LoopState, PlaybackType

from player_fixture import make_player


class TestPlayerPlayWithMpris(unittest.TestCase):
    """Test play() with mpris enabled."""

    def test_play_updates_mpris_can_go_next(self):
        """play() should update mpris CanGoNext property."""
        p = make_player()
        p.mpris = MagicMock()

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        # Check that update_props was called with CanGoNext
        calls = p.mpris.update_props.call_args_list
        self.assertTrue(any("CanGoNext" in str(call) for call in calls))

    def test_play_stops_mpris_if_stop_event_not_set(self):
        """play() should stop mpris if stop_event is not set."""
        p = make_player()
        p.mpris = MagicMock()
        p.mpris.stop_event.is_set.return_value = False

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        p.mpris.stop.assert_called_once()


class TestPlayerPlayNextMpris(unittest.TestCase):
    """Test play_next with MPRIS playback."""

    def test_play_next_mpris_with_manage_players(self):
        """play_next with MPRIS and manage_players=True should call mpris.play_next()."""
        p = make_player(PlaybackType.MPRIS)
        p.mpris = MagicMock()
        p.mpris.manage_players = True

        p.play_next()

        p.mpris.play_next.assert_called_once()


class TestPlayerPlayPrevMpris(unittest.TestCase):
    """Test play_prev with MPRIS."""

    def test_play_prev_mpris_disabled_warns(self):
        """play_prev with MPRIS and manage_players=False should warn."""
        p = make_player(PlaybackType.MPRIS)
        p.mpris = MagicMock()
        p.mpris.manage_players = False

        p.play_prev()

        # Should not crash, mpris.play_prev should not be called
        p.mpris.play_prev.assert_not_called()


class TestPlayerPauseMpris(unittest.TestCase):
    """Test pause with MPRIS."""

    def test_pause_mpris_with_manage_players(self):
        """pause() with MPRIS and manage_players=True should call mpris.pause()."""
        p = make_player(PlaybackType.MPRIS)
        p.mpris = MagicMock()
        p.mpris.manage_players = True

        with patch.object(p, "set_player_state"):
            p.pause()

        p.mpris.pause.assert_called_once()


class TestPlayerResumeMpris(unittest.TestCase):
    """Test resume with MPRIS."""

    def test_resume_mpris_with_manage_players(self):
        """resume() with MPRIS and manage_players=True should call mpris.resume()."""
        p = make_player(PlaybackType.MPRIS)
        p.mpris = MagicMock()
        p.mpris.manage_players = True

        with patch.object(p, "set_player_state"):
            p.resume()

        p.mpris.resume.assert_called_once()


class TestPlayerStopMpris(unittest.TestCase):
    """Test stop with MPRIS."""

    def test_stop_mpris_calls_mpris_pause(self):
        """stop() with MPRIS playback should call mpris.pause()."""
        p = make_player(PlaybackType.MPRIS)
        p.mpris = MagicMock()

        with patch.object(p, "set_player_state"):
            p.stop()

        p.mpris.pause.assert_called_once()


class TestPlayerHandleRepeatToggleMpris(unittest.TestCase):
    """Test handle_repeat_toggle_request with MPRIS."""

    def test_repeat_toggle_mpris_calls_toggle_repeat(self):
        """handle_repeat_toggle_request with MPRIS should call mpris.toggle_repeat()."""
        p = make_player(PlaybackType.MPRIS)
        p.mpris = MagicMock()
        p.loop_state = LoopState.NONE

        with patch.object(p, "handle_status"):
            p.handle_repeat_toggle_request(Message("x"))

        p.mpris.toggle_repeat.assert_called_once()


class TestPlayerHandleShuffleMpris(unittest.TestCase):
    """Test handle_shuffle_toggle_request with MPRIS."""

    def test_shuffle_toggle_mpris_calls_toggle_shuffle(self):
        """handle_shuffle_toggle_request with MPRIS should call mpris.toggle_shuffle()."""
        p = make_player(PlaybackType.MPRIS)
        p.mpris = MagicMock()

        with patch.object(p, "handle_status"):
            p.handle_shuffle_toggle_request(Message("x"))

        p.mpris.toggle_shuffle.assert_called_once()

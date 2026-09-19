"""Tests for OCPMediaPlayer state machine correctness and ducking handlers.

Covers:
- set_player_state / set_media_state actually update self.state / self.media_state
- set_player_state emits the NEW state (not the old one)
- recognizer_loop:* events route to the correct cork/duck handlers
- mycroft.stop handler stops playback and emits mycroft.stop.handled
"""
import unittest
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message
from ovos_utils.ocp import (LoopState, MediaState, PlaybackType, PlayerState)

from player_fixture import make_player




class TestSetPlayerStateStateAssignment(unittest.TestCase):
    """set_player_state must update self.state to the NEW value."""

    def test_transitions_from_stopped_to_paused(self):
        p = make_player()
        p.handle_status = MagicMock()  # prevent JSON serialisation of MagicMock fields
        p.set_player_state(PlayerState.PAUSED)
        self.assertEqual(p.state, PlayerState.PAUSED)

    def test_transitions_from_stopped_to_playing(self):
        p = make_player()
        p.handle_status = MagicMock()
        p.set_player_state(PlayerState.PLAYING)
        self.assertEqual(p.state, PlayerState.PLAYING)

    def test_transitions_paused_to_stopped(self):
        p = make_player()
        p.handle_status = MagicMock()
        p.state = PlayerState.PAUSED
        p.set_player_state(PlayerState.STOPPED)
        self.assertEqual(p.state, PlayerState.STOPPED)

    def test_noop_when_same_state(self):
        p = make_player()
        p.handle_status = MagicMock()
        emitted = []
        p.bus.emit = lambda m: emitted.append(m)
        p.set_player_state(PlayerState.STOPPED)
        # no bus message should be emitted for a no-op transition
        player_state_msgs = [
            m for m in emitted
            if m.msg_type == "ovos.common_play.player.state"
        ]
        self.assertEqual(len(player_state_msgs), 0)


class TestSetPlayerStateEmitsNewState(unittest.TestCase):
    """set_player_state must emit the NEW state in the bus message."""

    def test_emitted_state_is_new_state(self):
        p = make_player()
        p.handle_status = MagicMock()
        emitted = []
        p.bus.emit = lambda m: emitted.append(m)
        p.set_player_state(PlayerState.PLAYING)
        player_state_msgs = [
            m for m in emitted
            if m.msg_type == "ovos.common_play.player.state"
        ]
        self.assertTrue(len(player_state_msgs) >= 1)
        self.assertEqual(player_state_msgs[0].data["state"], PlayerState.PLAYING)


class TestSetMediaStateStateAssignment(unittest.TestCase):
    """set_media_state must update self.media_state to the NEW value."""

    def test_updates_media_state(self):
        p = make_player()
        p.set_media_state(MediaState.BUFFERED_MEDIA)
        self.assertEqual(p.media_state, MediaState.BUFFERED_MEDIA)

    def test_emits_new_media_state(self):
        p = make_player()
        emitted = []
        p.bus.emit = lambda m: emitted.append(m)
        p.set_media_state(MediaState.LOADED_MEDIA)
        media_state_msgs = [
            m for m in emitted
            if m.msg_type == "ovos.common_play.media.state"
        ]
        self.assertTrue(len(media_state_msgs) >= 1)
        self.assertEqual(media_state_msgs[0].data["state"], MediaState.LOADED_MEDIA)


class TestRecognizerLoopDuckingHandlers(unittest.TestCase):
    """ovos.common_play.duck/unduck and recognizer_loop:record_* events must
    trigger correct cork/duck handlers."""

    def test_duck_calls_duck(self):
        """ovos.common_play.duck → handle_duck_request"""
        p = make_player()
        p.state = PlayerState.PLAYING
        p.now_playing.playback = PlaybackType.AUDIO
        # Invoke the handler directly (the event is registered via add_event,
        # which we can't easily test in isolation without a full skill setup)
        from ovos_bus_client.message import Message
        p.handle_duck_request(Message("ovos.common_play.duck"))
        p.audio_service.lower_volume.assert_called_once()
        self.assertTrue(p._paused_on_duck)

    def test_unduck_calls_unduck(self):
        """ovos.common_play.unduck → handle_unduck_request"""
        p = make_player()
        p.state = PlayerState.PAUSED
        p._paused_on_duck = True
        p.now_playing.playback = PlaybackType.AUDIO
        from ovos_bus_client.message import Message
        p.handle_unduck_request(Message("ovos.common_play.unduck"))
        p.audio_service.restore_volume.assert_called_once()
        self.assertFalse(p._paused_on_duck)

    def test_record_begin_calls_cork(self):
        """recognizer_loop:record_begin → handle_cork_request"""
        p = make_player()
        p.state = PlayerState.PLAYING
        cork_called = []
        original_pause = p.pause
        p.pause = lambda: cork_called.append(True)
        from ovos_bus_client.message import Message
        p.handle_cork_request(Message("recognizer_loop:record_begin"))
        self.assertTrue(len(cork_called) > 0)
        self.assertTrue(p._paused_on_duck)


class TestMycroftstopHandler(unittest.TestCase):
    """mycroft.stop must stop playback and emit mycroft.stop.handled."""

    def test_stop_emits_handled_when_playing(self):
        p = make_player()
        p.state = PlayerState.PLAYING
        p.stop = MagicMock()
        p.reset = MagicMock()

        emitted = []
        p.bus.emit = lambda m: emitted.append(m)

        from ovos_bus_client.message import Message
        p.handle_mycroft_stop(Message("mycroft.stop"))

        p.stop.assert_called_once()
        p.reset.assert_called_once()
        handled_msgs = [m for m in emitted if m.msg_type == "mycroft.stop.handled"]
        self.assertTrue(len(handled_msgs) > 0)

    def test_stop_noop_when_already_stopped(self):
        p = make_player()
        p.state = PlayerState.STOPPED
        p.stop = MagicMock()

        from ovos_bus_client.message import Message
        p.handle_mycroft_stop(Message("mycroft.stop"))

        p.stop.assert_not_called()


class TestOCPMediaPlayerReset(unittest.TestCase):
    """reset() clears playlist, media, and state flags."""

    def test_reset_calls_now_playing_reset(self):
        p = make_player()
        p.reset()
        p.now_playing.reset.assert_called_once()

    def test_reset_calls_playlist_clear(self):
        p = make_player()
        p.reset()
        p.playlist.clear.assert_called()

    def test_reset_clears_shuffle(self):
        p = make_player()
        p.shuffle = True
        p.reset()
        self.assertFalse(p.shuffle)

    def test_reset_clears_loop_state(self):
        p = make_player()
        p.loop_state = LoopState.REPEAT
        p.reset()
        self.assertEqual(p.loop_state, LoopState.NONE)

    def test_reset_sets_state_stopped(self):
        p = make_player()
        p.state = PlayerState.PLAYING
        p.reset()
        self.assertEqual(p.state, PlayerState.STOPPED)


class TestDuckUnduckCorkUncork(unittest.TestCase):
    """Audio ducking handler edge cases."""

    def test_duck_audio_calls_lower_volume(self):
        p = make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PLAYING
        p.handle_duck_request(Message("ovos.common_play.duck"))
        p.audio_service.lower_volume.assert_called_once()
        self.assertTrue(p._paused_on_duck)

    def test_duck_video_calls_lower_volume(self):
        p = make_player(PlaybackType.VIDEO)
        p.state = PlayerState.PLAYING
        p.handle_duck_request(Message("ovos.common_play.duck"))
        p.video_service.lower_volume.assert_called_once()

    def test_unduck_audio_calls_restore_volume(self):
        p = make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PAUSED
        p._paused_on_duck = True
        p.handle_unduck_request(Message("ovos.common_play.unduck"))
        p.audio_service.restore_volume.assert_called_once()
        self.assertFalse(p._paused_on_duck)

    def test_unduck_noop_when_not_paused_on_duck(self):
        p = make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PAUSED
        p._paused_on_duck = False
        p.handle_unduck_request(Message("ovos.common_play.unduck"))
        p.audio_service.restore_volume.assert_not_called()

    def test_cork_pauses_when_playing(self):
        p = make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()
        p.handle_cork_request(Message("recognizer_loop:record_begin"))
        self.assertEqual(p.state, PlayerState.PAUSED)
        self.assertTrue(p._paused_on_duck)

    def test_cork_noop_when_not_playing(self):
        p = make_player()
        p.state = PlayerState.PAUSED
        p.handle_cork_request(Message("recognizer_loop:record_begin"))
        # _paused_on_duck should NOT be toggled here since we weren't playing
        self.assertFalse(p._paused_on_duck)

    def test_uncork_resumes_when_paused_on_duck(self):
        p = make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PAUSED
        p._paused_on_duck = True
        p.handle_status = MagicMock()
        p.handle_uncork_request(Message("recognizer_loop:record_end"))
        self.assertEqual(p.state, PlayerState.PLAYING)
        self.assertFalse(p._paused_on_duck)

    def test_uncork_noop_when_not_paused_on_duck(self):
        p = make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PAUSED
        p._paused_on_duck = False
        p.handle_status = MagicMock()
        p.handle_uncork_request(Message("recognizer_loop:record_end"))
        # State should remain PAUSED
        self.assertEqual(p.state, PlayerState.PAUSED)


class TestUnduckRequestVideo(unittest.TestCase):
    """Test handle_unduck_request with VIDEO playback."""

    def test_unduck_video_restores_volume(self):
        """handle_unduck_request with VIDEO should call video_service.restore_volume()."""
        p = make_player(PlaybackType.VIDEO)
        p._paused_on_duck = True

        p.handle_unduck_request(Message("x"))

        p.video_service.restore_volume.assert_called_once()


class TestPlayerUtteranceHandled(unittest.TestCase):
    """Test handle_utterance_handled."""

    def test_utterance_handled_calls_unduck(self):
        """handle_utterance_handled should call handle_unduck_request if _paused_on_duck."""
        p = make_player()
        p._paused_on_duck = True

        with patch.object(p, "handle_unduck_request") as mock_unduck:
            p.handle_utterance_handled(Message("x"))

        mock_unduck.assert_called_once()

    def test_utterance_handled_cork_path_resumes_playback(self):
        """Cork path: PAUSED + _paused_on_duck=True must resume via
        handle_uncork_request, not just restore volume, otherwise the
        player stays paused forever (record_end already no-op'd while a
        'speak' was in flight)."""
        p = make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PAUSED
        p._paused_on_duck = True
        p.handle_status = MagicMock()

        with patch.object(p, "handle_unduck_request") as mock_unduck:
            p.handle_utterance_handled(Message("ovos.utterance.handled"))

        mock_unduck.assert_not_called()
        self.assertEqual(p.state, PlayerState.PLAYING)
        self.assertFalse(p._paused_on_duck)

    def test_utterance_handled_duck_path_unchanged(self):
        """Duck path: PLAYING + _paused_on_duck=True must only restore
        volume, never call resume/uncork."""
        p = make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PLAYING
        p._paused_on_duck = True

        with patch.object(p, "handle_uncork_request") as mock_uncork:
            p.handle_utterance_handled(Message("ovos.utterance.handled"))

        mock_uncork.assert_not_called()
        p.audio_service.restore_volume.assert_called_once()
        self.assertEqual(p.state, PlayerState.PLAYING)
        self.assertFalse(p._paused_on_duck)

    def test_cork_then_utterance_handled_end_to_end_resumes(self):
        """End-to-end: record_begin corks playback, then
        ovos.utterance.handled must resume it (the previously-stuck
        sequence), and a late record_end afterwards is a harmless no-op."""
        p = make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()

        p.handle_cork_request(Message("recognizer_loop:record_begin"))
        self.assertEqual(p.state, PlayerState.PAUSED)
        self.assertTrue(p._paused_on_duck)

        p.handle_utterance_handled(Message("ovos.utterance.handled"))
        self.assertEqual(p.state, PlayerState.PLAYING)
        self.assertFalse(p._paused_on_duck)

        # a late record_end must no-op harmlessly (flag already cleared)
        p.handle_record_end(Message("recognizer_loop:record_end"))
        self.assertEqual(p.state, PlayerState.PLAYING)
        self.assertFalse(p._paused_on_duck)


class TestMycroftstopWhilePlaying(unittest.TestCase):
    """Test handle_mycroft_stop."""

    def test_mycroft_stop_when_playing(self):
        """handle_mycroft_stop should stop and reset when player is PLAYING."""
        p = make_player()
        p.state = PlayerState.PLAYING

        with patch.object(p, "stop"), patch.object(p, "reset"):
            p.handle_mycroft_stop(Message("mycroft.stop"))

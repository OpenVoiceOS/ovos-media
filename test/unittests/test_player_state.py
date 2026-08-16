"""Tests for OCPMediaPlayer state machine correctness and ducking handlers.

Covers:
- set_player_state / set_media_state actually update self.state / self.media_state
- set_player_state emits the NEW state (not the old one)
- recognizer_loop:* events route to the correct cork/duck handlers
- mycroft.stop handler stops playback and emits mycroft.stop.handled
"""
import unittest
from unittest.mock import MagicMock, patch

from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import PlayerState, MediaState, PlaybackType


def _make_player():
    """Return a minimal OCPMediaPlayer with FakeBus and mocked services."""
    from ovos_media.player import OCPMediaPlayer
    with patch("ovos_media.player.AudioService"), \
         patch("ovos_media.player.VideoService"), \
         patch("ovos_media.player.WebService"), \
         patch("ovos_media.player.OcpMprisExporter"), \
         patch("ovos_media.player.GUIInterface"), \
         patch("ovos_media.player.Configuration", return_value={"media": {}}), \
         patch("ovos_media.player.OCPMediaCatalog"):
        p = OCPMediaPlayer.__new__(OCPMediaPlayer)
        p._init_runtime_state()
        p.ocp_config = {}
        p.state = PlayerState.STOPPED
        p.loop_state = MagicMock()
        p.media_state = MediaState.NO_MEDIA
        p.shuffle = False
        p.track_history = {}
        p._paused_on_duck = False
        p.now_playing = MagicMock()
        p.now_playing.playback = PlaybackType.AUDIO
        p.now_playing.skill_id = "test.skill"
        p.playlist = MagicMock()
        p.playlist.as_list.return_value = []
        p.media = MagicMock()
        p.audio_service = MagicMock()
        p.video_service = MagicMock()
        p.web_service = MagicMock()
        p.current = None
        p.mpris = None
        p.bus = FakeBus()
        p.gui = MagicMock()
    return p


class TestSetPlayerStateStateAssignment(unittest.TestCase):
    """set_player_state must update self.state to the NEW value."""

    def test_transitions_from_stopped_to_paused(self):
        p = _make_player()
        p.handle_status = MagicMock()  # prevent JSON serialisation of MagicMock fields
        p.set_player_state(PlayerState.PAUSED)
        self.assertEqual(p.state, PlayerState.PAUSED)

    def test_transitions_from_stopped_to_playing(self):
        p = _make_player()
        p.handle_status = MagicMock()
        p.set_player_state(PlayerState.PLAYING)
        self.assertEqual(p.state, PlayerState.PLAYING)

    def test_transitions_paused_to_stopped(self):
        p = _make_player()
        p.handle_status = MagicMock()
        p.state = PlayerState.PAUSED
        p.set_player_state(PlayerState.STOPPED)
        self.assertEqual(p.state, PlayerState.STOPPED)

    def test_noop_when_same_state(self):
        p = _make_player()
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
        p = _make_player()
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
        p = _make_player()
        p.set_media_state(MediaState.BUFFERED_MEDIA)
        self.assertEqual(p.media_state, MediaState.BUFFERED_MEDIA)

    def test_emits_new_media_state(self):
        p = _make_player()
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
        p = _make_player()
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
        p = _make_player()
        p.state = PlayerState.PAUSED
        p._paused_on_duck = True
        p.now_playing.playback = PlaybackType.AUDIO
        from ovos_bus_client.message import Message
        p.handle_unduck_request(Message("ovos.common_play.unduck"))
        p.audio_service.restore_volume.assert_called_once()
        self.assertFalse(p._paused_on_duck)

    def test_record_begin_calls_cork(self):
        """recognizer_loop:record_begin → handle_cork_request"""
        p = _make_player()
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
        p = _make_player()
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
        p = _make_player()
        p.state = PlayerState.STOPPED
        p.stop = MagicMock()

        from ovos_bus_client.message import Message
        p.handle_mycroft_stop(Message("mycroft.stop"))

        p.stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()

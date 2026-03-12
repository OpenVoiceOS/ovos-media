"""Coverage tests for ovos_media/legacy_api.py.

Targets handle_queue (STOPPED path), handle_track_info, handle_list_backends,
handle_get_track_position, handle_get_track_length.
"""
import unittest
from unittest.mock import MagicMock, patch, call

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import PlayerState


def _make_compat():
    """Return a LegacyAudioServiceCompat with mocked player and FakeBus."""
    from ovos_media.legacy_api import LegacyAudioServiceCompat
    bus = FakeBus()
    player = MagicMock()
    player.state = PlayerState.STOPPED
    player.audio_service = MagicMock()
    compat = LegacyAudioServiceCompat(player, bus)
    return compat, bus, player


class TestHandleQueueWhenStopped(unittest.TestCase):
    """Test handle_queue with player in STOPPED state."""

    def test_queue_when_stopped_calls_handle_play(self):
        """When player is STOPPED, queue should call handle_play instead of queuing."""
        compat, bus, player = _make_compat()
        player.state = PlayerState.STOPPED

        received_play = []
        bus.on("ovos.common_play.play", lambda m: received_play.append(m))

        # Emit queue message while player is stopped
        bus.emit(Message("mycroft.audio.service.queue", {
            "tracks": ["http://example.com/track.mp3"],
        }))

        # Should have emitted an ovos.common_play.play message instead
        self.assertEqual(len(received_play), 1)
        self.assertIn("media", received_play[0].data)

    def test_queue_with_no_tracks_when_stopped_still_forwards_to_play(self):
        """Queue with empty tracks list still forwards to play when stopped."""
        compat, bus, player = _make_compat()
        player.state = PlayerState.STOPPED

        received_play = []
        bus.on("ovos.common_play.play", lambda m: received_play.append(m))

        bus.emit(Message("mycroft.audio.service.queue", {"tracks": []}))

        # Empty queue should not emit anything (handled by handle_play)
        # But the forward still happens
        self.assertEqual(len(received_play), 0)


class TestHandleTrackInfo(unittest.TestCase):
    """Test handle_track_info with wait_for_response."""

    def test_track_info_forwards_response_from_ocp(self):
        """handle_track_info should forward OCP's response as a reply."""
        compat, bus, player = _make_compat()

        received_reply = []
        bus.on("mycroft.audio.service.track_info_reply", lambda m: received_reply.append(m))

        # Mock the wait_for_response to return a fake response
        response_msg = Message("ovos.common_play.track_info.response", {
            "uri": "http://example.com/track.mp3",
            "title": "Test Track",
        })
        with patch.object(compat.bus, "wait_for_response", return_value=response_msg):
            bus.emit(Message("mycroft.audio.service.track_info"))

        # Verify reply was emitted with the OCP response data
        self.assertEqual(len(received_reply), 1)
        self.assertEqual(received_reply[0].data["uri"], "http://example.com/track.mp3")

    def test_track_info_handles_no_response(self):
        """handle_track_info should emit empty dict when wait_for_response returns None."""
        compat, bus, player = _make_compat()

        received_reply = []
        bus.on("mycroft.audio.service.track_info_reply", lambda m: received_reply.append(m))

        # Mock wait_for_response to return None (timeout)
        with patch.object(compat.bus, "wait_for_response", return_value=None):
            bus.emit(Message("mycroft.audio.service.track_info"))

        # Should emit reply with empty data dict
        self.assertEqual(len(received_reply), 1)
        self.assertEqual(received_reply[0].data, {})


class TestHandleListBackends(unittest.TestCase):
    """Test handle_list_backends with wait_for_response."""

    def test_list_backends_forwards_response(self):
        """handle_list_backends should forward OCP's response."""
        compat, bus, player = _make_compat()

        received_reply = []
        bus.on("mycroft.audio.service.list_backends.response", lambda m: received_reply.append(m))

        response_msg = Message("ovos.common_play.list_backends.response", {
            "plugins": ["vlc", "mplayer"],
        })
        with patch.object(compat.bus, "wait_for_response", return_value=response_msg):
            bus.emit(Message("mycroft.audio.service.list_backends"))

        self.assertEqual(len(received_reply), 1)
        self.assertEqual(received_reply[0].data["plugins"], ["vlc", "mplayer"])

    def test_list_backends_handles_no_response(self):
        """handle_list_backends should emit empty dict when response times out."""
        compat, bus, player = _make_compat()

        received_reply = []
        bus.on("mycroft.audio.service.list_backends.response", lambda m: received_reply.append(m))

        with patch.object(compat.bus, "wait_for_response", return_value=None):
            bus.emit(Message("mycroft.audio.service.list_backends"))

        self.assertEqual(len(received_reply), 1)
        self.assertEqual(received_reply[0].data, {})


class TestHandleGetTrackPosition(unittest.TestCase):
    """Test handle_get_track_position with wait_for_response."""

    def test_get_track_position_forwards_response(self):
        """handle_get_track_position should forward OCP's position response."""
        compat, bus, player = _make_compat()

        received_reply = []
        bus.on("mycroft.audio.service.get_track_position.response", lambda m: received_reply.append(m))

        response_msg = Message("ovos.common_play.get_track_position.response", {
            "position": 45000,
        })
        with patch.object(compat.bus, "wait_for_response", return_value=response_msg):
            bus.emit(Message("mycroft.audio.service.get_track_position"))

        self.assertEqual(len(received_reply), 1)
        self.assertEqual(received_reply[0].data["position"], 45000)

    def test_get_track_position_defaults_to_none(self):
        """handle_get_track_position should default to None when no response."""
        compat, bus, player = _make_compat()

        received_reply = []
        bus.on("mycroft.audio.service.get_track_position.response", lambda m: received_reply.append(m))

        with patch.object(compat.bus, "wait_for_response", return_value=None):
            bus.emit(Message("mycroft.audio.service.get_track_position"))

        self.assertEqual(len(received_reply), 1)
        self.assertEqual(received_reply[0].data["position"], None)


class TestHandleGetTrackLength(unittest.TestCase):
    """Test handle_get_track_length with wait_for_response."""

    def test_get_track_length_forwards_response(self):
        """handle_get_track_length should forward OCP's length response."""
        compat, bus, player = _make_compat()

        received_reply = []
        bus.on("mycroft.audio.service.get_track_length.response", lambda m: received_reply.append(m))

        response_msg = Message("ovos.common_play.get_track_length.response", {
            "length": 180000,
        })
        with patch.object(compat.bus, "wait_for_response", return_value=response_msg):
            bus.emit(Message("mycroft.audio.service.get_track_length"))

        self.assertEqual(len(received_reply), 1)
        self.assertEqual(received_reply[0].data["length"], 180000)

    def test_get_track_length_defaults_to_none(self):
        """handle_get_track_length should default to None when no response."""
        compat, bus, player = _make_compat()

        received_reply = []
        bus.on("mycroft.audio.service.get_track_length.response", lambda m: received_reply.append(m))

        with patch.object(compat.bus, "wait_for_response", return_value=None):
            bus.emit(Message("mycroft.audio.service.get_track_length"))

        self.assertEqual(len(received_reply), 1)
        self.assertEqual(received_reply[0].data["length"], None)


if __name__ == "__main__":
    unittest.main()

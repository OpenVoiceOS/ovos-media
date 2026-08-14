"""Tests for LegacyAudioServiceCompat.

Validates that the mycroft.audio.service.* shim correctly translates legacy
bus messages to their ovos.common_play.* equivalents.  Uses FakeBus so no
real D-Bus or OCP player is needed.
"""
import unittest
from unittest.mock import MagicMock, patch, call

from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import PlayerState


def _make_compat():
    """Return a LegacyAudioServiceCompat with a mocked player and FakeBus."""
    from ovos_media.legacy_api import LegacyAudioServiceCompat
    bus = FakeBus()
    player = MagicMock()
    player.state = PlayerState.STOPPED
    player.audio_service = MagicMock()
    compat = LegacyAudioServiceCompat(player, bus)
    return compat, bus, player


class TestLegacyPlayTranslation(unittest.TestCase):
    """mycroft.audio.service.play → ovos.common_play.play"""

    def test_play_emits_ocp_play(self):
        compat, bus, player = _make_compat()
        received = []
        bus.on("ovos.common_play.play", lambda m: received.append(m))

        from ovos_bus_client.message import Message
        bus.emit(Message("mycroft.audio.service.play", {
            "tracks": ["http://example.com/track.mp3"],
            "repeat": False,
        }))

        self.assertEqual(len(received), 1)
        self.assertIn("media", received[0].data)
        self.assertIn("playlist", received[0].data)

    def test_play_with_multiple_tracks_builds_playlist(self):
        compat, bus, player = _make_compat()
        received = []
        bus.on("ovos.common_play.play", lambda m: received.append(m))

        from ovos_bus_client.message import Message
        bus.emit(Message("mycroft.audio.service.play", {
            "tracks": [
                "http://example.com/a.mp3",
                "http://example.com/b.mp3",
            ],
        }))

        self.assertEqual(len(received[0].data["playlist"]), 2)

    def test_play_carries_utterance_through(self):
        """Legacy 'utterance' field (used for by-name backend selection)
        must survive translation into ovos.common_play.play."""
        compat, bus, player = _make_compat()
        received = []
        bus.on("ovos.common_play.play", lambda m: received.append(m))

        from ovos_bus_client.message import Message
        bus.emit(Message("mycroft.audio.service.play", {
            "tracks": ["http://example.com/track.mp3"],
            "utterance": "play track.mp3 using vlc",
        }))

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].data.get("utterance"),
                         "play track.mp3 using vlc")

    def test_play_without_utterance_forwards_empty_string(self):
        """utterance key is always present (empty default) so downstream
        by-name selection logic never KeyErrors."""
        compat, bus, player = _make_compat()
        received = []
        bus.on("ovos.common_play.play", lambda m: received.append(m))

        from ovos_bus_client.message import Message
        bus.emit(Message("mycroft.audio.service.play", {
            "tracks": ["http://example.com/track.mp3"],
        }))

        self.assertEqual(received[0].data.get("utterance"), "")

    def test_play_with_no_tracks_does_not_emit(self):
        compat, bus, player = _make_compat()
        received = []
        bus.on("ovos.common_play.play", lambda m: received.append(m))

        from ovos_bus_client.message import Message
        bus.emit(Message("mycroft.audio.service.play", {"tracks": []}))
        self.assertEqual(len(received), 0)

    def test_play_passes_repeat_flag(self):
        compat, bus, player = _make_compat()
        received = []
        bus.on("ovos.common_play.play", lambda m: received.append(m))

        from ovos_bus_client.message import Message
        bus.emit(Message("mycroft.audio.service.play", {
            "tracks": ["http://example.com/track.mp3"],
            "repeat": True,
        }))

        self.assertTrue(received[0].data["repeat"])


class TestLegacyQueueTranslation(unittest.TestCase):
    """mycroft.audio.service.queue → ovos.common_play.playlist.queue"""

    def test_queue_while_playing_emits_playlist_queue(self):
        compat, bus, player = _make_compat()
        player.state = PlayerState.PLAYING
        received = []
        bus.on("ovos.common_play.playlist.queue", lambda m: received.append(m))

        from ovos_bus_client.message import Message
        bus.emit(Message("mycroft.audio.service.queue", {
            "tracks": ["http://example.com/c.mp3"],
        }))

        self.assertEqual(len(received), 1)
        self.assertEqual(len(received[0].data["tracks"]), 1)

    def test_queue_while_stopped_falls_back_to_play(self):
        compat, bus, player = _make_compat()
        player.state = PlayerState.STOPPED
        play_received = []
        bus.on("ovos.common_play.play", lambda m: play_received.append(m))

        from ovos_bus_client.message import Message
        bus.emit(Message("mycroft.audio.service.queue", {
            "tracks": ["http://example.com/d.mp3"],
        }))

        self.assertEqual(len(play_received), 1)


class TestLegacyControlTranslations(unittest.TestCase):
    """Pause/resume/stop/next/prev → ovos.common_play.*"""

    def _assert_forward(self, legacy_msg, expected_ocp_msg):
        compat, bus, player = _make_compat()
        received = []
        bus.on(expected_ocp_msg, lambda m: received.append(m))

        from ovos_bus_client.message import Message
        bus.emit(Message(legacy_msg))
        self.assertEqual(len(received), 1,
                         f"Expected {expected_ocp_msg} after {legacy_msg}")

    def test_pause(self):
        self._assert_forward("mycroft.audio.service.pause",
                             "ovos.common_play.pause")

    def test_resume(self):
        self._assert_forward("mycroft.audio.service.resume",
                             "ovos.common_play.resume")

    def test_stop(self):
        self._assert_forward("mycroft.audio.service.stop",
                             "ovos.common_play.stop")

    def test_next(self):
        self._assert_forward("mycroft.audio.service.next",
                             "ovos.common_play.next")

    def test_prev(self):
        self._assert_forward("mycroft.audio.service.prev",
                             "ovos.common_play.previous")


class TestLegacySeekTranslations(unittest.TestCase):
    """seek_forward / seek_backward → ovos.common_play.seek"""

    def test_seek_forward_emits_positive_seconds(self):
        compat, bus, player = _make_compat()
        received = []
        bus.on("ovos.common_play.seek", lambda m: received.append(m))

        from ovos_bus_client.message import Message
        bus.emit(Message("mycroft.audio.service.seek_forward", {"seconds": 10}))

        self.assertEqual(received[0].data["seconds"], 10)

    def test_seek_backward_emits_negative_seconds(self):
        compat, bus, player = _make_compat()
        received = []
        bus.on("ovos.common_play.seek", lambda m: received.append(m))

        from ovos_bus_client.message import Message
        bus.emit(Message("mycroft.audio.service.seek_backward", {"seconds": 5}))

        self.assertEqual(received[0].data["seconds"], -5)


class TestLegacySetTrackPosition(unittest.TestCase):
    """set_track_position → ovos.common_play.set_track_position"""

    def test_position_forwarded(self):
        compat, bus, player = _make_compat()
        received = []
        bus.on("ovos.common_play.set_track_position", lambda m: received.append(m))

        from ovos_bus_client.message import Message
        bus.emit(Message("mycroft.audio.service.set_track_position",
                         {"position": 30000}))

        self.assertEqual(received[0].data["position"], 30000)


class TestTracksToEntries(unittest.TestCase):
    """_tracks_to_entries helper correctly converts legacy formats."""

    def test_string_list(self):
        from ovos_media.legacy_api import _tracks_to_entries
        entries = _tracks_to_entries(["http://a.com/a.mp3", "http://b.com/b.mp3"])
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["uri"], "http://a.com/a.mp3")

    def test_tuple_list(self):
        from ovos_media.legacy_api import _tracks_to_entries
        entries = _tracks_to_entries([("http://a.com/a.mp3", "audio/mpeg")])
        self.assertEqual(entries[0]["uri"], "http://a.com/a.mp3")

    def test_entries_have_audio_playback_type(self):
        from ovos_media.legacy_api import _tracks_to_entries
        from ovos_utils.ocp import PlaybackType
        entries = _tracks_to_entries(["http://a.com/a.mp3"])
        self.assertEqual(entries[0]["playback"], PlaybackType.AUDIO)


class TestLegacyCompatShutdown(unittest.TestCase):
    """shutdown() must remove all listeners."""

    def test_shutdown_removes_play_listener(self):
        compat, bus, player = _make_compat()
        received = []
        bus.on("ovos.common_play.play", lambda m: received.append(m))

        compat.shutdown()

        from ovos_bus_client.message import Message
        bus.emit(Message("mycroft.audio.service.play",
                         {"tracks": ["http://example.com/t.mp3"]}))
        # listener was removed so no translation should happen
        self.assertEqual(len(received), 0)


if __name__ == "__main__":
    unittest.main()

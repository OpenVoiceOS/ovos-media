"""``"announce_end": false`` on ovos.common_play.play silences the
queue_finished dialog for the queue that request creates."""
import unittest
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import MediaEntry, MediaState, PlaybackType, PlayerState


def _player():
    from ovos_media.player import OCPMediaPlayer
    player = OCPMediaPlayer(FakeBus(), config={"autoplay": True})
    player.now_playing.extract_stream = lambda **kwargs: None
    player.media.notify_dialog = MagicMock()
    player.audio_service = MagicMock(services=[MagicMock()])
    return player


def _track(name):
    return MediaEntry(uri=f"http://example.com/{name}.mp3", title=name,
                      playback=PlaybackType.AUDIO).as_dict


def _play_to_end(player, **extra):
    a, b = _track("a"), _track("b")
    with patch.object(player, "play"):
        player.bus.emit(
            Message("ovos.common_play.play",
                    {"media": a, "playlist": [a, b], **extra}))
        for _ in range(2):
            player.set_player_state(PlayerState.PLAYING)
            player.media_state = MediaState.BUFFERED_MEDIA
            player.bus.emit(Message("ovos.common_play.media.state",
                                    {"state": MediaState.END_OF_MEDIA}))
    assert player.state == PlayerState.STOPPED


def _spoken(player):
    return [c.args[0] for c in player.media.notify_dialog.call_args_list]


class TestAnnounceEnd(unittest.TestCase):
    def test_false_ends_queue_silently(self):
        player = _player()
        _play_to_end(player, announce_end=False)
        self.assertEqual(_spoken(player), [])
        player.shutdown()

    def test_default_speaks_queue_finished(self):
        player = _player()
        _play_to_end(player)
        self.assertEqual(_spoken(player), ["queue_finished"])
        player.shutdown()

    def test_later_request_without_flag_speaks_again(self):
        player = _player()
        _play_to_end(player, announce_end=False)
        _play_to_end(player)
        self.assertEqual(_spoken(player), ["queue_finished"])
        player.shutdown()

    def test_playlist_set_after_silent_queue_speaks(self):
        player = _player()
        _play_to_end(player, announce_end=False)
        c, d = _track("c"), _track("d")
        with patch.object(player, "play"):
            player.bus.emit(Message("ovos.common_play.playlist.set",
                                    {"tracks": [c, d]}))
            # nothing is loaded, so one next reaches the end of the queue
            player.bus.emit(Message("ovos.common_play.next"))
        self.assertEqual(player.state, PlayerState.STOPPED)
        self.assertEqual(_spoken(player), ["queue_finished"])
        player.shutdown()

"""A queue of extractor uris must still move on once a stream is extracted.

A playlist sent over the bus ('ovos.common_play.play' with dicts) whose
entries are '{sei}//...' references: the player decodes its own MediaEntry
objects from the dicts, so the queue cannot find the current track by
identity, only by uri. Once the track plays, extract_stream() has replaced
now_playing.uri with the real stream ('file://...', 'https://...') and kept
the queue's uri as now_playing.original_uri. Locating by the stream uri finds
nothing, so the next track, the previous track and the advance at the end of
a track all saw the end of the queue on the first track.

Found reading a story aloud through ovos-media: one 'tts//<lang>/<sentence>'
entry per sentence (OpenVoiceOS/ovos-media#240); the same holds for a
playlist of youtube// or rss// entries.
"""
import unittest
from unittest.mock import patch

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import MediaEntry, MediaState, PlaybackType, PlayerState

URIS = [f"tts//en-US/Sentence%20{i}." for i in range(1, 4)]


def _player():
    from ovos_media.player import OCPMediaPlayer
    player = OCPMediaPlayer(FakeBus(), config={})

    def extract(**kwargs):
        # what streams.extract_stream does with an extractor's answer
        np = player.now_playing
        if np.uri.startswith("tts//"):
            np.original_uri = np.uri
            np.uri = f"file:///tmp/{URIS.index(np.uri)}.wav"

    player.now_playing.extract_stream = extract
    return player


def _play(player, start=0):
    """'ovos.common_play.play' as a client sends it: dicts, not entries."""
    entries = [MediaEntry(uri=u, title="Story", playback=PlaybackType.AUDIO).as_dict
               for u in URIS]
    with patch.object(player, "play"):
        player.play_media(entries[start], playlist=entries)
    player.now_playing.extract_stream()  # what play() does before the backend
    player.set_player_state(PlayerState.PLAYING)
    player.media_state = MediaState.BUFFERED_MEDIA


def _playing(player):
    return player.now_playing.original_uri or player.now_playing.uri


class TestQueueOfExtractorUris(unittest.TestCase):

    def test_the_first_track_plays_extracted(self):
        player = _player()
        _play(player)
        self.assertEqual(player.now_playing.uri, "file:///tmp/0.wav")
        self.assertEqual(player.now_playing.original_uri, URIS[0])
        player.shutdown()

    def test_next(self):
        player = _player()
        _play(player)
        with patch.object(player, "play"):
            player.play_next()
        self.assertEqual(_playing(player), URIS[1])
        player.shutdown()

    def test_previous(self):
        player = _player()
        _play(player, start=2)
        with patch.object(player, "play"):
            player.play_prev()
        self.assertEqual(_playing(player), URIS[1])
        player.shutdown()

    def test_end_of_track_moves_on(self):
        player = _player()
        _play(player)
        with patch.object(player, "play"):
            player.bus.emit(Message("ovos.common_play.media.state",
                                    {"state": MediaState.END_OF_MEDIA}))
        self.assertEqual(_playing(player), URIS[1])
        player.shutdown()

    def test_the_last_track_still_ends_the_queue(self):
        player = _player()
        _play(player, start=2)
        with patch.object(player, "play") as play:
            player.play_next()
        play.assert_not_called()
        self.assertEqual(player.state, PlayerState.STOPPED)
        player.shutdown()


if __name__ == "__main__":
    unittest.main()

"""Tests that play_media validates disambiguation/playlist/track payloads
before mutating any playlist state, instead of letting a malformed
skill/bus-supplied entry raise mid-mutation."""
import unittest
from unittest.mock import MagicMock, patch

from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import (
    PlayerState, MediaState, LoopState, PlaybackType, MediaEntry, Playlist,
)


def _make_player():
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
        p.loop_state = LoopState.NONE
        p.media_state = MediaState.NO_MEDIA
        p.shuffle = False
        p.track_history = {}
        p._paused_on_duck = False
        p.now_playing = MagicMock()
        p.now_playing.playback = PlaybackType.AUDIO
        p.playlist = Playlist()
        p.media = MagicMock()
        p.media.search_playlist = Playlist()
        p.audio_service = MagicMock()
        p.video_service = MagicMock()
        p.web_service = MagicMock()
        p.current = None
        p.mpris = None
        p._no_backend_dialog_spoken = True
        p.bus = FakeBus()
        p.gui = MagicMock()
    return p


VALID_TRACK = {"uri": "http://example.com/a.mp3", "title": "A"}
VALID_TRACK_2 = {"uri": "http://example.com/b.mp3", "title": "B"}
NO_URI_TRACK = {"title": "no uri"}


class TestPlayMediaValidatesDisambiguation(unittest.TestCase):

    def test_invalid_entry_dropped_valid_entry_kept_no_crash(self):
        p = _make_player()
        p.set_now_playing = MagicMock()
        p.play = MagicMock()

        p.play_media(VALID_TRACK, disambiguation=[VALID_TRACK, NO_URI_TRACK])

        uris = [e.uri for e in p.media.search_playlist.entries]
        self.assertEqual(uris, [VALID_TRACK["uri"]])
        p.play.assert_called_once()

    def test_all_invalid_entries_leave_search_playlist_empty(self):
        p = _make_player()
        p.set_now_playing = MagicMock()
        p.play = MagicMock()

        p.play_media(VALID_TRACK, disambiguation=[None, 42, "str"])

        self.assertEqual(len(p.media.search_playlist.entries), 0)
        p.play.assert_called_once()


class TestPlayMediaValidatesPlaylist(unittest.TestCase):

    def test_invalid_playlist_entries_do_not_raise_and_are_dropped(self):
        p = _make_player()
        p.set_now_playing = MagicMock()
        p.play = MagicMock()

        p.play_media(VALID_TRACK, playlist=[VALID_TRACK, NO_URI_TRACK])

        uris = [e.uri for e in p.playlist.entries]
        self.assertEqual(uris, [VALID_TRACK["uri"]])

    def test_fully_invalid_playlist_leaves_prior_playlist_untouched(self):
        p = _make_player()
        p.playlist.add_entry(MediaEntry.from_dict(VALID_TRACK_2))
        p.set_now_playing = MagicMock()
        p.play = MagicMock()

        p.play_media(VALID_TRACK, playlist=[None, 42, "str"])

        uris = [e.uri for e in p.playlist.entries]
        self.assertEqual(uris, [VALID_TRACK_2["uri"]])


class TestPlayMediaValidatesTrack(unittest.TestCase):

    def test_malformed_track_returns_without_mutating_or_playing(self):
        p = _make_player()
        p.playlist.add_entry(MediaEntry.from_dict(VALID_TRACK_2))
        p.media.search_playlist.add_entry(MediaEntry.from_dict(VALID_TRACK_2))
        p.set_now_playing = MagicMock()
        p.play = MagicMock()

        p.play_media(NO_URI_TRACK, disambiguation=[VALID_TRACK],
                     playlist=[VALID_TRACK])

        self.assertEqual([e.uri for e in p.playlist.entries],
                         [VALID_TRACK_2["uri"]])
        self.assertEqual([e.uri for e in p.media.search_playlist.entries],
                         [VALID_TRACK_2["uri"]])
        p.set_now_playing.assert_not_called()
        p.play.assert_not_called()


class TestPlayMediaValidPayloadStillPlays(unittest.TestCase):

    def test_fully_valid_payload_plays(self):
        p = _make_player()
        p.set_now_playing = MagicMock()
        p.play = MagicMock()

        p.play_media(VALID_TRACK, disambiguation=[VALID_TRACK],
                     playlist=[VALID_TRACK])

        p.set_now_playing.assert_called_once()
        p.play.assert_called_once()
        self.assertEqual([e.uri for e in p.media.search_playlist.entries],
                         [VALID_TRACK["uri"]])
        self.assertEqual([e.uri for e in p.playlist.entries],
                         [VALID_TRACK["uri"]])


if __name__ == "__main__":
    unittest.main()

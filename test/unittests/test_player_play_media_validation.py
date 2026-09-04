"""Tests that play_media validates disambiguation/playlist/track payloads
before mutating any playlist state, instead of letting a malformed
skill/bus-supplied entry raise mid-mutation."""
import unittest
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import (
    PlayerState, MediaState, LoopState, PlaybackType, MediaEntry, Playlist,
    )

from player_fixture import make_player


def _make_player():
    from ovos_media.player import OCPMediaPlayer

    with patch("ovos_media.player.AudioService"), \
         patch("ovos_media.player.VideoService"), \
         patch("ovos_media.player.WebService"), \
         patch("ovos_media.player.OcpMprisExporter"), \
         patch("ovos_media.player.Configuration", return_value={"media": {}}), \
         patch("ovos_media.player.OCPMediaCatalog"):
        p = OCPMediaPlayer.__new__(OCPMediaPlayer)
        p._init_runtime_state()
        p.ocp_config = {}
        p._live_config = False
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


class TestPlayMediaAcceptsPluginStream(unittest.TestCase):

    def test_plugin_stream_shaped_track_reaches_set_now_playing(self):
        """A valid PluginStream-shaped dict (extractor_id+stream, no uri) is
        deserialized by MediaEntry.from_dict's dict2entry fallback into a
        PluginStream, which is a legitimate single-track play request and
        must not raise."""
        p = _make_player()
        p.set_now_playing = MagicMock()
        p.play = MagicMock()

        track = {"extractor_id": "ocp_youtube", "stream": "abc123",
                 "title": "Plugin Track"}
        p.play_media(track)  # must not raise

        p.set_now_playing.assert_called_once()
        played = p.set_now_playing.call_args[0][0]
        self.assertIsInstance(played, MediaEntry)
        p.play.assert_called_once()

    def test_unrepresentable_track_warns_and_returns_without_mutation(self):
        p = _make_player()
        p.playlist.add_entry(MediaEntry.from_dict(VALID_TRACK_2))
        p.set_now_playing = MagicMock()
        p.play = MagicMock()

        p.play_media(42)  # not a dict, MediaEntry, or PluginStream

        p.set_now_playing.assert_not_called()
        p.play.assert_not_called()
        self.assertEqual([e.uri for e in p.playlist.entries],
                         [VALID_TRACK_2["uri"]])


class TestPlayMediaAllInvalidDisambiguationKeepsPriorResults(unittest.TestCase):

    def test_all_invalid_disambiguation_leaves_prior_search_playlist_intact(self):
        p = _make_player()
        p.media.search_playlist.add_entry(MediaEntry.from_dict(VALID_TRACK_2))
        p.set_now_playing = MagicMock()
        p.play = MagicMock()

        p.play_media(VALID_TRACK, disambiguation=[None, 42, "str"])

        self.assertEqual([e.uri for e in p.media.search_playlist.entries],
                         [VALID_TRACK_2["uri"]])


class TestPlayMediaDisambiguationIsTheCandidateSet(unittest.TestCase):

    def test_replaying_a_candidate_keeps_the_candidate_set(self):
        """Playing an entry already among the candidates must not wipe them."""
        p = _make_player()
        p.set_now_playing = MagicMock()
        p.play = MagicMock()
        candidates = [VALID_TRACK, VALID_TRACK_2]

        p.play_media(VALID_TRACK, disambiguation=candidates)
        # the user picks a candidate from the set the UI was just handed
        p.play_media(VALID_TRACK_2, disambiguation=candidates)

        self.assertEqual(sorted(e.uri for e in p.media.search_playlist.entries),
                         sorted([VALID_TRACK["uri"], VALID_TRACK_2["uri"]]))

    def test_partly_new_disambiguation_keeps_the_overlap(self):
        """A new candidate set replaces the old one wholesale — entries the
        old set also contained stay, because they are in the new set."""
        p = _make_player()
        p.set_now_playing = MagicMock()
        p.play = MagicMock()

        p.play_media(VALID_TRACK, disambiguation=[VALID_TRACK])
        p.play_media(VALID_TRACK, disambiguation=[VALID_TRACK, VALID_TRACK_2])

        self.assertEqual(sorted(e.uri for e in p.media.search_playlist.entries),
                         sorted([VALID_TRACK["uri"], VALID_TRACK_2["uri"]]))


class TestPlayMedia(unittest.TestCase):
    """play_media sets up now_playing and calls play."""

    def _make_audio_entry(self):
        return MediaEntry(
            title="Test Song",
            artist="Test Artist",
            uri="http://example.com/song.mp3",
            playback=PlaybackType.AUDIO,
        )

    def test_play_media_calls_set_now_playing(self):
        p = make_player(PlaybackType.AUDIO)
        p.set_now_playing = MagicMock()
        p.play = MagicMock()
        p.media.search_playlist.replace = MagicMock()
        entry = self._make_audio_entry()
        p.playlist.__contains__ = MagicMock(return_value=False)
        p.play_media(entry)
        p.set_now_playing.assert_called_once_with(entry)

    def test_play_media_calls_play(self):
        p = make_player(PlaybackType.AUDIO)
        p.set_now_playing = MagicMock()
        p.play = MagicMock()
        entry = self._make_audio_entry()
        p.playlist.__contains__ = MagicMock(return_value=False)
        p.play_media(entry)
        p.play.assert_called_once()

    def test_play_media_accepts_dict(self):
        p = make_player(PlaybackType.AUDIO)
        p.set_now_playing = MagicMock()
        p.play = MagicMock()
        p.playlist.__contains__ = MagicMock(return_value=False)
        track_dict = {
            "title": "Dict Song",
            "uri": "http://example.com/dict.mp3",
            "playback": PlaybackType.AUDIO,
        }
        p.play_media(track_dict)
        p.play.assert_called_once()

    def test_play_media_invalid_type_warns_and_returns_without_raising(self):
        # play_media is bus-facing; an unrepresentable track type must not
        # raise out of the handler, just be logged and skipped.
        p = make_player()
        p.set_now_playing = MagicMock()
        p.play = MagicMock()
        p.play_media(12345)  # must not raise
        p.set_now_playing.assert_not_called()
        p.play.assert_not_called()

    def test_play_media_stops_mpris(self):
        p = make_player(PlaybackType.AUDIO)
        p.mpris = MagicMock()
        p.set_now_playing = MagicMock()
        p.play = MagicMock()
        p.playlist.__contains__ = MagicMock(return_value=False)
        entry = self._make_audio_entry()
        p.play_media(entry)
        p.mpris.stop.assert_called_once()

    def test_play_media_with_disambiguation_updates_search_playlist(self):
        p = make_player(PlaybackType.AUDIO)
        p.set_now_playing = MagicMock()
        p.play = MagicMock()
        p.playlist.__contains__ = MagicMock(return_value=False)
        entry = self._make_audio_entry()
        other = MediaEntry(title="Alt", uri="http://example.com/alt.mp3",
                           playback=PlaybackType.AUDIO)
        p.media.search_playlist.__contains__ = MagicMock(return_value=False)
        p.play_media(entry, disambiguation=[entry, other])
        p.media.search_playlist.replace.assert_called_once()


class TestPlayerHandlePlayRequestNoMedia(unittest.TestCase):
    """Test handle_play_request with no media."""

    def test_play_request_no_media_returns_early(self):
        """handle_play_request with no media should return without playing."""
        p = make_player()

        with patch.object(p, "play_media") as mock_play:
            p.handle_play_request(Message("ovos.common_play.play", {}))

        mock_play.assert_not_called()

# Copyright 2024, Mycroft AI Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for OCPMediaPlayer bus handler methods.

Covers the happy-path for every request handler registered in
``register_bus_handlers``, as well as helper methods that those handlers
delegate to (pause, resume, stop, seek, play_next, play_prev, etc.).

The player is constructed the same way as the existing test fixtures:
  - OVOSAbstractApplication.__init__ is stubbed out
  - All external service classes are patched to MagicMock instances
  - A real FakeBus is used so that bus.emit() calls can be captured
"""
import unittest
from unittest.mock import MagicMock, patch, call

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import (
    PlayerState,
    MediaState,
    LoopState,
    PlaybackType,
    MediaEntry,
    Playlist,
)


# ---------------------------------------------------------------------------
# Shared factory — mirrors the pattern in test_player.py / test_player_state.py
# ---------------------------------------------------------------------------

def _make_player(playback_type: PlaybackType = PlaybackType.AUDIO):
    """Return a minimal OCPMediaPlayer with all external deps mocked.

    Args:
        playback_type: PlaybackType to assign to now_playing (default AUDIO).

    Returns:
        OCPMediaPlayer instance with mocked services and FakeBus.
    """
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
        p.now_playing.playback = playback_type
        p.now_playing.skill_id = "test.skill"
        p.now_playing.uri = "http://example.com/track.mp3"
        p.now_playing.original_uri = "http://example.com/track.mp3"
        p.now_playing.title = "Test Track"
        p.now_playing.artist = "Test Artist"
        p.now_playing.image = ""
        p.now_playing.length = 180000
        p.now_playing.position = 0
        p.now_playing.media_type = MagicMock()
        p.now_playing.as_dict = {
            "uri": "http://example.com/track.mp3",
            "title": "Test Track",
            "artist": "Test Artist",
            "image": "",
        }
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


# ---------------------------------------------------------------------------
# handle_pause_request
# ---------------------------------------------------------------------------

class TestHandlePauseRequest(unittest.TestCase):
    """handle_pause_request delegates to pause()."""

    def test_pause_calls_audio_service(self):
        p = _make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()
        p.handle_pause_request(Message("ovos.common_play.pause"))
        p.audio_service.pause.assert_called_once()

    def test_pause_sets_player_state_paused(self):
        p = _make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()
        p.handle_pause_request(Message("ovos.common_play.pause"))
        self.assertEqual(p.state, PlayerState.PAUSED)

    def test_pause_clears_paused_on_duck_flag(self):
        p = _make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PLAYING
        p._paused_on_duck = True
        p.handle_status = MagicMock()
        p.handle_pause_request(Message("ovos.common_play.pause"))
        self.assertFalse(p._paused_on_duck)

    def test_pause_calls_update_gui(self):
        p = _make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()
        p._update_gui = MagicMock()
        p.handle_pause_request(Message("ovos.common_play.pause"))
        p._update_gui.assert_called()


# ---------------------------------------------------------------------------
# handle_resume_request
# ---------------------------------------------------------------------------

class TestHandleResumeRequest(unittest.TestCase):
    """handle_resume_request delegates to resume()."""

    def test_resume_calls_audio_service(self):
        p = _make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PAUSED
        p.handle_status = MagicMock()
        p.handle_resume_request(Message("ovos.common_play.resume"))
        p.audio_service.resume.assert_called_once()

    def test_resume_sets_player_state_playing(self):
        p = _make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PAUSED
        p.handle_status = MagicMock()
        p.handle_resume_request(Message("ovos.common_play.resume"))
        self.assertEqual(p.state, PlayerState.PLAYING)

    def test_resume_calls_update_gui(self):
        p = _make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PAUSED
        p.handle_status = MagicMock()
        p._update_gui = MagicMock()
        p.handle_resume_request(Message("ovos.common_play.resume"))
        p._update_gui.assert_called()


# ---------------------------------------------------------------------------
# handle_pause_toggle_request
# ---------------------------------------------------------------------------

class TestHandlePauseToggleRequest(unittest.TestCase):
    """handle_pause_toggle_request: PAUSED -> pause again; else -> resume."""

    def test_toggle_when_paused_calls_pause(self):
        """When state==PAUSED the toggle should call pause (handle_pause_request)."""
        p = _make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PAUSED
        p.handle_pause_request = MagicMock()
        p.handle_resume_request = MagicMock()
        p.handle_pause_toggle_request(Message("ovos.common_play.play_pause"))
        p.handle_pause_request.assert_called_once()
        p.handle_resume_request.assert_not_called()

    def test_toggle_when_playing_calls_resume(self):
        """When state==PLAYING the toggle should call resume."""
        p = _make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PLAYING
        p.handle_pause_request = MagicMock()
        p.handle_resume_request = MagicMock()
        p.handle_pause_toggle_request(Message("ovos.common_play.play_pause"))
        p.handle_resume_request.assert_called_once()
        p.handle_pause_request.assert_not_called()


# ---------------------------------------------------------------------------
# handle_stop_request
# ---------------------------------------------------------------------------

class TestHandleStopRequest(unittest.TestCase):
    """handle_stop_request calls stop() then reset()."""

    def test_stop_emits_search_stop(self):
        p = _make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()
        emitted = []
        p._bus.emit = lambda m: emitted.append(m)
        p.handle_stop_request(Message("ovos.common_play.stop"))
        types = [m.msg_type for m in emitted]
        self.assertIn("ovos.common_play.search.stop", types)

    def test_stop_sets_state_stopped(self):
        p = _make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()
        p.handle_stop_request(Message("ovos.common_play.stop"))
        self.assertEqual(p.state, PlayerState.STOPPED)

    def test_stop_calls_audio_service(self):
        p = _make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()
        p.handle_stop_request(Message("ovos.common_play.stop"))
        p.audio_service.stop.assert_called()


# ---------------------------------------------------------------------------
# handle_next_request / handle_prev_request
# ---------------------------------------------------------------------------

class TestHandleNextPrevRequest(unittest.TestCase):
    """handle_next_request and handle_prev_request delegate to play_next/play_prev."""

    def test_next_delegates_to_play_next(self):
        p = _make_player()
        p.play_next = MagicMock()
        p.handle_next_request(Message("ovos.common_play.next"))
        p.play_next.assert_called_once()

    def test_prev_delegates_to_play_prev(self):
        p = _make_player()
        p.play_prev = MagicMock()
        p.handle_prev_request(Message("ovos.common_play.previous"))
        p.play_prev.assert_called_once()


# ---------------------------------------------------------------------------
# handle_seek_request
# ---------------------------------------------------------------------------

class TestHandleSeekRequest(unittest.TestCase):
    """handle_seek_request computes position from message data and calls seek()."""

    def test_seek_with_seconds_param(self):
        """'seconds' key is converted to milliseconds and passed to seek."""
        p = _make_player(PlaybackType.AUDIO)
        p.seek = MagicMock()
        p.now_playing.position = 0
        p.audio_service.get_track_position.return_value = 5000
        msg = Message("ovos.common_play.seek", {"seconds": 10})
        p.handle_seek_request(msg)
        # position = 5000 (from audio_service) + 10*1000
        p.seek.assert_called_once_with(15000)

    def test_seek_with_seek_value_param(self):
        """'seekValue' is used directly, ignoring 'seconds'."""
        p = _make_player(PlaybackType.AUDIO)
        p.seek = MagicMock()
        msg = Message("ovos.common_play.seek", {"seekValue": 30000})
        p.handle_seek_request(msg)
        p.seek.assert_called_once_with(30000)

    def test_seek_audio_calls_audio_service(self):
        """seek() with AUDIO type calls audio_service.set_track_position."""
        p = _make_player(PlaybackType.AUDIO)
        p.seek(60000)
        p.audio_service.set_track_position.assert_called_once_with(60.0)


# ---------------------------------------------------------------------------
# handle_shuffle_toggle_request / handle_set_shuffle / handle_unset_shuffle
# ---------------------------------------------------------------------------

class TestHandleShuffleRequests(unittest.TestCase):
    """Shuffle toggle and set/unset handlers update self.shuffle."""

    def test_shuffle_toggle_flips_false_to_true(self):
        p = _make_player()
        p.shuffle = False
        p._update_gui = MagicMock()
        p.handle_shuffle_toggle_request(Message("ovos.common_play.shuffle.toggle"))
        self.assertTrue(p.shuffle)

    def test_shuffle_toggle_flips_true_to_false(self):
        p = _make_player()
        p.shuffle = True
        p._update_gui = MagicMock()
        p.handle_shuffle_toggle_request(Message("ovos.common_play.shuffle.toggle"))
        self.assertFalse(p.shuffle)

    def test_set_shuffle_sets_true(self):
        p = _make_player()
        p.shuffle = False
        p._update_gui = MagicMock()
        p.handle_set_shuffle(Message("ovos.common_play.shuffle.set"))
        self.assertTrue(p.shuffle)

    def test_unset_shuffle_sets_false(self):
        p = _make_player()
        p.shuffle = True
        p._update_gui = MagicMock()
        p.handle_unset_shuffle(Message("ovos.common_play.shuffle.unset"))
        self.assertFalse(p.shuffle)

    def test_shuffle_toggle_calls_update_gui(self):
        p = _make_player()
        p._update_gui = MagicMock()
        p.handle_shuffle_toggle_request(Message("ovos.common_play.shuffle.toggle"))
        p._update_gui.assert_called_once()


# ---------------------------------------------------------------------------
# handle_repeat_toggle_request / handle_set_repeat / handle_unset_repeat
# ---------------------------------------------------------------------------

class TestHandleRepeatRequests(unittest.TestCase):
    """Repeat toggle and set/unset handlers update self.loop_state."""

    def test_repeat_toggle_from_none_to_repeat(self):
        p = _make_player()
        p.loop_state = LoopState.NONE
        p._update_gui = MagicMock()
        p.handle_repeat_toggle_request(Message("ovos.common_play.repeat.toggle"))
        self.assertEqual(p.loop_state, LoopState.REPEAT)

    def test_repeat_toggle_from_repeat_to_repeat_track(self):
        p = _make_player()
        p.loop_state = LoopState.REPEAT
        p._update_gui = MagicMock()
        p.handle_repeat_toggle_request(Message("ovos.common_play.repeat.toggle"))
        self.assertEqual(p.loop_state, LoopState.REPEAT_TRACK)

    def test_repeat_toggle_from_repeat_track_to_none(self):
        p = _make_player()
        p.loop_state = LoopState.REPEAT_TRACK
        p._update_gui = MagicMock()
        p.handle_repeat_toggle_request(Message("ovos.common_play.repeat.toggle"))
        self.assertEqual(p.loop_state, LoopState.NONE)

    def test_set_repeat_sets_loop_state(self):
        p = _make_player()
        p.loop_state = LoopState.NONE
        p._update_gui = MagicMock()
        p.handle_set_repeat(Message("ovos.common_play.repeat.set"))
        self.assertEqual(p.loop_state, LoopState.REPEAT)

    def test_unset_repeat_clears_loop_state(self):
        p = _make_player()
        p.loop_state = LoopState.REPEAT
        p._update_gui = MagicMock()
        p.handle_unset_repeat(Message("ovos.common_play.repeat.unset"))
        self.assertEqual(p.loop_state, LoopState.NONE)

    def test_repeat_toggle_calls_update_gui(self):
        p = _make_player()
        p._update_gui = MagicMock()
        p.handle_repeat_toggle_request(Message("ovos.common_play.repeat.toggle"))
        p._update_gui.assert_called_once()


# ---------------------------------------------------------------------------
# handle_playlist_set_request / handle_playlist_clear_request / handle_playlist_queue_request
# ---------------------------------------------------------------------------

class TestHandlePlaylistRequests(unittest.TestCase):
    """Playlist manipulation handlers."""

    def _make_player_real_playlist(self):
        """Return a player whose self.playlist is a real Playlist instance."""
        p = _make_player()
        p.playlist = Playlist()
        return p

    def test_playlist_clear_empties_playlist(self):
        p = self._make_player_real_playlist()
        p.handle_playlist_clear_request(Message("ovos.common_play.playlist.clear"))
        self.assertEqual(len(p.playlist), 0)

    def test_playlist_queue_adds_tracks(self):
        p = self._make_player_real_playlist()
        tracks = [
            {"uri": "http://a.com/1.mp3", "title": "Track 1"},
            {"uri": "http://a.com/2.mp3", "title": "Track 2"},
        ]
        msg = Message("ovos.common_play.playlist.queue", {"tracks": tracks})
        p.handle_playlist_queue_request(msg)
        self.assertEqual(len(p.playlist), 2)

    def test_playlist_set_replaces_existing(self):
        p = self._make_player_real_playlist()
        # Pre-populate playlist
        pre_msg = Message("ovos.common_play.playlist.queue",
                          {"tracks": [{"uri": "http://old.com/old.mp3", "title": "Old"}]})
        p.handle_playlist_queue_request(pre_msg)
        self.assertEqual(len(p.playlist), 1)
        # Now set with new tracks (should clear first, then add)
        new_tracks = [
            {"uri": "http://new.com/1.mp3", "title": "New 1"},
            {"uri": "http://new.com/2.mp3", "title": "New 2"},
        ]
        set_msg = Message("ovos.common_play.playlist.set", {"tracks": new_tracks})
        p.handle_playlist_set_request(set_msg)
        self.assertEqual(len(p.playlist), 2)


# ---------------------------------------------------------------------------
# handle_track_info_request
# ---------------------------------------------------------------------------

class TestHandleTrackInfoRequest(unittest.TestCase):
    """handle_track_info_request emits now_playing.as_dict as a response."""

    def test_track_info_response_contains_now_playing_dict(self):
        p = _make_player()
        emitted = []
        p._bus.emit = lambda m: emitted.append(m)
        msg = Message("ovos.common_play.track_info")
        p.handle_track_info_request(msg)
        self.assertTrue(len(emitted) >= 1)
        # response message should carry the as_dict() result
        resp = emitted[0]
        self.assertEqual(resp.data, p.now_playing.as_dict)


# ---------------------------------------------------------------------------
# handle_track_length_request / handle_track_position_request / handle_set_track_position_request
# ---------------------------------------------------------------------------

class TestHandleTrackLengthPositionRequests(unittest.TestCase):
    """Track length and position handlers emit responses with the correct data."""

    def test_track_length_response(self):
        p = _make_player(PlaybackType.AUDIO)
        p.now_playing.length = 200000
        p.audio_service.get_track_length.return_value = 300000
        emitted = []
        p._bus.emit = lambda m: emitted.append(m)
        msg = Message("ovos.common_play.get_track_length")
        p.handle_track_length_request(msg)
        resp = emitted[0]
        # Should prefer value from audio_service
        self.assertEqual(resp.data["length"], 300000)

    def test_track_length_falls_back_to_now_playing(self):
        p = _make_player(PlaybackType.AUDIO)
        p.now_playing.length = 150000
        p.audio_service.get_track_length.return_value = None
        emitted = []
        p._bus.emit = lambda m: emitted.append(m)
        msg = Message("ovos.common_play.get_track_length")
        p.handle_track_length_request(msg)
        resp = emitted[0]
        self.assertEqual(resp.data["length"], 150000)

    def test_track_position_response(self):
        p = _make_player(PlaybackType.AUDIO)
        p.now_playing.position = 5000
        p.audio_service.get_track_position.return_value = 8000
        emitted = []
        p._bus.emit = lambda m: emitted.append(m)
        msg = Message("ovos.common_play.get_track_position")
        p.handle_track_position_request(msg)
        resp = emitted[0]
        self.assertEqual(resp.data["position"], 8000)

    def test_set_track_position_calls_seek(self):
        p = _make_player(PlaybackType.AUDIO)
        p.seek = MagicMock()
        msg = Message("ovos.common_play.set_track_position", {"position": 20000})
        p.handle_set_track_position_request(msg)
        p.seek.assert_called_once_with(20000)


# ---------------------------------------------------------------------------
# handle_player_state_update (bus event handler)
# ---------------------------------------------------------------------------

class TestHandlePlayerStateUpdate(unittest.TestCase):
    """handle_player_state_update parses state from the message and updates self.state."""

    def test_updates_state_from_int(self):
        p = _make_player()
        p._update_gui = MagicMock()
        msg = Message("ovos.common_play.player.state",
                      {"state": int(PlayerState.PLAYING)})
        p.handle_player_state_update(msg)
        self.assertEqual(p.state, PlayerState.PLAYING)

    def test_updates_state_from_enum(self):
        p = _make_player()
        p._update_gui = MagicMock()
        msg = Message("ovos.common_play.player.state",
                      {"state": PlayerState.PAUSED})
        p.handle_player_state_update(msg)
        self.assertEqual(p.state, PlayerState.PAUSED)

    def test_raises_on_missing_state(self):
        p = _make_player()
        msg = Message("ovos.common_play.player.state", {})
        with self.assertRaises(ValueError):
            p.handle_player_state_update(msg)

    def test_raises_on_invalid_state_type(self):
        p = _make_player()
        msg = Message("ovos.common_play.player.state", {"state": "playing"})
        with self.assertRaises(ValueError):
            p.handle_player_state_update(msg)

    def test_noop_when_same_state(self):
        p = _make_player()
        p._update_gui = MagicMock()
        p.state = PlayerState.STOPPED
        msg = Message("ovos.common_play.player.state",
                      {"state": PlayerState.STOPPED})
        p.handle_player_state_update(msg)
        p._update_gui.assert_not_called()


# ---------------------------------------------------------------------------
# handle_player_media_update (bus event handler)
# ---------------------------------------------------------------------------

class TestHandlePlayerMediaUpdate(unittest.TestCase):
    """handle_player_media_update parses MediaState from message and updates self.media_state."""

    def test_updates_media_state_from_int(self):
        p = _make_player()
        p._update_gui = MagicMock()
        msg = Message("ovos.common_play.media.state",
                      {"state": int(MediaState.LOADED_MEDIA)})
        p.handle_player_media_update(msg)
        self.assertEqual(p.media_state, MediaState.LOADED_MEDIA)

    def test_raises_on_missing_state(self):
        p = _make_player()
        msg = Message("ovos.common_play.media.state", {})
        with self.assertRaises(ValueError):
            p.handle_player_media_update(msg)

    def test_raises_on_invalid_state_type(self):
        p = _make_player()
        msg = Message("ovos.common_play.media.state", {"state": "loaded"})
        with self.assertRaises(ValueError):
            p.handle_player_media_update(msg)

    def test_noop_when_same_state(self):
        p = _make_player()
        p._update_gui = MagicMock()
        p.media_state = MediaState.NO_MEDIA
        msg = Message("ovos.common_play.media.state",
                      {"state": MediaState.NO_MEDIA})
        p.handle_player_media_update(msg)
        p._update_gui.assert_not_called()


# ---------------------------------------------------------------------------
# set_media_state
# ---------------------------------------------------------------------------

class TestSetMediaState(unittest.TestCase):
    """set_media_state updates self.media_state and emits a bus event."""

    def test_raises_on_wrong_type(self):
        p = _make_player()
        with self.assertRaises(TypeError):
            p.set_media_state("loaded")

    def test_updates_media_state(self):
        p = _make_player()
        p.set_media_state(MediaState.BUFFERED_MEDIA)
        self.assertEqual(p.media_state, MediaState.BUFFERED_MEDIA)

    def test_emits_bus_event_on_change(self):
        p = _make_player()
        emitted = []
        p._bus.emit = lambda m: emitted.append(m)
        p.set_media_state(MediaState.LOADED_MEDIA)
        types = [m.msg_type for m in emitted]
        self.assertIn("ovos.common_play.media.state", types)

    def test_noop_when_same_state(self):
        p = _make_player()
        p._bus.emit = MagicMock()
        p.set_media_state(MediaState.NO_MEDIA)
        p._bus.emit.assert_not_called()


# ---------------------------------------------------------------------------
# NowPlaying.as_dict serialization
# ---------------------------------------------------------------------------

class TestNowPlayingAsDict(unittest.TestCase):
    """NowPlaying.as_dict returns the expected keys."""

    def _make_now_playing(self):
        """Create a real NowPlaying instance with a fake bus."""
        from ovos_media.player import NowPlaying
        bus = FakeBus()
        with patch("ovos_media.player.load_stream_extractors"):
            np = NowPlaying(bus)
        return np

    def test_as_dict_has_required_keys(self):
        np = self._make_now_playing()
        d = np.as_dict
        for key in ("uri", "title", "artist", "image", "playback",
                    "status", "media_type", "length", "skill_id", "skill_icon"):
            self.assertIn(key, d, f"Missing key: {key}")

    def test_as_dict_reflects_updates(self):
        np = self._make_now_playing()
        np.title = "My Song"
        np.artist = "My Artist"
        d = np.as_dict
        self.assertEqual(d["title"], "My Song")
        self.assertEqual(d["artist"], "My Artist")


# ---------------------------------------------------------------------------
# handle_like / handle_unlike
# ---------------------------------------------------------------------------

class TestHandleLikeUnlike(unittest.TestCase):
    """handle_like and handle_unlike update media.liked_songs."""

    def test_like_stores_track(self):
        p = _make_player()
        p._update_gui = MagicMock()
        p._bus.emit = MagicMock()
        uri = "http://example.com/song.mp3"
        msg = Message("ovos.common_play.like", {"uri": uri, "title": "Song", "artist": "Artist", "image": ""})
        p.handle_like(msg)
        p.media.liked_songs.__setitem__.assert_called()
        p.media.liked_songs.store.assert_called_once()

    def test_unlike_removes_track_when_present(self):
        p = _make_player()
        uri = "http://example.com/song.mp3"
        p.now_playing.original_uri = uri
        p.media.liked_songs.__contains__ = MagicMock(return_value=True)
        msg = Message("ovos.common_play.unlike", {"uri": uri})
        p.handle_unlike(msg)
        p.media.liked_songs.pop.assert_called_once_with(uri)
        p.media.liked_songs.store.assert_called_once()

    def test_unlike_noop_when_not_present(self):
        p = _make_player()
        uri = "http://example.com/song.mp3"
        p.media.liked_songs.__contains__ = MagicMock(return_value=False)
        msg = Message("ovos.common_play.unlike", {"uri": uri})
        p.handle_unlike(msg)
        p.media.liked_songs.pop.assert_not_called()


# ---------------------------------------------------------------------------
# pause() with VIDEO playback type
# ---------------------------------------------------------------------------

class TestPauseVideoPlayback(unittest.TestCase):
    """pause() should call video_service.pause for VIDEO playback."""

    def test_pause_calls_video_service(self):
        p = _make_player(PlaybackType.VIDEO)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()
        p.pause()
        p.video_service.pause.assert_called_once()

    def test_pause_does_not_call_audio_service_for_video(self):
        p = _make_player(PlaybackType.VIDEO)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()
        p.pause()
        p.audio_service.pause.assert_not_called()


# ---------------------------------------------------------------------------
# resume() with VIDEO playback type
# ---------------------------------------------------------------------------

class TestResumeVideoPlayback(unittest.TestCase):
    """resume() should call video_service.resume for VIDEO playback."""

    def test_resume_calls_video_service(self):
        p = _make_player(PlaybackType.VIDEO)
        p.state = PlayerState.PAUSED
        p.handle_status = MagicMock()
        p.resume()
        p.video_service.resume.assert_called_once()


# ---------------------------------------------------------------------------
# stop() with various playback types
# ---------------------------------------------------------------------------

class TestStopVariousPlaybackTypes(unittest.TestCase):
    """stop() routes to the correct service depending on playback type."""

    def test_stop_video_calls_video_service(self):
        p = _make_player(PlaybackType.VIDEO)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()
        p.stop()
        p.video_service.stop.assert_called()

    def test_stop_web_calls_web_service(self):
        p = _make_player(PlaybackType.WEBVIEW)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()
        p.stop()
        p.web_service.stop.assert_called()

    def test_stop_skill_emits_skill_stop_message(self):
        p = _make_player(PlaybackType.SKILL)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()
        emitted = []
        p._bus.emit = lambda m: emitted.append(m)
        p.stop()
        skill_stop_msgs = [m for m in emitted
                           if m.msg_type == f"ovos.common_play.{p.now_playing.skill_id}.stop"]
        self.assertTrue(len(skill_stop_msgs) >= 1)


# ---------------------------------------------------------------------------
# handle_play_request — sets loop_state when repeat=True
# ---------------------------------------------------------------------------

class TestHandlePlayRequest(unittest.TestCase):
    """handle_play_request sets loop_state from repeat flag before delegating."""

    def test_play_request_sets_repeat_loop_state(self):
        p = _make_player()
        p.play_media = MagicMock()
        media = {"uri": "http://example.com/t.mp3", "title": "T",
                 "playback": PlaybackType.AUDIO}
        msg = Message("ovos.common_play.play",
                      {"media": media, "repeat": True})
        p.handle_play_request(msg)
        self.assertEqual(p.loop_state, LoopState.REPEAT)

    def test_play_request_delegates_to_play_media(self):
        p = _make_player()
        p.play_media = MagicMock()
        media = {"uri": "http://example.com/t.mp3", "title": "T",
                 "playback": PlaybackType.AUDIO}
        msg = Message("ovos.common_play.play", {"media": media})
        p.handle_play_request(msg)
        p.play_media.assert_called_once()


if __name__ == "__main__":
    unittest.main()

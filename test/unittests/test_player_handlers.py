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
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import (
    PlayerState,
    MediaState,
    LoopState,
    PlaybackType,
    TrackState,
    MediaEntry,
    Playlist,
)

from player_fixture import make_player


# ---------------------------------------------------------------------------
# Shared factory — mirrors the pattern in test_player.py / test_player_state.py
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# handle_pause_request
# ---------------------------------------------------------------------------

class TestHandlePauseRequest(unittest.TestCase):
    """handle_pause_request delegates to pause()."""

    def test_pause_calls_audio_service(self):
        p = make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()
        p.handle_pause_request(Message("ovos.common_play.pause"))
        p.audio_service.pause.assert_called_once()

    def test_pause_sets_player_state_paused(self):
        p = make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()
        p.handle_pause_request(Message("ovos.common_play.pause"))
        self.assertEqual(p.state, PlayerState.PAUSED)

    def test_pause_clears_paused_on_duck_flag(self):
        p = make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PLAYING
        p._paused_on_duck = True
        p.handle_status = MagicMock()
        p.handle_pause_request(Message("ovos.common_play.pause"))
        self.assertFalse(p._paused_on_duck)


# ---------------------------------------------------------------------------
# handle_resume_request
# ---------------------------------------------------------------------------

class TestHandleResumeRequest(unittest.TestCase):
    """handle_resume_request delegates to resume()."""

    def test_resume_calls_audio_service(self):
        p = make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PAUSED
        p.handle_status = MagicMock()
        p.handle_resume_request(Message("ovos.common_play.resume"))
        p.audio_service.resume.assert_called_once()

    def test_resume_sets_player_state_playing(self):
        p = make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PAUSED
        p.handle_status = MagicMock()
        p.handle_resume_request(Message("ovos.common_play.resume"))
        self.assertEqual(p.state, PlayerState.PLAYING)


# ---------------------------------------------------------------------------
# handle_pause_toggle_request
# ---------------------------------------------------------------------------

class TestHandlePauseToggleRequest(unittest.TestCase):
    """handle_pause_toggle_request: PAUSED -> resume; else -> pause."""

    def test_toggle_when_paused_calls_resume(self):
        """When state==PAUSED the toggle should call resume (handle_resume_request)."""
        p = make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PAUSED
        p.handle_pause_request = MagicMock()
        p.handle_resume_request = MagicMock()
        p.handle_pause_toggle_request(Message("ovos.common_play.play_pause"))
        p.handle_resume_request.assert_called_once()
        p.handle_pause_request.assert_not_called()

    def test_toggle_when_playing_calls_pause(self):
        """When state==PLAYING the toggle should call pause."""
        p = make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PLAYING
        p.handle_pause_request = MagicMock()
        p.handle_resume_request = MagicMock()
        p.handle_pause_toggle_request(Message("ovos.common_play.play_pause"))
        p.handle_pause_request.assert_called_once()
        p.handle_resume_request.assert_not_called()


# ---------------------------------------------------------------------------
# handle_stop_request
# ---------------------------------------------------------------------------

class TestHandleStopRequest(unittest.TestCase):
    """handle_stop_request calls stop() then reset()."""

    def test_stop_emits_search_stop(self):
        p = make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()
        emitted = []
        p.bus.emit = lambda m: emitted.append(m)
        p.handle_stop_request(Message("ovos.common_play.stop"))
        types = [m.msg_type for m in emitted]
        self.assertIn("ovos.common_play.search.stop", types)

    def test_stop_sets_state_stopped(self):
        p = make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()
        p.handle_stop_request(Message("ovos.common_play.stop"))
        self.assertEqual(p.state, PlayerState.STOPPED)

    def test_stop_calls_audio_service(self):
        p = make_player(PlaybackType.AUDIO)
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
        p = make_player()
        p.play_next = MagicMock()
        p.handle_next_request(Message("ovos.common_play.next"))
        p.play_next.assert_called_once()

    def test_prev_delegates_to_play_prev(self):
        p = make_player()
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
        p = make_player(PlaybackType.AUDIO)
        p.seek = MagicMock()
        p.now_playing.position = 0
        p.audio_service.get_track_position.return_value = 5000
        msg = Message("ovos.common_play.seek", {"seconds": 10})
        p.handle_seek_request(msg)
        # position = 5000 (from audio_service) + 10*1000
        p.seek.assert_called_once_with(15000)

    def test_seek_with_seek_value_param(self):
        """'seekValue' is used directly, ignoring 'seconds'."""
        p = make_player(PlaybackType.AUDIO)
        p.seek = MagicMock()
        msg = Message("ovos.common_play.seek", {"seekValue": 30000})
        p.handle_seek_request(msg)
        p.seek.assert_called_once_with(30000)

    def test_seek_audio_calls_audio_service(self):
        """seek() with AUDIO type calls audio_service.set_track_position
        with the position in milliseconds, unconverted (matching the
        milliseconds contract documented on seek() and on
        MediaBackend.set_track_position)."""
        p = make_player(PlaybackType.AUDIO)
        p.seek(60000)
        p.audio_service.set_track_position.assert_called_once_with(60000)

    def test_seek_video_calls_video_service(self):
        """A VIDEO seek (eg. GUI seekbar drag, "skip forward 30s" on a
        video skill) must reach video_service.set_track_position, not
        evaporate silently."""
        p = make_player(PlaybackType.VIDEO)
        p.seek(60000)
        p.video_service.set_track_position.assert_called_once_with(60000)
        p.audio_service.set_track_position.assert_not_called()

    def test_seek_skill_logs_warning_and_does_not_raise(self):
        p = make_player(PlaybackType.SKILL)
        with patch("ovos_media.player.LOG") as mock_log:
            p.seek(60000)
            mock_log.warning.assert_called_once()
        p.audio_service.set_track_position.assert_not_called()
        p.video_service.set_track_position.assert_not_called()

    def test_seek_mpris_logs_warning_and_does_not_raise(self):
        p = make_player(PlaybackType.MPRIS)
        with patch("ovos_media.player.LOG") as mock_log:
            p.seek(60000)
            mock_log.warning.assert_called_once()
        p.audio_service.set_track_position.assert_not_called()
        p.video_service.set_track_position.assert_not_called()

    def test_seek_with_non_numeric_seconds_is_ignored(self):
        """A non-numeric 'seconds' payload must not raise TypeError from the
        `* 1000` multiplication — it should be logged and ignored instead."""
        p = make_player(PlaybackType.AUDIO)
        p.seek = MagicMock()
        msg = Message("ovos.common_play.seek", {"seconds": "not-a-number"})
        p.handle_seek_request(msg)
        p.seek.assert_not_called()


# ---------------------------------------------------------------------------
# handle_shuffle_toggle_request / handle_set_shuffle / handle_unset_shuffle
# ---------------------------------------------------------------------------

class TestHandleShuffleRequests(unittest.TestCase):
    """Shuffle toggle and set/unset handlers update self.shuffle."""

    def test_shuffle_toggle_flips_false_to_true(self):
        p = make_player()
        p.shuffle = False
        p.handle_shuffle_toggle_request(Message("ovos.common_play.shuffle.toggle"))
        self.assertTrue(p.shuffle)

    def test_shuffle_toggle_flips_true_to_false(self):
        p = make_player()
        p.shuffle = True
        p.handle_shuffle_toggle_request(Message("ovos.common_play.shuffle.toggle"))
        self.assertFalse(p.shuffle)

    def test_set_shuffle_sets_true(self):
        p = make_player()
        p.shuffle = False
        p.handle_set_shuffle(Message("ovos.common_play.shuffle.set"))
        self.assertTrue(p.shuffle)

    def test_unset_shuffle_sets_false(self):
        p = make_player()
        p.shuffle = True
        p.handle_unset_shuffle(Message("ovos.common_play.shuffle.unset"))
        self.assertFalse(p.shuffle)



# ---------------------------------------------------------------------------
# handle_repeat_toggle_request / handle_set_repeat / handle_unset_repeat
# ---------------------------------------------------------------------------

class TestHandleRepeatRequests(unittest.TestCase):
    """Repeat toggle and set/unset handlers update self.loop_state."""

    def test_repeat_toggle_from_none_to_repeat(self):
        p = make_player()
        p.loop_state = LoopState.NONE
        p.handle_repeat_toggle_request(Message("ovos.common_play.repeat.toggle"))
        self.assertEqual(p.loop_state, LoopState.REPEAT)

    def test_repeat_toggle_from_repeat_to_repeat_track(self):
        p = make_player()
        p.loop_state = LoopState.REPEAT
        p.handle_repeat_toggle_request(Message("ovos.common_play.repeat.toggle"))
        self.assertEqual(p.loop_state, LoopState.REPEAT_TRACK)

    def test_repeat_toggle_from_repeat_track_to_none(self):
        p = make_player()
        p.loop_state = LoopState.REPEAT_TRACK
        p.handle_repeat_toggle_request(Message("ovos.common_play.repeat.toggle"))
        self.assertEqual(p.loop_state, LoopState.NONE)

    def test_set_repeat_sets_loop_state(self):
        p = make_player()
        p.loop_state = LoopState.NONE
        p.handle_set_repeat(Message("ovos.common_play.repeat.set"))
        self.assertEqual(p.loop_state, LoopState.REPEAT)

    def test_unset_repeat_clears_loop_state(self):
        p = make_player()
        p.loop_state = LoopState.REPEAT
        p.handle_unset_repeat(Message("ovos.common_play.repeat.unset"))
        self.assertEqual(p.loop_state, LoopState.NONE)



# ---------------------------------------------------------------------------
# handle_playlist_set_request / handle_playlist_clear_request / handle_playlist_queue_request
# ---------------------------------------------------------------------------

class TestHandlePlaylistRequests(unittest.TestCase):
    """Playlist manipulation handlers."""

    def _make_player_real_playlist(self):
        """Return a player whose self.playlist is a real Playlist instance."""
        p = make_player()
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
        p = make_player()
        emitted = []
        p.bus.emit = lambda m: emitted.append(m)
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
        p = make_player(PlaybackType.AUDIO)
        p.now_playing.length = 200000
        p.audio_service.get_track_length.return_value = 300000
        emitted = []
        p.bus.emit = lambda m: emitted.append(m)
        msg = Message("ovos.common_play.get_track_length")
        p.handle_track_length_request(msg)
        resp = emitted[0]
        # Should prefer value from audio_service
        self.assertEqual(resp.data["length"], 300000)

    def test_track_length_falls_back_to_now_playing(self):
        p = make_player(PlaybackType.AUDIO)
        p.now_playing.length = 150000
        p.audio_service.get_track_length.return_value = None
        emitted = []
        p.bus.emit = lambda m: emitted.append(m)
        msg = Message("ovos.common_play.get_track_length")
        p.handle_track_length_request(msg)
        resp = emitted[0]
        self.assertEqual(resp.data["length"], 150000)

    def test_track_position_response(self):
        p = make_player(PlaybackType.AUDIO)
        p.now_playing.position = 5000
        p.audio_service.get_track_position.return_value = 8000
        emitted = []
        p.bus.emit = lambda m: emitted.append(m)
        msg = Message("ovos.common_play.get_track_position")
        p.handle_track_position_request(msg)
        resp = emitted[0]
        self.assertEqual(resp.data["position"], 8000)

    def test_set_track_position_calls_seek(self):
        p = make_player(PlaybackType.AUDIO)
        p.seek = MagicMock()
        msg = Message("ovos.common_play.set_track_position", {"position": 20000})
        p.handle_set_track_position_request(msg)
        p.seek.assert_called_once_with(20000)

    def test_set_track_position_with_none_is_not_forwarded(self):
        """A missing/None 'position' must not be forwarded to seek() (which
        forwards it straight to the backend's set_track_position)."""
        p = make_player(PlaybackType.AUDIO)
        p.seek = MagicMock()
        msg = Message("ovos.common_play.set_track_position", {"position": None})
        p.handle_set_track_position_request(msg)
        p.seek.assert_not_called()


# ---------------------------------------------------------------------------
# handle_player_state_update (bus event handler)
# ---------------------------------------------------------------------------

class TestSetPlayerState(unittest.TestCase):
    """set_player_state is the single authoritative writer of self.state.

    The old handle_player_state_update bus handler has been removed —
    OCPMediaPlayer no longer self-subscribes to its own emitted event.
    """

    def test_updates_state_to_playing(self):
        p = make_player()
        p.handle_status = MagicMock()
        p.state = PlayerState.STOPPED
        p.set_player_state(PlayerState.PLAYING)
        self.assertEqual(p.state, PlayerState.PLAYING)

    def test_updates_state_to_paused(self):
        p = make_player()
        p.handle_status = MagicMock()
        p.state = PlayerState.PLAYING
        p.set_player_state(PlayerState.PAUSED)
        self.assertEqual(p.state, PlayerState.PAUSED)

    def test_raises_on_invalid_type(self):
        p = make_player()
        with self.assertRaises(TypeError):
            p.set_player_state("playing")

    def test_noop_when_same_state(self):
        p = make_player()
        p.handle_status = MagicMock()
        p.state = PlayerState.STOPPED
        p.set_player_state(PlayerState.STOPPED)
        p.handle_status.assert_not_called()

    def test_calls_handle_status_on_change(self):
        p = make_player()
        p.handle_status = MagicMock()
        p.state = PlayerState.STOPPED
        p.set_player_state(PlayerState.PLAYING)
        p.handle_status.assert_called_once()


# ---------------------------------------------------------------------------
# handle_player_media_update (bus event handler)
# ---------------------------------------------------------------------------

class TestHandlePlayerMediaUpdate(unittest.TestCase):
    """handle_player_media_update parses MediaState from message and updates self.media_state."""

    def test_updates_media_state_from_int(self):
        p = make_player()
        msg = Message("ovos.common_play.media.state",
                      {"state": int(MediaState.LOADED_MEDIA)})
        p.handle_player_media_update(msg)
        self.assertEqual(p.media_state, MediaState.LOADED_MEDIA)

    def test_raises_on_missing_state(self):
        p = make_player()
        msg = Message("ovos.common_play.media.state", {})
        with self.assertRaises(ValueError):
            p.handle_player_media_update(msg)

    def test_raises_on_invalid_state_type(self):
        p = make_player()
        msg = Message("ovos.common_play.media.state", {"state": "loaded"})
        with self.assertRaises(ValueError):
            p.handle_player_media_update(msg)

    def test_noop_when_same_state(self):
        p = make_player()
        p.media_state = MediaState.NO_MEDIA
        p.handle_playback_ended = MagicMock()
        msg = Message("ovos.common_play.media.state",
                      {"state": MediaState.NO_MEDIA})
        p.handle_player_media_update(msg)
        p.handle_playback_ended.assert_not_called()


# ---------------------------------------------------------------------------
# set_media_state
# ---------------------------------------------------------------------------

class TestSetMediaState(unittest.TestCase):
    """set_media_state updates self.media_state and emits a bus event."""

    def test_raises_on_wrong_type(self):
        p = make_player()
        with self.assertRaises(TypeError):
            p.set_media_state("loaded")

    def test_updates_media_state(self):
        p = make_player()
        p.set_media_state(MediaState.BUFFERED_MEDIA)
        self.assertEqual(p.media_state, MediaState.BUFFERED_MEDIA)

    def test_emits_bus_event_on_change(self):
        p = make_player()
        emitted = []
        p.bus.emit = lambda m: emitted.append(m)
        p.set_media_state(MediaState.LOADED_MEDIA)
        types = [m.msg_type for m in emitted]
        self.assertIn("ovos.common_play.media.state", types)

    def test_noop_when_same_state(self):
        p = make_player()
        p.bus.emit = MagicMock()
        p.set_media_state(MediaState.NO_MEDIA)
        p.bus.emit.assert_not_called()


# ---------------------------------------------------------------------------
# NowPlaying.as_dict serialization
# ---------------------------------------------------------------------------

class TestNowPlayingAsDict(unittest.TestCase):
    """NowPlaying.as_dict returns the expected keys."""

    def _make_now_playing(self):
        """Create a real NowPlaying instance with a fake bus."""
        from ovos_media.player import NowPlaying
        bus = FakeBus()
        with patch("ovos_media.player.now_playing.load_stream_extractors"):
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
    """handle_like and handle_unlike write through the liked-songs store."""

    def test_like_stores_track(self):
        p = make_player()
        p.bus.emit = MagicMock()
        uri = "http://example.com/song.mp3"
        msg = Message("ovos.common_play.like", {"uri": uri, "title": "Song", "artist": "Artist", "image": ""})
        p.handle_like(msg)
        p.media.likes.like.assert_called_once_with(uri, title="Song",
                                                   artist="Artist", image="")

    def test_unlike_removes_track(self):
        p = make_player()
        uri = "http://example.com/song.mp3"
        p.now_playing.original_uri = uri
        msg = Message("ovos.common_play.unlike", {"uri": uri})
        p.handle_unlike(msg)
        p.media.likes.unlike.assert_called_once_with(uri)

    def test_unlike_falls_back_to_the_now_playing_uri(self):
        p = make_player()
        p.now_playing.original_uri = "http://example.com/current.mp3"
        p.handle_unlike(Message("ovos.common_play.unlike", {}))
        p.media.likes.unlike.assert_called_once_with(
            "http://example.com/current.mp3")


# ---------------------------------------------------------------------------
# pause() with VIDEO playback type
# ---------------------------------------------------------------------------

class TestPauseVideoPlayback(unittest.TestCase):
    """pause() should call video_service.pause for VIDEO playback."""

    def test_pause_calls_video_service(self):
        p = make_player(PlaybackType.VIDEO)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()
        p.pause()
        p.video_service.pause.assert_called_once()

    def test_pause_does_not_call_audio_service_for_video(self):
        p = make_player(PlaybackType.VIDEO)
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
        p = make_player(PlaybackType.VIDEO)
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
        p = make_player(PlaybackType.VIDEO)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()
        p.stop()
        p.video_service.stop.assert_called()

    def test_stop_web_calls_web_service(self):
        p = make_player(PlaybackType.WEBVIEW)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()
        p.stop()
        p.web_service.stop.assert_called()

    def test_stop_skill_emits_skill_stop_message(self):
        p = make_player(PlaybackType.SKILL)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()
        emitted = []
        p.bus.emit = lambda m: emitted.append(m)
        p.stop()
        skill_stop_msgs = [m for m in emitted
                           if m.msg_type == f"ovos.common_play.{p.now_playing.skill_id}.stop"]
        self.assertTrue(len(skill_stop_msgs) >= 1)


# ---------------------------------------------------------------------------
# UNDEFINED playback type — "nothing is loaded, make sure nothing is playing"
# ---------------------------------------------------------------------------

class TestUndefinedPlaybackFanOut(unittest.TestCase):
    """With no media loaded, pause and stop must reach every player that
    could still be holding audio, not just the audio one."""

    def _skill_topic(self, p, verb):
        return f"ovos.common_play.{p.now_playing.skill_id}.{verb}"

    def test_pause_reaches_audio_video_and_the_skill(self):
        p = make_player(PlaybackType.UNDEFINED)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()
        emitted = []
        p.bus.emit = lambda m: emitted.append(m.msg_type)
        p.pause()
        p.audio_service.pause.assert_called_once()
        p.video_service.pause.assert_called_once()
        self.assertIn(self._skill_topic(p, "pause"), emitted)

    def test_resume_reaches_audio_and_the_skill_but_not_video(self):
        p = make_player(PlaybackType.UNDEFINED)
        p.state = PlayerState.PAUSED
        p.handle_status = MagicMock()
        emitted = []
        p.bus.emit = lambda m: emitted.append(m.msg_type)
        p.resume()
        p.audio_service.resume.assert_called_once()
        p.video_service.resume.assert_not_called()
        self.assertIn(self._skill_topic(p, "resume"), emitted)

    def test_stop_reaches_every_player_in_order(self):
        p = make_player(PlaybackType.UNDEFINED)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()
        manager = MagicMock()
        p.audio_service.stop = manager.audio_stop
        p.video_service.stop = manager.video_stop
        p.web_service.stop = manager.web_stop
        skill_stop = self._skill_topic(p, "stop")

        def emit(message):
            if message.msg_type == skill_stop:
                manager.skill_stop()

        p.bus.emit = emit
        p.stop()
        self.assertEqual([name for name, _, _ in manager.mock_calls],
                         ["audio_stop", "skill_stop", "video_stop", "web_stop"])


# ---------------------------------------------------------------------------
# handle_play_request — sets loop_state when repeat=True
# ---------------------------------------------------------------------------

class TestHandlePlayRequest(unittest.TestCase):
    """handle_play_request sets loop_state from repeat flag before delegating."""

    def test_play_request_sets_repeat_loop_state(self):
        p = make_player()
        p.play_media = MagicMock()
        media = {"uri": "http://example.com/t.mp3", "title": "T",
                 "playback": PlaybackType.AUDIO}
        msg = Message("ovos.common_play.play",
                      {"media": media, "repeat": True})
        p.handle_play_request(msg)
        self.assertEqual(p.loop_state, LoopState.REPEAT)

    def test_play_request_delegates_to_play_media(self):
        p = make_player()
        p.play_media = MagicMock()
        media = {"uri": "http://example.com/t.mp3", "title": "T",
                 "playback": PlaybackType.AUDIO}
        msg = Message("ovos.common_play.play", {"media": media})
        p.handle_play_request(msg)
        p.play_media.assert_called_once()



# ---------------------------------------------------------------------------
# handle_mpris_now_playing / set_external_now_playing
# ---------------------------------------------------------------------------

class TestExternalMprisNowPlaying(unittest.TestCase):
    """An external MPRIS player is reflected as OCP now_playing with NO local
    backend playback (PlaybackType.MPRIS)."""

    def _player(self):
        p = make_player(PlaybackType.AUDIO)
        p.set_now_playing = MagicMock()
        p.handle_status = MagicMock()
        return p

    def test_playing_reflects_and_sets_playing(self):
        p = self._player()
        p.handle_mpris_now_playing(Message(
            "ovos.common_play.mpris.now_playing",
            {"external_player": "org.mpris.MediaPlayer2.spotify",
             "title": "Song", "artist": "Artist",
             "uri": "spotify:track:1", "state": "Playing"}))
        self.assertEqual(p.playback_type, PlaybackType.MPRIS)
        self.assertEqual(p.active_skill, "org.mpris.MediaPlayer2.spotify")
        self.assertEqual(p.state, PlayerState.PLAYING)
        # metadata reflected as an MPRIS track
        data = p.set_now_playing.call_args[0][0]
        self.assertEqual(data["playback"], PlaybackType.MPRIS)
        self.assertEqual(data["status"], TrackState.PLAYING_MPRIS)
        self.assertEqual(data["skill_id"], "org.mpris.MediaPlayer2.spotify")
        # NO local backend invoked
        p.audio_service.play.assert_not_called()
        p.video_service.play.assert_not_called()

    def test_paused_sets_paused(self):
        p = self._player()
        p.handle_mpris_now_playing(Message("x", {
            "external_player": "vlc", "state": "Paused"}))
        self.assertEqual(p.state, PlayerState.PAUSED)
        p.audio_service.play.assert_not_called()

    def test_stopped_sets_stopped(self):
        p = self._player()
        p.handle_mpris_now_playing(Message("x", {
            "external_player": "vlc", "state": "Stopped"}))
        self.assertEqual(p.state, PlayerState.STOPPED)

    def test_no_player_id_is_ignored(self):
        p = self._player()
        p.handle_mpris_now_playing(Message("x", {"title": "no id"}))
        p.set_now_playing.assert_not_called()

    def test_new_external_player_triggers_takeover(self):
        """A new external player starting playback stops OCP's own backends."""
        p = self._player()
        p.handle_MPRIS_takeover = MagicMock()
        p.playback_type = PlaybackType.AUDIO  # OCP was playing its own audio
        p.active_skill = "some.ocp.skill"
        p.handle_mpris_now_playing(Message("x", {
            "external_player": "org.mpris.MediaPlayer2.spotify", "state": "Playing"}))
        p.handle_MPRIS_takeover.assert_called_once()

    def test_same_external_player_does_not_repeat_takeover(self):
        """Metadata/position updates from the already-active external player must
        not re-trigger the takeover (which would stop it)."""
        p = self._player()
        p.handle_MPRIS_takeover = MagicMock()
        p.playback_type = PlaybackType.MPRIS
        p.active_skill = "org.mpris.MediaPlayer2.spotify"
        p.handle_mpris_now_playing(Message("x", {
            "external_player": "org.mpris.MediaPlayer2.spotify", "state": "Playing"}))
        p.handle_MPRIS_takeover.assert_not_called()


class TestStopSkillAndMprisTakeover(unittest.TestCase):
    """stop_skill emits correct message; handle_MPRIS_takeover stops services."""

    def test_stop_skill_emits_message(self):
        p = make_player()
        emitted = []
        p.bus.emit = lambda m: emitted.append(m)
        p.stop_skill()
        types = [m.msg_type for m in emitted]
        self.assertIn(f"ovos.common_play.{p.active_skill}.stop", types)

    def test_handle_mpris_takeover_stops_all_services(self):
        p = make_player()
        p.handle_MPRIS_takeover()
        p.audio_service.stop.assert_called_once()
        p.video_service.stop.assert_called_once()
        p.web_service.stop.assert_called_once()

    def test_handle_mpris_takeover_clears_original_uri(self):
        p = make_player()
        p.now_playing.original_uri = "http://example.com/t.mp3"
        p.handle_MPRIS_takeover()
        self.assertEqual(p.now_playing.original_uri, "")


class TestPlayNext(unittest.TestCase):
    """play_next navigation logic."""

    def test_play_next_skill_emits_bus_message(self):
        p = make_player(PlaybackType.SKILL)
        emitted = []
        p.bus.emit = lambda m: emitted.append(m)
        p.play_next()
        skill_next = [m for m in emitted
                      if "next" in m.msg_type]
        self.assertTrue(len(skill_next) >= 1)

    def test_play_next_repeat_track_calls_play(self):
        p = make_player(PlaybackType.AUDIO)
        p.loop_state = LoopState.REPEAT_TRACK
        p.play = MagicMock()
        p.play_next()
        p.play.assert_called_once()

    def test_play_next_no_more_tracks_returns_without_play(self):
        """play_next does nothing when now_playing is the last entry in the queue."""
        p = make_player(PlaybackType.AUDIO)
        track_a = MediaEntry(uri="http://example.com/a.mp3", title="A", playback=PlaybackType.AUDIO)
        p.playlist.entries = [track_a]
        p.media.search_playlist.entries = []
        p.now_playing.uri = track_a.uri  # at the only/last track
        p.loop_state = LoopState.NONE
        p.shuffle = False
        p.ocp_config = {"merge_search": False}
        p.play = MagicMock()
        p.play_next()
        p.play.assert_not_called()

    def test_play_next_advances_playlist(self):
        """play_next picks the entry after now_playing in the merged queue."""
        p = make_player(PlaybackType.AUDIO)
        track_a = MediaEntry(uri="http://example.com/a.mp3", title="A", playback=PlaybackType.AUDIO)
        track_b = MediaEntry(uri="http://example.com/b.mp3", title="B", playback=PlaybackType.AUDIO)
        p.playlist.entries = [track_a, track_b]
        p.media.search_playlist.entries = []
        p.now_playing.uri = track_a.uri
        p.loop_state = LoopState.NONE
        p.shuffle = False
        p.ocp_config = {"merge_search": False}
        p.play = MagicMock()
        p.set_now_playing = MagicMock()
        p.play_next()
        p.set_now_playing.assert_called_once_with(track_b)
        p.play.assert_called_once()

    def test_play_next_loop_repeat_wraps_to_start(self):
        """play_next with REPEAT restarts from the first queue entry."""
        p = make_player(PlaybackType.AUDIO)
        track_a = MediaEntry(uri="http://example.com/a.mp3", title="A", playback=PlaybackType.AUDIO)
        track_b = MediaEntry(uri="http://example.com/b.mp3", title="B", playback=PlaybackType.AUDIO)
        p.playlist.entries = [track_a, track_b]
        p.media.search_playlist.entries = []
        p.now_playing.uri = track_b.uri  # at end of queue
        p.loop_state = LoopState.REPEAT
        p.shuffle = False
        p.ocp_config = {"merge_search": False}
        p.play = MagicMock()
        p.set_now_playing = MagicMock()
        p.play_next()
        p.set_now_playing.assert_called_once_with(track_a)
        p.play.assert_called_once()


class TestPlayPrev(unittest.TestCase):
    """play_prev navigation logic."""

    def test_play_prev_skill_emits_bus_message(self):
        p = make_player(PlaybackType.SKILL)
        emitted = []
        p.bus.emit = lambda m: emitted.append(m)
        p.play_prev()
        prev_msgs = [m for m in emitted if "prev" in m.msg_type]
        self.assertTrue(len(prev_msgs) >= 1)

    def test_play_prev_goes_to_previous_track(self):
        """play_prev picks the entry before now_playing in the merged queue."""
        p = make_player(PlaybackType.AUDIO)
        track_a = MediaEntry(uri="http://example.com/a.mp3", title="A", playback=PlaybackType.AUDIO)
        track_b = MediaEntry(uri="http://example.com/b.mp3", title="B", playback=PlaybackType.AUDIO)
        p.playlist.entries = [track_a, track_b]
        p.media.search_playlist.entries = []
        p.now_playing.uri = track_b.uri  # currently on second track
        p.shuffle = False
        p.ocp_config = {"merge_search": False}
        p.play = MagicMock()
        p.set_now_playing = MagicMock()
        p.play_prev()
        p.set_now_playing.assert_called_once_with(track_a)
        p.play.assert_called_once()

    def test_play_prev_noop_at_first_track(self):
        """play_prev does nothing when now_playing is the first entry."""
        p = make_player(PlaybackType.AUDIO)
        track_a = MediaEntry(uri="http://example.com/a.mp3", title="A", playback=PlaybackType.AUDIO)
        p.playlist.entries = [track_a]
        p.media.search_playlist.entries = []
        p.now_playing.uri = track_a.uri  # at the first (only) track
        p.shuffle = False
        p.ocp_config = {"merge_search": False}
        p.play = MagicMock()
        p.play_prev()
        p.play.assert_not_called()

    def test_play_prev_undefined_does_not_emit_bus(self):
        # D5: UNDEFINED (the default playback_type) must NOT be treated as
        # a skill-controlled playback and defer to a bogus
        # ovos.common_play.<skill_id>.prev - mirrors play_next, which only
        # matches PlaybackType.SKILL. UNDEFINED falls through to the merged
        # queue logic instead (a no-op here: no queue entries beyond the
        # current one).
        p = make_player(PlaybackType.UNDEFINED)
        emitted = []
        p.bus.emit = lambda m: emitted.append(m)
        p.playlist.entries = []
        p.media.search_playlist.entries = []
        p.now_playing.uri = "http://example.com/track.mp3"
        p.shuffle = False
        p.ocp_config = {"merge_search": False}
        p.play_prev()
        prev_msgs = [m for m in emitted if "prev" in m.msg_type]
        self.assertEqual(prev_msgs, [])


class TestSetNowPlaying(unittest.TestCase):
    """set_now_playing updates now_playing and playlist."""

    def _make_fully_mocked_player(self, playback_type=PlaybackType.AUDIO):
        """Return a player with mocked playlist and now_playing."""
        p = make_player(playback_type)
        # now_playing is already a MagicMock — make playlist also a MagicMock
        # so goto_track won't enforce strict types
        return p

    def test_set_now_playing_with_media_entry_calls_reset(self):
        p = self._make_fully_mocked_player()
        p.handle_status = MagicMock()
        entry = MediaEntry(title="Track A",
                           uri="http://example.com/a.mp3",
                           playback=PlaybackType.AUDIO)
        p.set_now_playing(entry)
        p.now_playing.reset.assert_called_once()

    def test_set_now_playing_raises_on_invalid(self):
        p = make_player()
        with self.assertRaises(ValueError):
            p.set_now_playing("not a track")

    def test_set_now_playing_with_dict_converts_to_media_entry(self):
        p = self._make_fully_mocked_player()
        p.handle_status = MagicMock()
        p.set_now_playing({"title": "Dict Track",
                           "uri": "http://example.com/t.mp3",
                           "playback": PlaybackType.AUDIO})
        p.now_playing.reset.assert_called_once()

    def test_set_now_playing_updates_now_playing(self):
        p = self._make_fully_mocked_player()
        p.handle_status = MagicMock()
        entry = MediaEntry(title="My Song",
                           uri="http://example.com/song.mp3",
                           playback=PlaybackType.AUDIO)
        p.set_now_playing(entry)
        p.now_playing.update.assert_called()

    def test_set_now_playing_clears_mpris_playlist_when_switching(self):
        """When now_playing.playback is MPRIS and new track is not, playlist is cleared."""
        p = self._make_fully_mocked_player(PlaybackType.MPRIS)
        p.handle_status = MagicMock()
        entry = MediaEntry(title="Audio Track",
                           uri="http://example.com/audio.mp3",
                           playback=PlaybackType.AUDIO)
        p.set_now_playing(entry)
        p.playlist.clear.assert_called()

    def test_set_now_playing_with_playlist_object(self):
        """A Playlist as track replaces the queue."""
        p = self._make_fully_mocked_player()
        p.handle_status = MagicMock()
        pl = Playlist()
        entry = MediaEntry(title="Pl1", uri="http://example.com/pl1.mp3",
                           playback=PlaybackType.AUDIO)
        pl.add_entry(entry)
        p.set_now_playing(pl)
        p.playlist.clear.assert_called()

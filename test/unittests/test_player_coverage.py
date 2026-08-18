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
"""Additional coverage tests for player.py to push coverage to 65%+.

Targets uncovered regions:
- NowPlaying: reset, update, handle_track_state_change, handle_media_state_change,
  handle_sync_seekbar, handle_external_play, as_entry
- OCPMediaPlayer: play_media, play_next, play_prev, set_now_playing,
  handle_duck_request, handle_unduck_request, handle_cork_request,
  handle_uncork_request, handle_playback_ended, handle_invalid_media,
  handle_player_media_update (END_OF_MEDIA path), active_skill property,
  playback_type property, tracks property, can_prev, can_next,
  stop_skill, handle_MPRIS_takeover, reset
"""
import asyncio
import time
import unittest
from unittest.mock import MagicMock, patch, call

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import (
    PlayerState,
    MediaState,
    TrackState,
    LoopState,
    PlaybackType,
    MediaType,
    MediaEntry,
    Playlist,
)


# ---------------------------------------------------------------------------
# Shared factory — same pattern as the existing test files
# ---------------------------------------------------------------------------

def _make_player(playback_type: PlaybackType = PlaybackType.AUDIO):
    """Return a minimal OCPMediaPlayer with all external deps mocked.

    Args:
        playback_type: PlaybackType to assign to now_playing.

    Returns:
        OCPMediaPlayer instance with mocked services and FakeBus.
    """
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
        p.state = PlayerState.STOPPED
        p.loop_state = LoopState.NONE
        p.media_state = MediaState.NO_MEDIA
        p.shuffle = False
        p.track_history = {}
        p._paused_on_duck = False
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
        p.now_playing.media_type = MediaType.GENERIC
        p.now_playing.as_dict = {
            "uri": "http://example.com/track.mp3",
            "title": "Test Track",
            "artist": "Test Artist",
            "image": "",
        }
        p.playlist = MagicMock()
        p.playlist.as_list.return_value = []
        p.playlist.entries = []
        p.playlist.position = 0
        p.playlist.__len__ = lambda self: 0
        p.media = MagicMock()
        p.media.search_playlist.entries = []
        p.audio_service = MagicMock()
        p.audio_service.current = None
        p.video_service = MagicMock()
        p.video_service.current = None
        p.web_service = MagicMock()
        p.web_service.current = None
        p.current = None
        p.mpris = None
        p.bus = FakeBus()
        # __init__ normally sets these; OCPMediaPlayer.__new__ skips __init__
        p._last_playback_type = playback_type
        p._last_playback_uri = p.now_playing.uri
    return p


# ---------------------------------------------------------------------------
# NowPlaying tests
# ---------------------------------------------------------------------------

class TestNowPlayingInit(unittest.TestCase):
    """NowPlaying.__init__ creates instance with expected defaults."""

    def _make_now_playing(self):
        from ovos_media.player import NowPlaying
        bus = FakeBus()
        with patch("ovos_media.player.now_playing.load_stream_extractors"):
            np = NowPlaying(bus)
        return np, bus

    def test_position_defaults_to_zero(self):
        np, _ = self._make_now_playing()
        self.assertEqual(np.position, 0)

    def test_bus_attribute_set(self):
        np, bus = self._make_now_playing()
        self.assertIs(np.bus, bus)

    def test_original_uri_matches_uri(self):
        np, _ = self._make_now_playing()
        # uri defaults to empty string, original_uri mirrors it
        self.assertEqual(np.original_uri, np.uri)

    def test_as_entry_returns_media_entry(self):
        np, _ = self._make_now_playing()
        entry = np.as_entry()
        self.assertIsInstance(entry, MediaEntry)

    def test_reset_clears_fields(self):
        np, _ = self._make_now_playing()
        np.title = "Something"
        np.artist = "Someone"
        np.reset()
        self.assertEqual(np.title, "")
        self.assertEqual(np.artist, "")
        self.assertEqual(np.position, 0)

    def test_update_with_dict(self):
        np, _ = self._make_now_playing()
        np.update({"title": "New Title", "artist": "New Artist"})
        self.assertEqual(np.title, "New Title")

    def test_update_with_media_entry(self):
        np, _ = self._make_now_playing()
        entry = MediaEntry(title="Entry Title", artist="Entry Artist",
                           uri="http://example.com/a.mp3")
        np.update(entry)
        self.assertEqual(np.title, "Entry Title")

    def test_handle_sync_seekbar_updates_position(self):
        np, _ = self._make_now_playing()
        msg = Message("ovos.common_play.playback_time",
                      {"length": 180000, "position": 45000})
        np.handle_sync_seekbar(msg)
        self.assertEqual(np.position, 45000)
        self.assertEqual(np.length, 180000)

    def test_handle_sync_seekbar_missing_keys_does_not_raise(self):
        np, _ = self._make_now_playing()
        np.position = 1000
        np.length = 2000
        msg = Message("ovos.common_play.playback_time", {})
        np.handle_sync_seekbar(msg)  # must not raise KeyError
        # prior values are kept, not clobbered
        self.assertEqual(np.position, 1000)
        self.assertEqual(np.length, 2000)

    def test_handle_sync_seekbar_none_position_keeps_prior_value(self):
        np, _ = self._make_now_playing()
        np.position = 1000
        msg = Message("ovos.common_play.playback_time",
                      {"length": 180000, "position": None})
        np.handle_sync_seekbar(msg)
        self.assertEqual(np.position, 1000)
        self.assertEqual(np.length, 180000)

    def test_handle_sync_seekbar_str_position_keeps_prior_value(self):
        np, _ = self._make_now_playing()
        np.position = 1000
        msg = Message("ovos.common_play.playback_time",
                      {"length": 180000, "position": "5000"})
        np.handle_sync_seekbar(msg)  # must not string-repeat/overflow
        self.assertEqual(np.position, 1000)
        self.assertEqual(np.length, 180000)

    def test_handle_sync_seekbar_nan_position_ignored_without_raising(self):
        np, _ = self._make_now_playing()
        np.position = 1000
        np.length = 2000
        msg = Message("ovos.common_play.playback_time",
                      {"length": 180000, "position": float("nan")})
        np.handle_sync_seekbar(msg)  # must not raise ValueError
        # valid 'length' still applies; the invalid 'position' is ignored
        self.assertEqual(np.position, 1000)
        self.assertEqual(np.length, 180000)

    def test_handle_sync_seekbar_inf_length_ignored_without_raising(self):
        np, _ = self._make_now_playing()
        np.position = 1000
        np.length = 2000
        msg = Message("ovos.common_play.playback_time",
                      {"length": float("inf"), "position": 45000})
        np.handle_sync_seekbar(msg)  # must not raise OverflowError
        self.assertEqual(np.length, 2000)
        self.assertEqual(np.position, 45000)

    def test_handle_sync_seekbar_neg_inf_ignored_without_raising(self):
        np, _ = self._make_now_playing()
        np.position = 1000
        np.length = 2000
        msg = Message("ovos.common_play.playback_time",
                      {"length": float("-inf"), "position": float("-inf")})
        np.handle_sync_seekbar(msg)  # must not raise
        self.assertEqual(np.length, 2000)
        self.assertEqual(np.position, 1000)

    def test_handle_sync_seekbar_mixed_valid_length_nan_position_no_partial_commit(self):
        # the crash used to happen AFTER a valid field had already been
        # applied via setattr; verify both fields are validated up front
        # and a valid field can commit independently of an invalid one
        np, _ = self._make_now_playing()
        np.position = 999
        np.length = 111
        msg = Message("ovos.common_play.playback_time",
                      {"length": 180000, "position": float("nan")})
        np.handle_sync_seekbar(msg)
        self.assertEqual(np.length, 180000)  # valid field applied
        self.assertEqual(np.position, 999)   # invalid field left untouched

    def test_on_invalid_stream_after_bad_extract_does_not_raise(self):
        p = _make_player()
        np, _ = self._make_now_playing()
        np.uri = "ocp://original"
        np.stream_xtract = MagicMock()
        np.stream_xtract.extract_stream.return_value = {"uri": ["bad"]}
        with self.assertRaises(ValueError):
            np.extract_stream()
        p.now_playing = np
        # the retry-guard recovery path must not crash on the (still
        # valid, untouched) uri left behind by the refused extraction
        p.on_invalid_stream()  # must not raise

    def test_handle_external_play_updates_metadata(self):
        np, _ = self._make_now_playing()
        msg = Message("ovos.common_play.play",
                      {"media": {"title": "External Song",
                                 "uri": "http://example.com/e.mp3"}})
        np.handle_external_play(msg)
        self.assertEqual(np.title, "External Song")

    def test_handle_external_play_tracks_only_payload_is_noop(self):
        """A 'tracks'-only payload (no 'media' key) is ignored — the old
        tracks-list compat branch is gone, so this now falls through the
        empty-media guard without updating anything."""
        np, _ = self._make_now_playing()
        np.title = "unchanged"
        msg = Message("ovos.common_play.play",
                      {"tracks": [{"title": "Track 0",
                                   "uri": "http://example.com/t0.mp3"}]})
        np.handle_external_play(msg)  # must not raise
        self.assertEqual(np.title, "unchanged")

    def test_handle_track_state_change_updates_status(self):
        np, _ = self._make_now_playing()
        msg = Message("ovos.common_play.track.state",
                      {"state": int(TrackState.PLAYING_AUDIO)})
        np.handle_track_state_change(msg)
        self.assertEqual(np.status, TrackState.PLAYING_AUDIO)

    def test_handle_track_state_change_raises_on_missing(self):
        np, _ = self._make_now_playing()
        msg = Message("ovos.common_play.track.state", {})
        with self.assertRaises(ValueError):
            np.handle_track_state_change(msg)

    def test_handle_track_state_change_queued_does_not_crash(self):
        """Regression: a non-PLAYING state (QUEUED/…) used to hit a reference to
        the non-existent ``TrackState.PAUSED_AUDIO`` and raise AttributeError."""
        np, _ = self._make_now_playing()
        np._player = MagicMock()
        for st in (TrackState.QUEUED_AUDIO, TrackState.QUEUED_VIDEO,
                   TrackState.QUEUED_AUDIOSERVICE, TrackState.DISAMBIGUATION):
            np.status = TrackState.PLAYING_AUDIO  # force a transition each time
            np.handle_track_state_change(
                Message("ovos.common_play.track.state", {"state": int(st)}))
        # queued/disambiguation never change the player state
        np._player.set_player_state.assert_not_called()

    def test_handle_track_state_change_playing_sets_player_playing(self):
        """Every PLAYING_* track state (incl. AUDIOSERVICE and MPRIS) marks the
        player PLAYING — a paused track stays PLAYING_*, pause is a PlayerState."""
        from ovos_utils.ocp import PlayerState
        for st in (TrackState.PLAYING_AUDIO, TrackState.PLAYING_VIDEO,
                   TrackState.PLAYING_WEBVIEW, TrackState.PLAYING_SKILL,
                   TrackState.PLAYING_AUDIOSERVICE, TrackState.PLAYING_MPRIS):
            np, _ = self._make_now_playing()
            np._player = MagicMock()
            np.status = TrackState.DISAMBIGUATION
            np.handle_track_state_change(
                Message("ovos.common_play.track.state", {"state": int(st)}))
            np._player.set_player_state.assert_called_once_with(PlayerState.PLAYING)

    def test_handle_track_state_change_raises_on_bad_type(self):
        np, _ = self._make_now_playing()
        msg = Message("ovos.common_play.track.state", {"state": "playing"})
        with self.assertRaises(ValueError):
            np.handle_track_state_change(msg)

    def test_handle_track_state_noop_when_same(self):
        np, _ = self._make_now_playing()
        # TrackState defaults to DISAMBIGUATION
        original = np.status
        msg = Message("ovos.common_play.track.state",
                      {"state": int(original)})
        np.handle_track_state_change(msg)
        self.assertEqual(np.status, original)

    def test_handle_media_state_end_triggers_reset(self):
        np, _ = self._make_now_playing()
        np.title = "Something"
        msg = Message("ovos.common_play.media.state",
                      {"state": int(MediaState.END_OF_MEDIA)})
        np.handle_media_state_change(msg)
        # reset() should have cleared title
        self.assertEqual(np.title, "")

    def test_handle_media_state_raises_on_missing(self):
        np, _ = self._make_now_playing()
        msg = Message("ovos.common_play.media.state", {})
        with self.assertRaises(ValueError):
            np.handle_media_state_change(msg)


# ---------------------------------------------------------------------------
# OCPMediaPlayer: active_skill and playback_type properties
# ---------------------------------------------------------------------------

class TestPlayerProperties(unittest.TestCase):
    """active_skill, playback_type, tracks, can_prev, can_next."""

    def test_active_skill_getter(self):
        p = _make_player()
        p.now_playing.skill_id = "my.skill"
        self.assertEqual(p.active_skill, "my.skill")

    def test_active_skill_setter(self):
        p = _make_player()
        p.active_skill = "new.skill"
        self.assertEqual(p.now_playing.skill_id, "new.skill")

    def test_playback_type_getter(self):
        p = _make_player(PlaybackType.VIDEO)
        self.assertEqual(p.playback_type, PlaybackType.VIDEO)

    def test_playback_type_setter(self):
        p = _make_player()
        p.playback_type = PlaybackType.VIDEO
        self.assertEqual(p.now_playing.playback, PlaybackType.VIDEO)

    def test_tracks_returns_list(self):
        p = _make_player()
        p.playlist.entries = []
        result = p.tracks
        self.assertIsInstance(result, list)

    def test_can_prev_false_when_first_track(self):
        p = _make_player()
        p.now_playing.playback = PlaybackType.AUDIO
        p.playlist.is_first_track = True
        self.assertFalse(p.can_prev)

    def test_can_prev_true_for_mpris(self):
        p = _make_player(PlaybackType.MPRIS)
        p.playlist.is_first_track = True
        self.assertTrue(p.can_prev)

    def test_can_next_true_with_shuffle(self):
        p = _make_player()
        p.shuffle = True
        self.assertTrue(p.can_next)

    def test_can_next_true_with_loop(self):
        p = _make_player()
        p.loop_state = LoopState.REPEAT
        self.assertTrue(p.can_next)

    def test_can_next_false_when_last_track_no_loop(self):
        p = _make_player()
        p.loop_state = LoopState.NONE
        p.shuffle = False
        p.playlist.is_last_track = True
        p.media.search_playlist.is_last_track = True
        p.ocp_config = {"merge_search": False}
        self.assertFalse(p.can_next)


# ---------------------------------------------------------------------------
# OCPMediaPlayer: stop_skill / handle_MPRIS_takeover
# ---------------------------------------------------------------------------

class TestStopSkillAndMprisTakeover(unittest.TestCase):
    """stop_skill emits correct message; handle_MPRIS_takeover stops services."""

    def test_stop_skill_emits_message(self):
        p = _make_player()
        emitted = []
        p.bus.emit = lambda m: emitted.append(m)
        p.stop_skill()
        types = [m.msg_type for m in emitted]
        self.assertIn(f"ovos.common_play.{p.active_skill}.stop", types)

    def test_handle_mpris_takeover_stops_all_services(self):
        p = _make_player()
        p.handle_MPRIS_takeover()
        p.audio_service.stop.assert_called_once()
        p.video_service.stop.assert_called_once()
        p.web_service.stop.assert_called_once()

    def test_handle_mpris_takeover_clears_original_uri(self):
        p = _make_player()
        p.now_playing.original_uri = "http://example.com/t.mp3"
        p.handle_MPRIS_takeover()
        self.assertEqual(p.now_playing.original_uri, "")


# ---------------------------------------------------------------------------
# OCPMediaPlayer: reset
# ---------------------------------------------------------------------------

class TestOCPMediaPlayerReset(unittest.TestCase):
    """reset() clears playlist, media, and state flags."""

    def test_reset_calls_now_playing_reset(self):
        p = _make_player()
        p.reset()
        p.now_playing.reset.assert_called_once()

    def test_reset_calls_playlist_clear(self):
        p = _make_player()
        p.reset()
        p.playlist.clear.assert_called()

    def test_reset_clears_shuffle(self):
        p = _make_player()
        p.shuffle = True
        p.reset()
        self.assertFalse(p.shuffle)

    def test_reset_clears_loop_state(self):
        p = _make_player()
        p.loop_state = LoopState.REPEAT
        p.reset()
        self.assertEqual(p.loop_state, LoopState.NONE)

    def test_reset_sets_state_stopped(self):
        p = _make_player()
        p.state = PlayerState.PLAYING
        p.reset()
        self.assertEqual(p.state, PlayerState.STOPPED)


# ---------------------------------------------------------------------------
# OCPMediaPlayer: duck/unduck/cork/uncork handlers
# ---------------------------------------------------------------------------

class TestDuckUnduckCorkUncork(unittest.TestCase):
    """Audio ducking handler edge cases."""

    def test_duck_audio_calls_lower_volume(self):
        p = _make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PLAYING
        p.handle_duck_request(Message("ovos.common_play.duck"))
        p.audio_service.lower_volume.assert_called_once()
        self.assertTrue(p._paused_on_duck)

    def test_duck_video_calls_lower_volume(self):
        p = _make_player(PlaybackType.VIDEO)
        p.state = PlayerState.PLAYING
        p.handle_duck_request(Message("ovos.common_play.duck"))
        p.video_service.lower_volume.assert_called_once()

    def test_unduck_audio_calls_restore_volume(self):
        p = _make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PAUSED
        p._paused_on_duck = True
        p.handle_unduck_request(Message("ovos.common_play.unduck"))
        p.audio_service.restore_volume.assert_called_once()
        self.assertFalse(p._paused_on_duck)

    def test_unduck_noop_when_not_paused_on_duck(self):
        p = _make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PAUSED
        p._paused_on_duck = False
        p.handle_unduck_request(Message("ovos.common_play.unduck"))
        p.audio_service.restore_volume.assert_not_called()

    def test_cork_pauses_when_playing(self):
        p = _make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()
        p.handle_cork_request(Message("recognizer_loop:record_begin"))
        self.assertEqual(p.state, PlayerState.PAUSED)
        self.assertTrue(p._paused_on_duck)

    def test_cork_noop_when_not_playing(self):
        p = _make_player()
        p.state = PlayerState.PAUSED
        p.handle_cork_request(Message("recognizer_loop:record_begin"))
        # _paused_on_duck should NOT be toggled here since we weren't playing
        self.assertFalse(p._paused_on_duck)

    def test_uncork_resumes_when_paused_on_duck(self):
        p = _make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PAUSED
        p._paused_on_duck = True
        p.handle_status = MagicMock()
        p.handle_uncork_request(Message("recognizer_loop:record_end"))
        self.assertEqual(p.state, PlayerState.PLAYING)
        self.assertFalse(p._paused_on_duck)

    def test_uncork_noop_when_not_paused_on_duck(self):
        p = _make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PAUSED
        p._paused_on_duck = False
        p.handle_status = MagicMock()
        p.handle_uncork_request(Message("recognizer_loop:record_end"))
        # State should remain PAUSED
        self.assertEqual(p.state, PlayerState.PAUSED)


# ---------------------------------------------------------------------------
# OCPMediaPlayer: handle_playback_ended / handle_invalid_media
# ---------------------------------------------------------------------------

class TestHandlePlaybackEnded(unittest.TestCase):
    """handle_playback_ended calls play_next when playlist has items and autoplay is on."""

    def test_plays_next_when_playlist_has_tracks(self):
        p = _make_player()
        p.ocp_config = {"autoplay": True}
        p.playlist.__len__ = MagicMock(return_value=2)
        p.play_next = MagicMock()
        p.handle_playback_ended(Message("ovos.common_play.media.state"))
        p.play_next.assert_called_once()

    def test_noop_when_empty_playlist(self):
        p = _make_player()
        p.ocp_config = {"autoplay": True}
        p.playlist.__len__ = MagicMock(return_value=0)
        p.play_next = MagicMock()
        p.handle_playback_ended(Message("ovos.common_play.media.state"))
        p.play_next.assert_not_called()

    def test_noop_when_autoplay_false(self):
        p = _make_player()
        p.ocp_config = {"autoplay": False}
        p.playlist.__len__ = MagicMock(return_value=2)
        p.play_next = MagicMock()
        p.handle_playback_ended(Message("ovos.common_play.media.state"))
        p.play_next.assert_not_called()


class TestHandleInvalidMedia(unittest.TestCase):
    """handle_invalid_media speaks track.failed, rate-limited to once per queue."""

    def test_speaks_track_failed_once(self):
        p = _make_player()
        p.media.notify_dialog = MagicMock()
        p.handle_invalid_media(Message("ovos.common_play.media.state"))
        p.media.notify_dialog.assert_called_once_with("track.failed")
        self.assertTrue(p._track_failed_spoken)
        # rate-limited: a second call must not speak again
        p.handle_invalid_media(Message("ovos.common_play.media.state"))
        p.media.notify_dialog.assert_called_once_with("track.failed")


# ---------------------------------------------------------------------------
# OCPMediaPlayer: play_next edge cases
# ---------------------------------------------------------------------------

class TestPlayNext(unittest.TestCase):
    """play_next navigation logic."""

    def test_play_next_skill_emits_bus_message(self):
        p = _make_player(PlaybackType.SKILL)
        emitted = []
        p.bus.emit = lambda m: emitted.append(m)
        p.play_next()
        skill_next = [m for m in emitted
                      if "next" in m.msg_type]
        self.assertTrue(len(skill_next) >= 1)

    def test_play_next_repeat_track_calls_play(self):
        p = _make_player(PlaybackType.AUDIO)
        p.loop_state = LoopState.REPEAT_TRACK
        p.play = MagicMock()
        p.play_next()
        p.play.assert_called_once()

    def test_play_next_no_more_tracks_returns_without_play(self):
        """play_next does nothing when now_playing is the last entry in the queue."""
        p = _make_player(PlaybackType.AUDIO)
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
        p = _make_player(PlaybackType.AUDIO)
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
        p = _make_player(PlaybackType.AUDIO)
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


# ---------------------------------------------------------------------------
# OCPMediaPlayer: play_prev edge cases
# ---------------------------------------------------------------------------

class TestPlayPrev(unittest.TestCase):
    """play_prev navigation logic."""

    def test_play_prev_skill_emits_bus_message(self):
        p = _make_player(PlaybackType.SKILL)
        emitted = []
        p.bus.emit = lambda m: emitted.append(m)
        p.play_prev()
        prev_msgs = [m for m in emitted if "prev" in m.msg_type]
        self.assertTrue(len(prev_msgs) >= 1)

    def test_play_prev_goes_to_previous_track(self):
        """play_prev picks the entry before now_playing in the merged queue."""
        p = _make_player(PlaybackType.AUDIO)
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
        p = _make_player(PlaybackType.AUDIO)
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
        p = _make_player(PlaybackType.UNDEFINED)
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


# ---------------------------------------------------------------------------
# OCPMediaPlayer: play_media
# ---------------------------------------------------------------------------

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
        p = _make_player(PlaybackType.AUDIO)
        p.set_now_playing = MagicMock()
        p.play = MagicMock()
        p.media.search_playlist.replace = MagicMock()
        entry = self._make_audio_entry()
        p.playlist.__contains__ = MagicMock(return_value=False)
        p.play_media(entry)
        p.set_now_playing.assert_called_once_with(entry)

    def test_play_media_calls_play(self):
        p = _make_player(PlaybackType.AUDIO)
        p.set_now_playing = MagicMock()
        p.play = MagicMock()
        entry = self._make_audio_entry()
        p.playlist.__contains__ = MagicMock(return_value=False)
        p.play_media(entry)
        p.play.assert_called_once()

    def test_play_media_accepts_dict(self):
        p = _make_player(PlaybackType.AUDIO)
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
        p = _make_player()
        p.set_now_playing = MagicMock()
        p.play = MagicMock()
        p.play_media(12345)  # must not raise
        p.set_now_playing.assert_not_called()
        p.play.assert_not_called()

    def test_play_media_stops_mpris(self):
        p = _make_player(PlaybackType.AUDIO)
        p.mpris = MagicMock()
        p.set_now_playing = MagicMock()
        p.play = MagicMock()
        p.playlist.__contains__ = MagicMock(return_value=False)
        entry = self._make_audio_entry()
        p.play_media(entry)
        p.mpris.stop.assert_called_once()

    def test_play_media_with_disambiguation_updates_search_playlist(self):
        p = _make_player(PlaybackType.AUDIO)
        p.set_now_playing = MagicMock()
        p.play = MagicMock()
        p.playlist.__contains__ = MagicMock(return_value=False)
        entry = self._make_audio_entry()
        other = MediaEntry(title="Alt", uri="http://example.com/alt.mp3",
                           playback=PlaybackType.AUDIO)
        p.media.search_playlist.__contains__ = MagicMock(return_value=False)
        p.play_media(entry, disambiguation=[entry, other])
        p.media.search_playlist.replace.assert_called_once()


# ---------------------------------------------------------------------------
# OCPMediaPlayer: handle_player_media_update — END_OF_MEDIA path
# ---------------------------------------------------------------------------

class TestHandlePlayerMediaUpdateEndOfMedia(unittest.TestCase):
    """handle_player_media_update triggers handle_playback_ended on END_OF_MEDIA."""

    def test_end_of_media_triggers_playback_ended(self):
        p = _make_player()
        p.handle_playback_ended = MagicMock()
        msg = Message("ovos.common_play.media.state",
                      {"state": int(MediaState.END_OF_MEDIA)})
        p.handle_player_media_update(msg)
        p.handle_playback_ended.assert_called_once()

    def test_invalid_media_triggers_play_next(self):
        p = _make_player()
        p.handle_invalid_media = MagicMock()
        p.play_next = MagicMock()
        p.ocp_config = {"autoplay": True}
        # The skip is scheduled through on_invalid_stream() rather than
        # called inline, so it lands on the next tick, not this one.
        p.invalid_stream_delay = 0.01
        msg = Message("ovos.common_play.media.state",
                      {"state": int(MediaState.INVALID_MEDIA)})
        p.handle_player_media_update(msg)
        p.handle_invalid_media.assert_called_once()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not p.play_next.called:
            time.sleep(0.01)
        p.play_next.assert_called_once()


# ---------------------------------------------------------------------------
# OCPMediaPlayer: set_now_playing
# ---------------------------------------------------------------------------

class TestSetNowPlaying(unittest.TestCase):
    """set_now_playing updates now_playing and playlist."""

    def _make_fully_mocked_player(self, playback_type=PlaybackType.AUDIO):
        """Return a player with mocked playlist and now_playing."""
        p = _make_player(playback_type)
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
        p = _make_player()
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


if __name__ == "__main__":
    unittest.main()

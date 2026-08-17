"""Coverage tests for ovos_media/player.py.

Targets uncovered lines in NowPlaying, OCPMediaPlayer init, playback paths, etc.
"""
import unittest
from unittest.mock import MagicMock, patch, call

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import (
    PlayerState, MediaState, TrackState, LoopState,
    PlaybackType, MediaEntry, Playlist,
)


def _make_player(playback_type=PlaybackType.AUDIO):
    """Return a minimal OCPMediaPlayer with all external deps mocked."""
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
        p.now_playing.media_type = MagicMock()
        p.now_playing.infocard = {
            "uri": "http://example.com/track.mp3",
            "title": "Test Track",
        }
        p.now_playing.as_dict = {
            "uri": "http://example.com/track.mp3",
            "title": "Test Track",
        }
        p.playlist = MagicMock()
        p.playlist.as_list.return_value = []
        p.media = MagicMock()
        p.audio_service = MagicMock()
        p.video_service = MagicMock()
        p.web_service = MagicMock()
        p.current = None
        p.mpris = None
        p.bus = FakeBus()
    return p


class TestNowPlayingTrackStateChange(unittest.TestCase):
    """Test NowPlaying.handle_track_state_change with various states."""

    def _make_now_playing(self):
        from ovos_media.player import NowPlaying
        bus = FakeBus()
        with patch("ovos_media.player.now_playing.load_stream_extractors"):
            np = NowPlaying(bus)
        return np, bus

    def test_playing_video_sets_player_state(self):
        """TrackState.PLAYING_VIDEO should set player state to PLAYING."""
        np, bus = self._make_now_playing()
        mock_player = MagicMock()
        np._player = mock_player
        np.status = TrackState.QUEUED_AUDIO  # different state

        np.handle_track_state_change(Message("ovos.common_play.track.state",
                                             {"state": TrackState.PLAYING_VIDEO}))

        mock_player.set_player_state.assert_called_with(PlayerState.PLAYING)

    def test_playing_skill_sets_player_state(self):
        """TrackState.PLAYING_SKILL should set player state to PLAYING."""
        np, bus = self._make_now_playing()
        mock_player = MagicMock()
        np._player = mock_player

        np.handle_track_state_change(Message("ovos.common_play.track.state",
                                             {"state": TrackState.PLAYING_SKILL}))

        mock_player.set_player_state.assert_called_with(PlayerState.PLAYING)

class TestPlayerPlaySkillPath(unittest.TestCase):
    """Test play() with SKILL playback type."""

    def test_play_skill_emits_skill_play_message(self):
        """play() with SKILL type should emit ovos.common_play.{skill_id}.play."""
        p = _make_player(PlaybackType.SKILL)
        p.now_playing.skill_id = "test.skill"

        emitted = []
        p.bus.on("ovos.common_play.test.skill.play", lambda m: emitted.append(m))

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        self.assertEqual(len(emitted), 1)
        # Emitted data is the infocard dict itself
        self.assertIn("uri", emitted[0].data)


class TestPlayerPlayVideoPath(unittest.TestCase):
    """Test play() with VIDEO playback type."""

    def test_play_video_calls_video_service(self):
        """play() with VIDEO type should call video_service.play()."""
        p = _make_player(PlaybackType.VIDEO)

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        p.video_service.play.assert_called_once()

    def test_video_backend_present_still_plays_as_video(self):
        """Control case: a video backend claims the uri -> plays as VIDEO,
        does not fall back to audio."""
        p = _make_player(PlaybackType.VIDEO)
        p.video_service.can_play.return_value = True

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        p.video_service.play.assert_called_once()
        p.audio_service.play.assert_not_called()
        self.assertEqual(p.now_playing.playback, PlaybackType.VIDEO)

    def test_no_video_backend_claims_uri_falls_back_to_audio(self):
        """No installed video backend claims the uri (eg. a headless
        install with only audio backends configured), but an audio backend
        does -> play() must degrade to audio instead of dead-ending in
        INVALID_MEDIA."""
        p = _make_player(PlaybackType.VIDEO)
        p.video_service.can_play.return_value = False
        p.audio_service.can_play.return_value = True

        emitted = []
        p.bus.on("ovos.common_play.media.state", lambda m: emitted.append(m))

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        p.video_service.play.assert_not_called()
        p.audio_service.play.assert_called_once()
        self.assertEqual(p.now_playing.playback, PlaybackType.AUDIO)
        self.assertEqual(emitted, [],
                         "no INVALID_MEDIA should be emitted when the audio "
                         "fallback succeeds")

    def test_no_backend_claims_uri_at_all_emits_invalid_media(self):
        """Neither a video nor an audio backend claims the uri -> the
        fallback must not swallow a genuine INVALID_MEDIA."""
        p = _make_player(PlaybackType.VIDEO)
        p.video_service.can_play.return_value = False
        p.audio_service.can_play.return_value = False

        emitted = []
        p.bus.on("ovos.common_play.media.state", lambda m: emitted.append(m))

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        p.video_service.play.assert_not_called()
        p.audio_service.play.assert_not_called()
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].data["state"], MediaState.INVALID_MEDIA)

    def test_audio_fallback_stops_abandoned_video_backend(self):
        """A video backend is genuinely playing track A (svc.current is set)
        when a NEW play() request for a differently-shaped uri that no
        video backend claims arrives, falling back to audio. The abandoned
        video backend must be stopped and cleared — otherwise track A keeps
        playing on the video backend while the audio fallback starts a
        second stream (double playback)."""
        p = _make_player(PlaybackType.VIDEO)
        old_backend = MagicMock()
        p.video_service.current = old_backend
        p.video_service.can_play.return_value = False
        p.audio_service.can_play.return_value = True

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        old_backend.stop.assert_called_once()
        self.assertIsNone(p.video_service.current,
                          "abandoned video backend's `current` must be "
                          "cleared, or a later stray LOADED_MEDIA event "
                          "could revive it")
        p.audio_service.play.assert_called_once()

    def test_normal_video_to_video_switch_does_not_stop_new_backend(self):
        """Control: a normal switch where the video backend DOES claim the
        new uri must not have its `current` stopped/cleared by the
        fallback path (which must not trigger at all here)."""
        p = _make_player(PlaybackType.VIDEO)
        old_backend = MagicMock()
        p.video_service.current = old_backend
        p.video_service.can_play.return_value = True

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        old_backend.stop.assert_not_called()
        p.video_service.play.assert_called_once()
        p.audio_service.play.assert_not_called()


class TestPlayerPlayWebviewPath(unittest.TestCase):
    """Test play() with WEBVIEW playback type."""

    def test_play_webview_calls_web_service(self):
        """play() with WEBVIEW type should call web_service.play()."""
        p = _make_player(PlaybackType.WEBVIEW)

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        p.web_service.play.assert_called_once()

    def test_no_web_backend_claims_uri_falls_back_to_audio(self):
        """Same fallback as VIDEO, for WEBVIEW."""
        p = _make_player(PlaybackType.WEBVIEW)
        p.web_service.can_play.return_value = False
        p.audio_service.can_play.return_value = True

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        p.web_service.play.assert_not_called()
        p.audio_service.play.assert_called_once()
        self.assertEqual(p.now_playing.playback, PlaybackType.AUDIO)


class TestPlayerPlayWithMpris(unittest.TestCase):
    """Test play() with mpris enabled."""

    def test_play_updates_mpris_can_go_next(self):
        """play() should update mpris CanGoNext property."""
        p = _make_player()
        p.mpris = MagicMock()

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        # Check that update_props was called with CanGoNext
        calls = p.mpris.update_props.call_args_list
        self.assertTrue(any("CanGoNext" in str(call) for call in calls))

    def test_play_stops_mpris_if_stop_event_not_set(self):
        """play() should stop mpris if stop_event is not set."""
        p = _make_player()
        p.mpris = MagicMock()
        p.mpris.stop_event.is_set.return_value = False

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        p.mpris.stop.assert_called_once()


class TestPlayerPlayWithLikedSongs(unittest.TestCase):
    """Test play() with liked_songs play count tracking."""

    def test_play_increments_liked_songs_play_count(self):
        """play() should increment play_count for liked songs."""
        p = _make_player()
        p.now_playing.uri = "http://liked.mp3"
        # Create a mock object that acts like a dict with a store() method
        liked_songs_mock = MagicMock()
        liked_songs_dict = {"http://liked.mp3": {"title": "Liked"}}
        liked_songs_mock.__getitem__.side_effect = liked_songs_dict.__getitem__
        liked_songs_mock.__setitem__.side_effect = liked_songs_dict.__setitem__
        liked_songs_mock.__contains__.side_effect = liked_songs_dict.__contains__
        liked_songs_mock.get.side_effect = liked_songs_dict.get
        p.media.liked_songs = liked_songs_mock

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        # Check that play_count was incremented
        self.assertEqual(liked_songs_dict["http://liked.mp3"]["play_count"], 1)
        liked_songs_mock.store.assert_called_once()

    def test_play_survives_liked_song_popped_between_check_and_index(self):
        """play() must not raise KeyError when another bus-handler thread
        (handle_unlike) pops the now-playing uri from liked_songs between
        the membership check and the play_count mutation - a real race,
        since bus handlers dispatch on a thread pool."""
        p = _make_player()
        p.now_playing.uri = "http://liked.mp3"

        class _PoppedBetweenCheckAndIndex(dict):
            """Membership looks True (the entry existed a moment ago) but
            indexing/`.get()` raises/returns None as if it was concurrently
            popped - simulates the race window without needing real
            threads."""

            def __contains__(self, key):
                return True

            def __getitem__(self, key):
                raise KeyError(key)

            def get(self, key, default=None):
                return default

        liked_songs = _PoppedBetweenCheckAndIndex()
        liked_songs.store = MagicMock()
        p.media.liked_songs = liked_songs

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()  # must not raise KeyError

        # no entry to mutate - store() must not be called
        liked_songs.store.assert_not_called()


class _LockProbe:
    """Records acquire()/release() calls, standing in for an RLock so a
    test can assert a critical section actually took the lock."""

    def __init__(self):
        self.acquire_count = 0
        self.release_count = 0

    def __enter__(self):
        self.acquire_count += 1
        return self

    def __exit__(self, *exc):
        self.release_count += 1
        return False


class TestLikedSongsLockSerialization(unittest.TestCase):
    """D4: liked_songs.store() (json.dump) iterates the dict while
    handle_like/handle_unlike/play()'s play-count block can mutate it from
    other bus-dispatch threads - all three sites must go through the same
    _liked_songs_lock. Fails on the old code because the lock attribute
    doesn't exist / isn't held around these sites."""

    def test_play_play_count_block_holds_liked_songs_lock(self):
        p = _make_player()
        p.now_playing.uri = "http://liked.mp3"
        liked_songs_mock = MagicMock()
        liked_songs_dict = {"http://liked.mp3": {"title": "Liked"}}
        liked_songs_mock.__getitem__.side_effect = liked_songs_dict.__getitem__
        liked_songs_mock.__setitem__.side_effect = liked_songs_dict.__setitem__
        liked_songs_mock.__contains__.side_effect = liked_songs_dict.__contains__
        liked_songs_mock.get.side_effect = liked_songs_dict.get
        p.media.liked_songs = liked_songs_mock

        probe = _LockProbe()
        p._liked_songs_lock = probe

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        self.assertGreaterEqual(probe.acquire_count, 1)
        self.assertEqual(probe.acquire_count, probe.release_count)
        liked_songs_mock.store.assert_called_once()

    def test_handle_unlike_holds_liked_songs_lock(self):
        p = _make_player()
        p.media.liked_songs = MagicMock()
        p.media.liked_songs.__contains__ = MagicMock(return_value=True)

        probe = _LockProbe()
        p._liked_songs_lock = probe

        from ovos_bus_client.message import Message
        p.handle_unlike(Message("ovos.common_play.unlike", {"uri": "http://liked.mp3"}))

        self.assertGreaterEqual(probe.acquire_count, 1)
        self.assertEqual(probe.acquire_count, probe.release_count)
        p.media.liked_songs.pop.assert_called_once()
        p.media.liked_songs.store.assert_called_once()


class TestPlayerValidateStreamException(unittest.TestCase):
    """Test validate_stream with extraction exception."""

    def test_validate_stream_exception_returns_false(self):
        """validate_stream should return False if extract_stream raises."""
        p = _make_player()
        p.now_playing.playback = PlaybackType.AUDIO
        p.now_playing.extract_stream = MagicMock(side_effect=Exception("fail"))

        result = p.validate_stream()

        self.assertFalse(result)


class TestPlayerOnInvalidStream(unittest.TestCase):
    """Test on_invalid_stream."""

    def test_on_invalid_stream_marks_uri_failed_and_schedules_retry(self):
        """on_invalid_stream should record the failed uri and schedule a retry."""
        p = _make_player()
        p.playlist = MagicMock()
        p.playlist.entries = []
        p.now_playing.uri = "http://example.com/bad.mp3"
        p._schedule_play_next = MagicMock()

        p.on_invalid_stream()

        self.assertIn("http://example.com/bad.mp3", p._failed_uris)
        p._schedule_play_next.assert_called_once()


class TestPlayerPlayShuffle(unittest.TestCase):
    """Test play_shuffle."""

    def test_play_shuffle_picks_different_track(self):
        """play_shuffle should set now_playing to a different track."""
        p = _make_player()
        from ovos_utils.ocp import Playlist
        p.playlist = Playlist()
        e1 = MediaEntry(uri="http://a.mp3", playback=PlaybackType.AUDIO)
        e2 = MediaEntry(uri="http://b.mp3", playback=PlaybackType.AUDIO)
        p.playlist.add_entry(e1)
        p.playlist.add_entry(e2)
        p.now_playing = MagicMock()
        p.now_playing.uri = "http://a.mp3"
        p.media = MagicMock()
        p.media.search_playlist.entries = []

        with patch.object(p, "set_now_playing") as mock_set:
            p.play_shuffle()

        mock_set.assert_called_once()

    def test_play_shuffle_with_small_queue_returns_early(self):
        """play_shuffle should return without changing track if queue < 2."""
        p = _make_player()
        p.playlist = Playlist()
        p.media = MagicMock()
        p.media.search_playlist.entries = []

        with patch.object(p, "set_now_playing") as mock_set:
            p.play_shuffle()

        mock_set.assert_not_called()


class TestPlayerPlayNextMpris(unittest.TestCase):
    """Test play_next with MPRIS playback."""

    def test_play_next_mpris_with_manage_players(self):
        """play_next with MPRIS and manage_players=True should call mpris.play_next()."""
        p = _make_player(PlaybackType.MPRIS)
        p.mpris = MagicMock()
        p.mpris.manage_players = True

        p.play_next()

        p.mpris.play_next.assert_called_once()


class TestPlayerPlayNextWithShuffle(unittest.TestCase):
    """Test play_next with shuffle enabled."""

    def test_play_next_with_shuffle_starts_playback(self):
        """play_next with shuffle=True must select a track AND start it -
        play_shuffle only picks, play() is what reaches the backend. The
        old test mocked play_shuffle out, which hid exactly that gap."""
        p = _make_player()
        p.shuffle = True

        with patch.object(p, "play") as mock_play:
            p.play_next()

        mock_play.assert_called_once()


class TestPlayerPlayPrevMpris(unittest.TestCase):
    """Test play_prev with MPRIS."""

    def test_play_prev_mpris_disabled_warns(self):
        """play_prev with MPRIS and manage_players=False should warn."""
        p = _make_player(PlaybackType.MPRIS)
        p.mpris = MagicMock()
        p.mpris.manage_players = False

        p.play_prev()

        # Should not crash, mpris.play_prev should not be called
        p.mpris.play_prev.assert_not_called()


class TestPlayerPlayPrevWithShuffle(unittest.TestCase):
    """Test play_prev with shuffle."""

    def test_play_prev_with_shuffle_starts_playback(self):
        """Same contract as play_next: a shuffled 'previous' must actually
        start playback, not just repoint now_playing."""
        p = _make_player()
        p.shuffle = True

        with patch.object(p, "play") as mock_play:
            p.play_prev()

        mock_play.assert_called_once()


class TestPlayerPauseMpris(unittest.TestCase):
    """Test pause with MPRIS."""

    def test_pause_mpris_with_manage_players(self):
        """pause() with MPRIS and manage_players=True should call mpris.pause()."""
        p = _make_player(PlaybackType.MPRIS)
        p.mpris = MagicMock()
        p.mpris.manage_players = True

        with patch.object(p, "set_player_state"):
            p.pause()

        p.mpris.pause.assert_called_once()


class TestPlayerResumeMpris(unittest.TestCase):
    """Test resume with MPRIS."""

    def test_resume_mpris_with_manage_players(self):
        """resume() with MPRIS and manage_players=True should call mpris.resume()."""
        p = _make_player(PlaybackType.MPRIS)
        p.mpris = MagicMock()
        p.mpris.manage_players = True

        with patch.object(p, "set_player_state"):
            p.resume()

        p.mpris.resume.assert_called_once()


class TestPlayerStopMpris(unittest.TestCase):
    """Test stop with MPRIS."""

    def test_stop_mpris_calls_mpris_pause(self):
        """stop() with MPRIS playback should call mpris.pause()."""
        p = _make_player(PlaybackType.MPRIS)
        p.mpris = MagicMock()

        with patch.object(p, "set_player_state"):
            p.stop()

        p.mpris.pause.assert_called_once()


class TestPlayerHandleRepeatToggleMpris(unittest.TestCase):
    """Test handle_repeat_toggle_request with MPRIS."""

    def test_repeat_toggle_mpris_calls_toggle_repeat(self):
        """handle_repeat_toggle_request with MPRIS should call mpris.toggle_repeat()."""
        p = _make_player(PlaybackType.MPRIS)
        p.mpris = MagicMock()
        p.loop_state = LoopState.NONE

        with patch.object(p, "handle_status"):
            p.handle_repeat_toggle_request(Message("x"))

        p.mpris.toggle_repeat.assert_called_once()


class TestPlayerHandleShuffleMpris(unittest.TestCase):
    """Test handle_shuffle_toggle_request with MPRIS."""

    def test_shuffle_toggle_mpris_calls_toggle_shuffle(self):
        """handle_shuffle_toggle_request with MPRIS should call mpris.toggle_shuffle()."""
        p = _make_player(PlaybackType.MPRIS)
        p.mpris = MagicMock()

        with patch.object(p, "handle_status"):
            p.handle_shuffle_toggle_request(Message("x"))

        p.mpris.toggle_shuffle.assert_called_once()


class TestPlayerUnductRequestVideo(unittest.TestCase):
    """Test handle_unduck_request with VIDEO playback."""

    def test_unduck_video_restores_volume(self):
        """handle_unduck_request with VIDEO should call video_service.restore_volume()."""
        p = _make_player(PlaybackType.VIDEO)
        p._paused_on_duck = True

        p.handle_unduck_request(Message("x"))

        p.video_service.restore_volume.assert_called_once()


class TestPlayerUtteranceHandled(unittest.TestCase):
    """Test handle_utterance_handled."""

    def test_utterance_handled_calls_unduck(self):
        """handle_utterance_handled should call handle_unduck_request if _paused_on_duck."""
        p = _make_player()
        p._paused_on_duck = True

        with patch.object(p, "handle_unduck_request") as mock_unduck:
            p.handle_utterance_handled(Message("x"))

        mock_unduck.assert_called_once()

    def test_utterance_handled_cork_path_resumes_playback(self):
        """Cork path: PAUSED + _paused_on_duck=True must resume via
        handle_uncork_request, not just restore volume, otherwise the
        player stays paused forever (record_end already no-op'd while a
        'speak' was in flight)."""
        p = _make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PAUSED
        p._paused_on_duck = True
        p.handle_status = MagicMock()

        with patch.object(p, "handle_unduck_request") as mock_unduck:
            p.handle_utterance_handled(Message("ovos.utterance.handled"))

        mock_unduck.assert_not_called()
        self.assertEqual(p.state, PlayerState.PLAYING)
        self.assertFalse(p._paused_on_duck)

    def test_utterance_handled_duck_path_unchanged(self):
        """Duck path: PLAYING + _paused_on_duck=True must only restore
        volume, never call resume/uncork."""
        p = _make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PLAYING
        p._paused_on_duck = True

        with patch.object(p, "handle_uncork_request") as mock_uncork:
            p.handle_utterance_handled(Message("ovos.utterance.handled"))

        mock_uncork.assert_not_called()
        p.audio_service.restore_volume.assert_called_once()
        self.assertEqual(p.state, PlayerState.PLAYING)
        self.assertFalse(p._paused_on_duck)

    def test_cork_then_utterance_handled_end_to_end_resumes(self):
        """End-to-end: record_begin corks playback, then
        ovos.utterance.handled must resume it (the previously-stuck
        sequence), and a late record_end afterwards is a harmless no-op."""
        p = _make_player(PlaybackType.AUDIO)
        p.state = PlayerState.PLAYING
        p.handle_status = MagicMock()

        p.handle_cork_request(Message("recognizer_loop:record_begin"))
        self.assertEqual(p.state, PlayerState.PAUSED)
        self.assertTrue(p._paused_on_duck)

        p.handle_utterance_handled(Message("ovos.utterance.handled"))
        self.assertEqual(p.state, PlayerState.PLAYING)
        self.assertFalse(p._paused_on_duck)

        # a late record_end must no-op harmlessly (flag already cleared)
        p.handle_record_end(Message("recognizer_loop:record_end"))
        self.assertEqual(p.state, PlayerState.PLAYING)
        self.assertFalse(p._paused_on_duck)


class TestPlayerHandlePlayRequestNoMedia(unittest.TestCase):
    """Test handle_play_request with no media."""

    def test_play_request_no_media_returns_early(self):
        """handle_play_request with no media should return without playing."""
        p = _make_player()

        with patch.object(p, "play_media") as mock_play:
            p.handle_play_request(Message("ovos.common_play.play", {}))

        mock_play.assert_not_called()


class TestPlayerHandleMediaUpdateInvalidAutoplayOff(unittest.TestCase):
    """Test handle_player_media_update with INVALID_MEDIA and autoplay=False."""

    def test_invalid_media_autoplay_false_no_play_next(self):
        """handle_player_media_update INVALID_MEDIA with autoplay=False shouldn't call play_next."""
        p = _make_player()
        p.ocp_config = {"autoplay": False}
        p.media_state = MediaState.NO_MEDIA

        with patch.object(p, "handle_invalid_media"), \
             patch.object(p, "play_next") as mock_next:
            p.handle_player_media_update(Message("ovos.common_play.media.state",
                                                 {"state": MediaState.INVALID_MEDIA}))

        mock_next.assert_not_called()


class TestPlayerHandleMycroftstop(unittest.TestCase):
    """Test handle_mycroft_stop."""

    def test_mycroft_stop_when_playing(self):
        """handle_mycroft_stop should stop and reset when player is PLAYING."""
        p = _make_player()
        p.state = PlayerState.PLAYING

        with patch.object(p, "stop"), patch.object(p, "reset"):
            p.handle_mycroft_stop(Message("mycroft.stop"))


class TestPlayerListBackendsRequest(unittest.TestCase):
    """Test handle_list_backends_request."""

    def test_list_backends_response(self):
        """handle_list_backends_request should emit response with available_backends."""
        p = _make_player()
        p.audio_service.available_backends.return_value = {"vlc": {}}

        received = []
        p.bus.on("ovos.common_play.list_backends.response", lambda m: received.append(m))

        p.handle_list_backends_request(Message("ovos.common_play.list_backends"))

        self.assertEqual(len(received), 1)


if __name__ == "__main__":
    unittest.main()

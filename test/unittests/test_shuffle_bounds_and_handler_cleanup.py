"""Regression tests covering two OCPMediaPlayer defect fixes.

Shuffle bounds
    play_next's shuffle branch must respect the same termination bounds as
    the sequential path (_all_tracks_failed(), end-of-queue): an all-failing
    queue with shuffle+REPEAT must not hot-loop forever picking a "random"
    failed track and calling self.play() unconditionally.

Dead-player duck/unduck/cork leak
    A shut-down OCPMediaPlayer must not keep answering duck/unduck/cork.
    handle_duck_request/handle_unduck_request/handle_cork_request must not
    be bound (as the SAME bound-method object) to both a plain
    'ovos.common_play.*' topic and a migrated legacy 'recognizer_loop:*'
    topic: upstream MessageBusClient.remove() (ovos-bus-client client.py)
    dedups registrations of a shared callable via self._dedup_registrations,
    keyed by that callable; once ANY of its topics is migrated, remove()
    takes the dedup branch for EVERY topic bound to it — including a plain
    topic whose registration never lived in that table — and silently
    removes nothing for it instead of falling through to _remove_normal().

The console-script resolution fix for test_help_flag.py (resolving
Path(sys.executable).parent / "ovos-media" instead of shutil.which(), which
could certify a stale shared-venv script) is covered by test_help_flag.py
itself; no separate regression test is needed here.
"""
import unittest
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import (
    PlayerState, MediaState, TrackState, LoopState,
    PlaybackType, MediaEntry, Playlist,
)


def _make_player(playback_type=PlaybackType.AUDIO):
    """Return a minimal OCPMediaPlayer with all external deps mocked
    (same helper shape as test_player_coverage2.py's _make_player)."""
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
        p.shuffle = True
        p.track_history = {}
        p._paused_on_duck = False
        p.now_playing = MagicMock()
        p.now_playing.playback = playback_type
        p.now_playing.skill_id = "test.skill"
        p.now_playing.uri = "http://a.mp3"
        p.now_playing.original_uri = "http://a.mp3"
        p.now_playing.title = "Track A"
        p.now_playing.artist = "Test Artist"
        p.now_playing.image = ""
        p.now_playing.length = 180000
        p.now_playing.position = 0
        p.now_playing.media_type = MagicMock()
        p.now_playing.infocard = {"uri": "http://a.mp3", "title": "Track A"}
        p.now_playing.as_dict = {"uri": "http://a.mp3", "title": "Track A"}
        p.playlist = Playlist()
        p.media = MagicMock()
        p.media.search_playlist.entries = []
        p.audio_service = MagicMock()
        p.video_service = MagicMock()
        p.web_service = MagicMock()
        p.current = None
        p.mpris = None
        p.bus = FakeBus()
    return p


def _track(uri, title="T"):
    return MediaEntry(uri=uri, title=title, playback=PlaybackType.AUDIO)


class TestShuffleAllFailedBounded(unittest.TestCase):
    """An all-failing shuffle+REPEAT queue must stop instead of hot-looping."""

    def test_all_failing_shuffle_repeat_stops_bounded(self):
        p = _make_player()
        p.loop_state = LoopState.REPEAT
        tracks = [_track(f"http://{i}.mp3", f"T{i}") for i in range(4)]
        for t in tracks:
            p.playlist.add_entry(t)
        p.now_playing.uri = tracks[0].uri
        for t in tracks:
            p._failed_uris.add(t.uri)

        with patch.object(p, "play") as mock_play, \
             patch.object(p, "set_player_state") as mock_state:
            # bounded: even calling play_next several times in a row must
            # never resume playback - it must settle into STOPPED and stay
            # there, not loop forever picking new "random" failed tracks
            for _ in range(5):
                p.play_next()

        mock_play.assert_not_called()
        mock_state.assert_any_call(PlayerState.STOPPED)

    def test_one_good_track_keeps_playing_bound_not_over_triggered(self):
        """Control: the failure bound must not fire when at least one
        unfailed track remains - shuffle should keep advancing/replaying
        the good track, never stopping."""
        p = _make_player()
        p.loop_state = LoopState.REPEAT
        good = _track("http://good.mp3", "Good")
        bad_tracks = [_track(f"http://bad{i}.mp3", f"Bad{i}") for i in range(3)]
        for t in [good] + bad_tracks:
            p.playlist.add_entry(t)
        for t in bad_tracks:
            p._failed_uris.add(t.uri)
        p.now_playing.uri = good.uri

        with patch.object(p, "play") as mock_play, \
             patch.object(p, "set_player_state") as mock_state:
            p.play_next()

        mock_play.assert_called_once()
        mock_state.assert_not_called()
        self.assertEqual(p.now_playing.uri, good.uri,
                        "the only unfailed track must still be the one selected")

    def test_single_track_shuffle_repeat_off_stops_with_queue_finished(self):
        """A single-track shuffle queue with repeat off has no other track
        to shuffle to - must stop and speak queue.finished, exactly like
        the sequential end-of-queue path, not replay forever."""
        p = _make_player()
        p.loop_state = LoopState.NONE
        only = _track("http://only.mp3", "Only")
        p.playlist.add_entry(only)
        p.now_playing.uri = only.uri

        with patch.object(p, "play") as mock_play, \
             patch.object(p, "set_player_state") as mock_state:
            p.play_next()

        mock_play.assert_not_called()
        mock_state.assert_called_once_with(PlayerState.STOPPED)
        p.media.speak_dialog.assert_called_once_with("queue.finished")


class TestDeadPlayerStopsHandlingDuckTopics(unittest.TestCase):
    """After shutdown, a player must not react to duck/unduck/cork anymore -
    neither via the plain ovos.common_play.* topics nor the legacy
    recognizer_loop:* ones."""

    def _make_real_player(self, bus):
        from ovos_media.player import OCPMediaPlayer
        with patch("ovos_media.player.AudioService"), \
             patch("ovos_media.player.VideoService"), \
             patch("ovos_media.player.WebService"), \
             patch("ovos_media.player.OcpMprisExporter"), \
                 patch("ovos_media.player.Configuration", return_value={"media": {}}), \
             patch("ovos_media.player.OCPMediaCatalog"):
            p = OCPMediaPlayer(bus, config={})
        return p

    def test_unduck_after_shutdown_does_not_restore_volume(self):
        bus = FakeBus()
        p = self._make_real_player(bus)
        p.playback_type = PlaybackType.AUDIO
        p.state = PlayerState.PLAYING
        p._paused_on_duck = True
        p.audio_service = MagicMock()

        p.bus_api.shutdown()

        bus.emit(Message("ovos.common_play.unduck"))
        p.audio_service.restore_volume.assert_not_called()

    def test_duck_after_shutdown_does_not_lower_volume(self):
        bus = FakeBus()
        p = self._make_real_player(bus)
        p.playback_type = PlaybackType.AUDIO
        p.state = PlayerState.PLAYING
        p.audio_service = MagicMock()

        p.bus_api.shutdown()

        bus.emit(Message("ovos.common_play.duck"))
        p.audio_service.lower_volume.assert_not_called()


class TestShuffleEmptyQueueFailedTrackBounded(unittest.TestCase):
    """An empty merged queue (e.g. playlist cleared mid-play) whose
    now_playing track already failed must not be replayed forever."""

    def test_empty_merged_queue_failed_current_track_stops_bounded(self):
        p = _make_player()
        p.loop_state = LoopState.NONE
        p.shuffle = True
        p.playlist = Playlist()
        p.media.search_playlist.entries = []
        p._failed_uris.add(p.now_playing.uri)

        with patch.object(p, "play") as mock_play, \
             patch.object(p, "set_player_state") as mock_state:
            p.play_next()

        mock_play.assert_not_called()
        mock_state.assert_called_once_with(PlayerState.STOPPED)
        p.media.speak_dialog.assert_called_once_with("queue.finished")


if __name__ == "__main__":
    unittest.main()

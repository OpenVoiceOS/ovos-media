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
#
"""Quick-win #4: spoken failure dialogs.

- no.playback.backend: spoken once, at the first play attempt, when zero
  backends of any kind are loaded.
- track.failed: spoken once per queue, on evidence of PLAYBACK (not on
  every LOADED_MEDIA — base.py emits LOADED_MEDIA then INVALID_MEDIA for a
  track that loads ok but fails to play, which would otherwise reset the
  guard on every single failing track).
- queue.finished: spoken exactly once, when play_next() finds there really
  are no more tracks — not on every autoplay-off track end, not on an
  MPRIS-external track end, not on an explicit stop.
"""
import unittest
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import MediaEntry, MediaState, PlaybackType, PlayerState


def _make_player():
    from ovos_media.player import OCPMediaPlayer
    p = OCPMediaPlayer.__new__(OCPMediaPlayer)
    p.bus = FakeBus()
    p.validate_source = False
    p._init_runtime_state()
    p.media = MagicMock()
    p.mpris = None
    p.playlist = MagicMock()
    p.playlist.__len__ = MagicMock(return_value=0)
    p.playlist.__contains__ = MagicMock(return_value=False)
    p.search_playlist = MagicMock()
    p.ocp_config = {}
    p.gui = MagicMock()
    p.state = PlayerState.STOPPED
    return p


class TestNoPlaybackBackendDialog(unittest.TestCase):
    """Spoken once, at the first play attempt, when zero backends are loaded."""

    def _make_player_with_backends(self, audio_services, video_services=(),
                                    web_services=()):
        p = _make_player()
        p.audio_service = MagicMock(services=list(audio_services))
        p.video_service = MagicMock(services=list(video_services))
        p.web_service = MagicMock(services=list(web_services))
        p.set_now_playing = MagicMock()
        p.play = MagicMock()
        return p

    def test_spoken_when_no_backends_loaded(self):
        p = self._make_player_with_backends([])
        p.play_media({"uri": "http://x.mp3", "title": "X"})
        p.media.speak_dialog.assert_called_once_with("no.playback.backend")

    def test_not_spoken_when_a_backend_is_loaded(self):
        p = self._make_player_with_backends([MagicMock()])
        p.play_media({"uri": "http://x.mp3", "title": "X"})
        p.media.speak_dialog.assert_not_called()

    def test_spoken_only_once_across_repeated_play_attempts(self):
        p = self._make_player_with_backends([])
        p.play_media({"uri": "http://x.mp3", "title": "X"})
        p.play_media({"uri": "http://y.mp3", "title": "Y"})
        p.play_media({"uri": "http://z.mp3", "title": "Z"})
        self.assertEqual(p.media.speak_dialog.call_count, 1)

    def test_video_only_backend_still_counts_as_available(self):
        p = self._make_player_with_backends([], video_services=[MagicMock()])
        p.play_media({"uri": "http://x.mp4", "title": "X"})
        p.media.speak_dialog.assert_not_called()


class TestTrackFailedDialog(unittest.TestCase):
    """Spoken on an INVALID_MEDIA skip, rate-limited to once per queue.

    The guard (`_track_failed_spoken`/`_failed_uris`) is reset on evidence
    of PLAYBACK — NowPlaying.handle_track_state_change's TrackState.PLAYING_*
    branch — never by hand-setting the flags. Driving the real
    'ovos.common_play.track.state' PLAYING_AUDIO event through a real
    NowPlaying instance is what makes these tests fail if the reset site is
    gutted or moved back to LOADED_MEDIA."""

    def _make_player(self):
        from ovos_media.player import NowPlaying
        p = _make_player()
        # a real NowPlaying wired to the same bus/player, exactly as
        # OCPMediaPlayer.__init__ constructs it, so handle_track_state_change
        # is the genuine reset site under test (not a stand-in).
        p.now_playing = NowPlaying(p.bus, player=p)
        return p

    def test_spoken_on_first_invalid_media(self):
        p = self._make_player()
        p.handle_invalid_media()
        p.media.speak_dialog.assert_called_once_with("track.failed")

    def test_not_spoken_again_for_a_second_invalid_track_in_the_same_queue(self):
        p = self._make_player()
        p.handle_invalid_media()
        p.handle_invalid_media()
        p.handle_invalid_media()
        self.assertEqual(p.media.speak_dialog.call_count, 1)

    def test_not_reset_by_loaded_media_alone(self):
        """LOADED_MEDIA without a subsequent confirmed-PLAYING TrackState
        (eg. a track that loads fine but raises out of current.play() —
        base.py's handle_media_state_change emits LOADED_MEDIA then
        INVALID_MEDIA for exactly that case) must NOT reset the guard. If it
        did, a REPEAT queue where every track loads-ok-but-fails-to-play
        would never trip _all_tracks_failed() and would loop unbounded."""
        p = self._make_player()
        p.handle_invalid_media()
        self.assertEqual(p.media.speak_dialog.call_count, 1)
        # a bare LOADED_MEDIA/BUFFERED_MEDIA transition (no PLAYING_* track
        # state) must be a no-op for the guard
        with p._state_lock:
            p.media_state = MediaState.LOADED_MEDIA
        p.handle_invalid_media()
        self.assertEqual(p.media.speak_dialog.call_count, 1,
                        "LOADED_MEDIA alone must not reset the track.failed "
                        "rate limit")

    def test_spoken_again_after_a_track_actually_plays(self):
        """Real evidence of playback — TrackState.PLAYING_AUDIO on
        'ovos.common_play.track.state', driven through the real NowPlaying
        handler — clears the per-queue rate limit, so a later failure in a
        DIFFERENT run of playback speaks again."""
        from ovos_utils.ocp import TrackState
        p = self._make_player()
        p.handle_invalid_media()
        self.assertEqual(p.media.speak_dialog.call_count, 1)
        # set_player_state()'s own status-report side effects need a fuller
        # player than this fixture builds; stub it so the test isolates the
        # guard-reset logic under test (handle_track_state_change calling
        # set_player_state, then unconditionally clearing the guards) without
        # also exercising unrelated status-report plumbing.
        p.set_player_state = MagicMock()
        # drive the real handler the bus edge dispatches to after
        # current.play() returns without raising (see base.py
        # handle_media_state_change)
        p.now_playing.handle_track_state_change(
            Message("ovos.common_play.track.state",
                    {"state": TrackState.PLAYING_AUDIO}))
        self.assertEqual(p._failed_uris, set())
        self.assertFalse(p._track_failed_spoken)
        p.handle_invalid_media()
        self.assertEqual(p.media.speak_dialog.call_count, 2)

    def test_flag_is_cleared_by_reset_body(self):
        """reset() must clear the per-queue rate-limit flag alongside
        _failed_uris (same guard both are reset together, see player.py's
        reset()) so a fresh queue after an explicit stop speaks again."""
        p = self._make_player()
        p.handle_invalid_media()
        self.assertTrue(p._track_failed_spoken)
        # drive the REAL reset() body (not a hand-set of the two flags) —
        # stub out the collaborators reset() also touches that this fixture
        # doesn't construct, so only the bookkeeping under test is real.
        p.now_playing.reset = MagicMock()
        p._invalid_timer = None
        p.playlist.clear = MagicMock()
        p.set_media_state = MagicMock()
        p.playback_type = PlaybackType.UNDEFINED
        p.shuffle = True
        p.loop_state = MagicMock()
        p.set_player_state = MagicMock()
        p.reset()
        self.assertFalse(p._track_failed_spoken)
        self.assertEqual(p._failed_uris, set())
        p.handle_invalid_media()
        self.assertEqual(p.media.speak_dialog.call_count, 2)


def _track(uri, title, playback=PlaybackType.AUDIO):
    return MediaEntry(uri=uri, title=title, playback=playback)


def _real_player(bus=None, config=None):
    """A real OCPMediaPlayer on a FakeBus (same construction pattern as
    test_end_of_track_handling.py), with stream extraction and the actual
    speak_dialog call stubbed out so tests observe calls without touching
    real dialog resources."""
    from ovos_media.player import OCPMediaPlayer
    bus = bus or FakeBus()
    player = OCPMediaPlayer(bus, config=config if config is not None else {})
    player.now_playing.extract_stream = lambda: None
    player.media.speak_dialog = MagicMock()
    return player


def _load(player, entries):
    player.playlist.clear()
    for e in entries:
        player.playlist.add_entry(e)
    player.set_now_playing(player.playlist[0])


class TestQueueFinishedDialog(unittest.TestCase):
    """Spoken exactly once, when play_next() finds no more tracks — the
    ONLY state real playback reaches at the true end of a queue. Every test
    here drives a real, non-empty playlist through the real handlers (never
    a hand-set playlist length of 0, which real playback never reaches)."""

    def test_two_track_queue_speaks_once_at_the_natural_end(self):
        player = _real_player(config={"autoplay": True})
        a = _track("http://example.com/a.mp3", "A")
        b = _track("http://example.com/b.mp3", "B")
        _load(player, [a, b])
        player.set_player_state(PlayerState.PLAYING)
        player.media_state = MediaState.BUFFERED_MEDIA

        with patch.object(player, "play"):
            # track A ends -> advances to B, no announcement yet
            player.bus.emit(Message("ovos.common_play.media.state",
                                    {"state": MediaState.END_OF_MEDIA}))
            self.assertEqual(player.now_playing.uri, b.uri)
            player.media.speak_dialog.assert_not_called()

            # track B ends -> no more tracks -> spoken exactly once
            player.media_state = MediaState.BUFFERED_MEDIA
            player.bus.emit(Message("ovos.common_play.media.state",
                                    {"state": MediaState.END_OF_MEDIA}))
        player.media.speak_dialog.assert_called_once_with("queue.finished")
        self.assertEqual(player.state, PlayerState.STOPPED)
        player.shutdown()

    def test_autoplay_off_mid_queue_is_silent(self):
        """With autoplay disabled, a track ending mid-queue must not
        announce the queue as finished — play_next() (the only speak site)
        is never even invoked automatically here."""
        player = _real_player(config={"autoplay": False})
        a = _track("http://example.com/a.mp3", "A")
        b = _track("http://example.com/b.mp3", "B")
        _load(player, [a, b])
        player.set_player_state(PlayerState.PLAYING)
        player.media_state = MediaState.BUFFERED_MEDIA

        with patch.object(player, "play_next") as mock_play_next:
            player.bus.emit(Message("ovos.common_play.media.state",
                                    {"state": MediaState.END_OF_MEDIA}))
            mock_play_next.assert_not_called()
        player.media.speak_dialog.assert_not_called()
        player.shutdown()

    def test_mpris_track_end_is_silent(self):
        """An MPRIS-external player's track ending must not announce
        queue.finished — ovos-media never controlled that queue."""
        player = _real_player(config={"autoplay": True})
        a = _track("mpris://player/a", "A", playback=PlaybackType.MPRIS)
        _load(player, [a])
        player.handle_playback_ended(
            message=None, playback_type=PlaybackType.MPRIS,
            playback_uri=a.uri, stop_requested=False,
        )
        player.media.speak_dialog.assert_not_called()
        player.shutdown()

    def test_explicit_stop_is_silent(self):
        player = _real_player(config={"autoplay": True})
        a = _track("http://example.com/a.mp3", "A")
        b = _track("http://example.com/b.mp3", "B")
        _load(player, [a, b])
        player.handle_playback_ended(
            message=None, playback_type=PlaybackType.AUDIO,
            playback_uri=a.uri, stop_requested=True,
        )
        player.media.speak_dialog.assert_not_called()
        player.shutdown()

    def test_not_spoken_when_nothing_ever_played(self):
        """PlaybackType.UNDEFINED means no media was ever loaded (eg. an
        explicit stop before any play) - nothing to announce the end of."""
        player = _real_player()
        player.handle_playback_ended(
            message=None, playback_type=PlaybackType.UNDEFINED,
            playback_uri=None, stop_requested=False,
        )
        player.media.speak_dialog.assert_not_called()
        player.shutdown()


if __name__ == "__main__":
    unittest.main()

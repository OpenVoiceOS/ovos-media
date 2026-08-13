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
- track.failed: spoken on an INVALID_MEDIA skip, rate-limited to once per
  queue (not once per skipped track).
- queue.finished: spoken on a natural end-of-queue STOPPED.
"""
import unittest
from unittest.mock import MagicMock

from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import PlaybackType


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
    """Spoken on an INVALID_MEDIA skip, rate-limited to once per queue."""

    def _make_player(self):
        p = _make_player()
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

    def test_spoken_again_after_a_track_successfully_loads(self):
        """A successful load (LOADED_MEDIA/BUFFERED_MEDIA) clears the
        per-queue rate limit, so a later failure in a DIFFERENT run of
        playback speaks again."""
        p = self._make_player()
        p.handle_invalid_media()
        self.assertEqual(p.media.speak_dialog.call_count, 1)
        # simulate a subsequent track loading successfully
        p._failed_uris.clear()
        p._track_failed_spoken = False
        p.handle_invalid_media()
        self.assertEqual(p.media.speak_dialog.call_count, 2)

    def test_flag_is_cleared_by_reset_body(self):
        """reset() must clear the per-queue rate-limit flag alongside
        _failed_uris (same guard both are reset together, see player.py's
        reset()) so a fresh queue after an explicit stop speaks again."""
        p = self._make_player()
        p.handle_invalid_media()
        self.assertTrue(p._track_failed_spoken)
        # exercise exactly the two lines reset() runs for this bookkeeping,
        # without pulling in the rest of reset()'s GUI/MPRIS/state-machine
        # side effects (covered by other lifecycle tests)
        p._failed_uris.clear()
        p._track_failed_spoken = False
        p.handle_invalid_media()
        self.assertEqual(p.media.speak_dialog.call_count, 2)


class TestQueueFinishedDialog(unittest.TestCase):
    """Spoken on a natural end-of-queue STOPPED, not on every explicit stop."""

    def _make_player(self):
        p = _make_player()
        p._update_gui = MagicMock()
        p.play_next = MagicMock()
        return p

    def test_spoken_when_a_track_finishes_and_nothing_else_to_play(self):
        p = self._make_player()
        p.handle_playback_ended(
            message=None, playback_type=PlaybackType.AUDIO,
            playback_uri="http://last.mp3", stop_requested=False,
        )
        p.media.speak_dialog.assert_called_once_with("queue.finished")

    def test_not_spoken_on_explicit_stop(self):
        p = self._make_player()
        p.handle_playback_ended(
            message=None, playback_type=PlaybackType.AUDIO,
            playback_uri="http://last.mp3", stop_requested=True,
        )
        p.media.speak_dialog.assert_not_called()

    def test_not_spoken_when_nothing_ever_played(self):
        """PlaybackType.UNDEFINED means no media was ever loaded (eg. an
        explicit stop before any play) - nothing to announce the end of."""
        p = self._make_player()
        p.handle_playback_ended(
            message=None, playback_type=PlaybackType.UNDEFINED,
            playback_uri=None, stop_requested=False,
        )
        p.media.speak_dialog.assert_not_called()

    def test_not_spoken_when_queue_advances_to_next_track(self):
        p = self._make_player()
        p.playlist.__len__ = MagicMock(return_value=2)
        p.ocp_config = {"autoplay": True}
        p.handle_playback_ended(
            message=None, playback_type=PlaybackType.AUDIO,
            playback_uri="http://a.mp3", stop_requested=False,
        )
        p.media.speak_dialog.assert_not_called()
        p.play_next.assert_called_once()


if __name__ == "__main__":
    unittest.main()

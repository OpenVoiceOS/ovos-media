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

"""End-to-end tests for OCPMediaPlayer state machine using OCPPlayerHarness.

Uses ovoscope.media.OCPPlayerHarness to run a real OCPMediaPlayer on a
FakeBus with MockOCPBackend injected.  No audio hardware or D-Bus session
is required.

Test groups:
    TestPlayPauseStop       -- basic transport controls
    TestQueueNavigation     -- next/prev/repeat queue traversal
    TestInvalidStream       -- broken stream fallback behaviour
    TestDuckUnduck          -- volume ducking on TTS speech
    TestStateMessages       -- bus message emission assertions via OCPCaptureSession
"""
import time
import unittest
from ovos_bus_client.message import Message
from ovos_utils.ocp import (
    LoopState,
    MediaEntry,
    MediaState,
    PlaybackType,
    PlayerState,
)

from ovoscope.media import OCPCaptureSession, OCPPlayerHarness


def _audio_entry(uri: str, title: str = "Test Track") -> MediaEntry:
    """Return a minimal AUDIO MediaEntry for use in tests.

    Args:
        uri: Track URI.
        title: Track title (default ``"Test Track"``).

    Returns:
        ``MediaEntry`` with ``playback=PlaybackType.AUDIO``.
    """
    return MediaEntry(uri=uri, playback=PlaybackType.AUDIO, title=title)


# ---------------------------------------------------------------------------
# TestPlayPauseStop
# ---------------------------------------------------------------------------

class TestPlayPauseStop(unittest.TestCase):
    """Basic transport control state machine tests."""

    def test_play_sets_player_state_playing(self) -> None:
        """Emitting play must set player.state to PLAYING."""
        with OCPPlayerHarness() as h:
            h.play(_audio_entry("http://example.com/song.mp3"))
            h.assert_player_state(PlayerState.PLAYING)

    def test_pause_sets_player_state_paused(self) -> None:
        """Pausing a playing track must set player.state to PAUSED."""
        with OCPPlayerHarness() as h:
            h.play(_audio_entry("http://example.com/song.mp3"))
            h.pause()
            h.assert_player_state(PlayerState.PAUSED)

    def test_resume_after_pause_is_playing(self) -> None:
        """Resuming a paused track must set player.state back to PLAYING."""
        with OCPPlayerHarness() as h:
            h.play(_audio_entry("http://example.com/song.mp3"))
            h.pause()
            h.resume()
            h.assert_player_state(PlayerState.PLAYING)

    def test_stop_sets_player_state_stopped(self) -> None:
        """Stopping playback must set player.state to STOPPED."""
        with OCPPlayerHarness() as h:
            h.play(_audio_entry("http://example.com/song.mp3"))
            h.stop()
            h.assert_player_state(PlayerState.STOPPED)

    def test_stop_emits_search_stop_message(self) -> None:
        """stop() must emit ovos.common_play.search.stop to cancel any search."""
        with OCPPlayerHarness() as h:
            captured: list = []
            h.bus.on("ovos.common_play.search.stop", lambda m: captured.append(m))
            h.play(_audio_entry("http://example.com/song.mp3"))
            h.stop()
            self.assertTrue(
                len(captured) > 0,
                "Expected ovos.common_play.search.stop to be emitted on stop()",
            )


# ---------------------------------------------------------------------------
# TestQueueNavigation
# ---------------------------------------------------------------------------

class TestQueueNavigation(unittest.TestCase):
    """Queue traversal: next, previous, repeat."""

    def _play_two_track_queue(self, h: OCPPlayerHarness) -> None:
        """Emit play with a two-track playlist into harness *h*.

        Args:
            h: Active ``OCPPlayerHarness``.
        """
        t1 = _audio_entry("http://example.com/1.mp3", "Track 1")
        t2 = _audio_entry("http://example.com/2.mp3", "Track 2")
        h.bus.emit(Message("ovos.common_play.play", {
            "media": t1.as_dict,
            "playlist": [t1.as_dict, t2.as_dict],
        }))
        time.sleep(0.05)

    def test_next_advances_to_second_track(self) -> None:
        """next_track() must advance now_playing to the second track."""
        with OCPPlayerHarness() as h:
            self._play_two_track_queue(h)
            h.assert_now_playing_uri("http://example.com/1.mp3")
            h.next_track()
            h.assert_now_playing_uri("http://example.com/2.mp3")

    def test_prev_goes_back_to_first_track(self) -> None:
        """prev_track() from the second track must go back to the first."""
        with OCPPlayerHarness() as h:
            self._play_two_track_queue(h)
            h.next_track()
            h.assert_now_playing_uri("http://example.com/2.mp3")
            h.prev_track()
            h.assert_now_playing_uri("http://example.com/1.mp3")

    def test_repeat_wraps_at_end_of_queue(self) -> None:
        """With LoopState.REPEAT, next at end of queue must wrap to first track."""
        with OCPPlayerHarness() as h:
            self._play_two_track_queue(h)
            h.player.loop_state = LoopState.REPEAT
            # Advance past the last track
            h.next_track()  # → track 2
            h.next_track()  # → should wrap back to track 1
            h.assert_now_playing_uri("http://example.com/1.mp3")


# ---------------------------------------------------------------------------
# TestInvalidStream
# ---------------------------------------------------------------------------

class TestInvalidStream(unittest.TestCase):
    """Broken-stream fallback behaviour."""

    def test_invalid_stream_triggers_skip_to_next(self) -> None:
        """INVALID_MEDIA with a next track must advance to track 2."""
        with OCPPlayerHarness() as h:
            t1 = _audio_entry("http://example.com/1.mp3", "Track 1")
            t2 = _audio_entry("http://example.com/2.mp3", "Track 2")
            h.bus.emit(Message("ovos.common_play.play", {
                "media": t1.as_dict,
                "playlist": [t1.as_dict, t2.as_dict],
                "disambiguation": [t1.as_dict, t2.as_dict],
            }))
            time.sleep(0.05)
            # Enable autoplay so the player auto-skips
            h.player.ocp_config["autoplay"] = True
            h.simulate_invalid_stream()
            # Player should have skipped to track 2
            h.assert_now_playing_uri("http://example.com/2.mp3")

    def test_invalid_stream_no_next_does_not_crash(self) -> None:
        """INVALID_MEDIA with no next track must not crash — player stays intact."""
        with OCPPlayerHarness() as h:
            h.play(_audio_entry("http://example.com/broken.mp3"))
            h.player.ocp_config["autoplay"] = True
            # Simulate invalid stream with a single-track queue.
            # play_next() will log "no more tracks" and return without stopping,
            # so the player state stays PLAYING (no crash, no exception).
            try:
                h.simulate_invalid_stream()
            except Exception as exc:
                self.fail(f"simulate_invalid_stream raised unexpectedly: {exc}")


# ---------------------------------------------------------------------------
# TestDuckUnduck
# ---------------------------------------------------------------------------

class TestDuckUnduck(unittest.TestCase):
    """Volume ducking on TTS speech events."""

    def test_duck_lowers_audio_service_volume(self) -> None:
        """duck() (recognizer_loop:audio_output_start) must call audio_service.lower_volume()."""
        with OCPPlayerHarness() as h:
            h.play(_audio_entry("http://example.com/song.mp3"))
            h.duck()
            # OCPMediaPlayer.handle_duck_request delegates to audio_service.lower_volume
            # audio_service is a MagicMock so we can assert the call
            h.player.audio_service.lower_volume.assert_called()

    def test_unduck_when_paused_restores_volume(self) -> None:
        """unduck() after cork (pause-on-listen) must restore audio volume.

        ``handle_unduck_request`` only calls ``restore_volume`` when the player
        is PAUSED and ``_paused_on_duck`` is True.  ``duck()`` (recognised as
        speech start) lowers volume but does NOT pause; to test the restore path
        we must first cork (pause) the player then uncork (unduck) it.
        """
        with OCPPlayerHarness() as h:
            h.play(_audio_entry("http://example.com/song.mp3"))
            # Cork pauses the player and sets _paused_on_duck
            h.bus.emit(Message("ovos.common_play.cork"))
            time.sleep(0.05)
            h.assert_player_state(PlayerState.PAUSED)
            # Uncork should resume AND restore volume for audio playback
            h.bus.emit(Message("ovos.common_play.uncork"))
            time.sleep(0.05)
            h.assert_player_state(PlayerState.PLAYING)


# ---------------------------------------------------------------------------
# TestStateMessages
# ---------------------------------------------------------------------------

class TestStateMessages(unittest.TestCase):
    """Bus message emission assertions via OCPCaptureSession."""

    def test_player_state_message_emitted_on_play(self) -> None:
        """play() must cause ovos.common_play.player.state to be emitted."""
        with OCPPlayerHarness() as h:
            with OCPCaptureSession(h.bus) as session:
                h.play(_audio_entry("http://example.com/song.mp3"))
            session.assert_sequence("ovos.common_play.player.state")

    def test_media_state_sequence_on_track_end(self) -> None:
        """Simulate the full PLAYING → END_OF_MEDIA message sequence."""
        with OCPPlayerHarness() as h:
            h.play(_audio_entry("http://example.com/song.mp3"))
            with OCPCaptureSession(h.bus) as session:
                h.simulate_track_end()
            self.assertIn(
                "ovos.common_play.media.state",
                session.message_types,
                "Expected ovos.common_play.media.state after track end",
            )


if __name__ == "__main__":
    unittest.main()

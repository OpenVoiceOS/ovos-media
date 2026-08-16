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


def _duck(h: OCPPlayerHarness) -> None:
    """Emit ``ovos.common_play.duck`` directly.

    ``OCPPlayerHarness.duck()`` still emits the removed
    ``recognizer_loop:audio_output_start`` alias, so tests exercise the
    native OCP topic here instead.
    """
    h.bus.emit(Message("ovos.common_play.duck"))
    time.sleep(0.05)


def _unduck(h: OCPPlayerHarness) -> None:
    """Emit ``ovos.common_play.unduck`` directly (see ``_duck``)."""
    h.bus.emit(Message("ovos.common_play.unduck"))
    time.sleep(0.05)


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
            # The skip is deferred (on_invalid_stream) rather than inline
            h.player.invalid_stream_delay = 0.01
            h.simulate_invalid_stream()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if h.player.now_playing.uri == "http://example.com/2.mp3":
                    break
                time.sleep(0.02)
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
    """Volume ducking — lower/restore audio service volume while player keeps playing.

    Ducking is triggered by TTS speech starting (``ovos.common_play.duck``)
    and ending (``ovos.common_play.unduck``).

    Key behavioural invariant: ducking does NOT pause the player.  The audio
    backend continues playing at a lower volume and ``restore_volume`` is called
    when TTS finishes, regardless of player state.

    See ``ovos_media/player.py:handle_duck_request`` and
    ``handle_unduck_request``.
    """

    def test_duck_lowers_audio_backend_volume(self) -> None:
        """duck() must call audio_service.lower_volume() when player is PLAYING.

        ``handle_duck_request`` (``ovos_media/player.py:1216``) delegates to
        ``audio_service.lower_volume()`` for AUDIO playback type.
        """
        with OCPPlayerHarness() as h:
            h.play(_audio_entry("http://example.com/song.mp3"))
            _duck(h)
            h.player.audio_service.lower_volume.assert_called()

    def test_duck_player_remains_playing(self) -> None:
        """Player state must stay PLAYING after duck — only volume is lowered."""
        with OCPPlayerHarness() as h:
            h.play(_audio_entry("http://example.com/song.mp3"))
            _duck(h)
            h.assert_player_state(PlayerState.PLAYING)

    def test_duck_sets_paused_on_duck_flag(self) -> None:
        """duck() must set ``_paused_on_duck = True`` even though player stays playing.

        This flag is read by ``handle_unduck_request`` and
        ``handle_utterance_handled`` to gate volume restoration.
        """
        with OCPPlayerHarness() as h:
            h.play(_audio_entry("http://example.com/song.mp3"))
            _duck(h)
            self.assertTrue(
                h.player._paused_on_duck,
                "Expected _paused_on_duck=True after duck",
            )

    def test_duck_no_op_when_stopped(self) -> None:
        """duck() must be a no-op when player is STOPPED (no speech to lower around)."""
        with OCPPlayerHarness() as h:
            # Do not play anything — player starts STOPPED
            _duck(h)
            h.player.audio_service.lower_volume.assert_not_called()
            self.assertFalse(h.player._paused_on_duck)

    def test_unduck_when_playing_restores_volume(self) -> None:
        """unduck() must restore volume even when player is PLAYING.

        After a pure duck cycle the player stays PLAYING.  ``handle_unduck_request``
        must call ``audio_service.restore_volume()`` and clear ``_paused_on_duck``
        regardless of player state.
        """
        with OCPPlayerHarness() as h:
            h.play(_audio_entry("http://example.com/song.mp3"))
            _duck(h)
            h.assert_player_state(PlayerState.PLAYING)  # still PLAYING after duck
            _unduck(h)
            # restore_volume IS called — no PAUSED guard
            h.player.audio_service.restore_volume.assert_called()
            # _paused_on_duck cleared
            self.assertFalse(h.player._paused_on_duck)

    def test_ocp_duck_message_triggers_duck(self) -> None:
        """``ovos.common_play.duck`` must lower the audio backend volume."""
        with OCPPlayerHarness() as h:
            h.play(_audio_entry("http://example.com/song.mp3"))
            h.bus.emit(Message("ovos.common_play.duck"))
            time.sleep(0.05)
            h.player.audio_service.lower_volume.assert_called()
            h.assert_player_state(PlayerState.PLAYING)


# ---------------------------------------------------------------------------
# TestAudioOutputSpecTopicsDuck
# ---------------------------------------------------------------------------

class TestAudioOutputSpecTopicsDuck(unittest.TestCase):
    """ovos-audio emits 'ovos.audio.output.started'/'ovos.audio.output.ended'
    unconditionally on every TTS output (ovos_audio/playback.py begin_audio/
    end_audio) — the ovos.common_play.duck/cork messages are only emitted
    when tts.ocp_duck/tts.ocp_cork are enabled (both default False). These
    spec topics must be bound to the same duck/unduck handlers so ducking
    works on default installs, not only when that config is opted into.
    """

    def test_audio_output_started_triggers_duck(self) -> None:
        """'ovos.audio.output.started' must lower the audio backend volume."""
        with OCPPlayerHarness() as h:
            h.play(_audio_entry("http://example.com/song.mp3"))
            h.bus.emit(Message("ovos.audio.output.started"))
            time.sleep(0.05)
            h.player.audio_service.lower_volume.assert_called()
            h.assert_player_state(PlayerState.PLAYING)

    def test_audio_output_ended_triggers_unduck(self) -> None:
        """'ovos.audio.output.ended' must restore the audio backend volume."""
        with OCPPlayerHarness() as h:
            h.play(_audio_entry("http://example.com/song.mp3"))
            h.bus.emit(Message("ovos.audio.output.started"))
            time.sleep(0.05)
            h.bus.emit(Message("ovos.audio.output.ended"))
            time.sleep(0.05)
            h.player.audio_service.restore_volume.assert_called()
            self.assertFalse(h.player._paused_on_duck)


# ---------------------------------------------------------------------------
# TestCorkUncork
# ---------------------------------------------------------------------------

class TestCorkUncork(unittest.TestCase):
    """Audio corking — pause/resume the player around microphone listening.

    Corking is triggered by the mic opening (``recognizer_loop:record_begin``
    / ``ovos.common_play.cork``) and closing (``recognizer_loop:record_end``
    / ``ovos.common_play.uncork``).

    Unlike ducking, corking fully **pauses** the player and resumes it after
    the voice interaction completes.  ``handle_record_end`` waits up to 8 s for
    a ``speak`` message; if none arrives it uncorks immediately.

    Key state transitions (``ovos_media/player.py:1198–1255``):
        cork  → state: PLAYING → PAUSED, ``_paused_on_duck = True``
        uncork → state: PAUSED → PLAYING, ``_paused_on_duck = False``
        record_end (no speak within 8 s) → same as uncork
    """

    def test_cork_pauses_player(self) -> None:
        """cork must transition player to PAUSED."""
        with OCPPlayerHarness() as h:
            h.play(_audio_entry("http://example.com/song.mp3"))
            h.cork()
            h.assert_player_state(PlayerState.PAUSED)

    def test_cork_sets_paused_on_duck_flag(self) -> None:
        """cork must set ``_paused_on_duck = True`` to signal auto-resume is pending."""
        with OCPPlayerHarness() as h:
            h.play(_audio_entry("http://example.com/song.mp3"))
            h.cork()
            self.assertTrue(
                h.player._paused_on_duck,
                "Expected _paused_on_duck=True after cork",
            )

    def test_cork_no_op_when_not_playing(self) -> None:
        """cork must be a no-op when the player is already STOPPED."""
        with OCPPlayerHarness() as h:
            # player starts STOPPED
            h.cork()
            h.assert_player_state(PlayerState.STOPPED)
            self.assertFalse(h.player._paused_on_duck)

    def test_legacy_record_begin_triggers_cork(self) -> None:
        """``recognizer_loop:record_begin`` must be equivalent to cork."""
        with OCPPlayerHarness() as h:
            h.play(_audio_entry("http://example.com/song.mp3"))
            h.bus.emit(Message("recognizer_loop:record_begin"))
            time.sleep(0.05)
            h.assert_player_state(PlayerState.PAUSED)
            self.assertTrue(h.player._paused_on_duck)

    def test_uncork_resumes_player(self) -> None:
        """uncork must transition player back to PLAYING."""
        with OCPPlayerHarness() as h:
            h.play(_audio_entry("http://example.com/song.mp3"))
            h.cork()
            h.assert_player_state(PlayerState.PAUSED)
            h.uncork()
            h.assert_player_state(PlayerState.PLAYING)

    def test_uncork_clears_paused_on_duck_flag(self) -> None:
        """uncork must clear ``_paused_on_duck`` so a second uncork is a no-op."""
        with OCPPlayerHarness() as h:
            h.play(_audio_entry("http://example.com/song.mp3"))
            h.cork()
            h.uncork()
            self.assertFalse(
                h.player._paused_on_duck,
                "Expected _paused_on_duck=False after uncork",
            )

    def test_uncork_no_op_without_paused_on_duck(self) -> None:
        """uncork must be a no-op if ``_paused_on_duck`` is False.

        Prevents spurious resume when the player paused for a reason other than
        a cork (e.g. user pressed pause manually).
        """
        with OCPPlayerHarness() as h:
            h.play(_audio_entry("http://example.com/song.mp3"))
            h.pause()
            # _paused_on_duck is False (we paused manually, not via cork)
            self.assertFalse(h.player._paused_on_duck)
            h.uncork()
            # Player must stay PAUSED — uncork was a no-op
            h.assert_player_state(PlayerState.PAUSED)

    def test_record_end_uncorks_when_no_speak_arrives(self) -> None:
        """``recognizer_loop:record_end`` must uncork after 8 s with no ``speak``.

        ``handle_record_end`` (``ovos_media/player.py:1240``) calls
        ``bus.wait_for_message('speak', timeout=8.0)``.  When no speak arrives
        it calls ``handle_uncork_request`` to resume the player.

        This test patches ``bus.wait_for_message`` to return ``None`` immediately
        so the 8-second wait does not slow the suite.
        """
        from unittest.mock import patch as _patch

        with OCPPlayerHarness() as h:
            h.play(_audio_entry("http://example.com/song.mp3"))
            h.cork()
            h.assert_player_state(PlayerState.PAUSED)

            # Patch wait_for_message to return None immediately (no speak)
            with _patch.object(h.bus, "wait_for_message", return_value=None):
                h.bus.emit(Message("recognizer_loop:record_end"))
                time.sleep(0.05)

            h.assert_player_state(PlayerState.PLAYING)

    def test_record_end_stays_corked_when_speak_arrives(self) -> None:
        """``recognizer_loop:record_end`` must NOT uncork if a ``speak`` is detected.

        When a ``speak`` message is detected within 8 s, ``handle_record_end``
        returns without uncorking — the player stays PAUSED until the TTS
        finishes and ``ovos.utterance.handled`` fires.
        """
        from unittest.mock import patch as _patch

        with OCPPlayerHarness() as h:
            h.play(_audio_entry("http://example.com/song.mp3"))
            h.cork()
            h.assert_player_state(PlayerState.PAUSED)

            # Patch wait_for_message to simulate speak arriving within 8 s
            fake_speak = Message("speak", {"utterance": "hello"})
            with _patch.object(h.bus, "wait_for_message",
                               return_value=fake_speak):
                h.bus.emit(Message("recognizer_loop:record_end"))
                time.sleep(0.05)

            # Player must still be PAUSED — speak was detected, no uncork yet
            h.assert_player_state(PlayerState.PAUSED)


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

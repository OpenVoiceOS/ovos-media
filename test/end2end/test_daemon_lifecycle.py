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

"""End-to-end tests for the ovos-media daemon through the real OCPMediaPlayer.

These tests drive the full media daemon — a real :class:`OCPMediaPlayer` on a
``FakeBus`` with heavy deps + GUI mocked — via ``ovoscope.media.OCPPlayerHarness``.
They complement ``test_ocp_player.py`` (transport/duck/cork) by covering the
daemon-level concerns the prompt calls out:

Test groups:
    TestTransportLifecycle      -- play→pause→resume→stop + now_playing + track-end
    TestPlaylistNavigation      -- queue nav, next/prev now_playing, can_next/can_prev
    TestBackendRouting          -- AUDIO→audio svc, VIDEO→video svc, WEBVIEW→web svc
    TestExternalMprisNowPlaying -- ovos.common_play.mpris.now_playing reflection +
                                   takeover stopping OCP's own backends
    TestSeekAndPosition         -- seek / set_track_position / get_track_position
    TestDuckCork                -- duck/unduck + cork/uncork at the daemon level

Run locally with::

    PYTHONPATH=. python -m pytest test/end2end/ -q -p no:ovoscope

(``-p no:ovoscope`` avoids the ovoscope pytest-plugin autoload.)
"""
import time
import unittest
from unittest.mock import patch

from ovos_bus_client.message import Message
from ovos_utils.ocp import (
    MediaEntry,
    MediaState,
    PlaybackType,
    PlayerState,
)

from ovoscope.media import OCPPlayerHarness


def _audio(uri: str, title: str = "Test Track") -> MediaEntry:
    """Return a minimal AUDIO ``MediaEntry``."""
    return MediaEntry(uri=uri, playback=PlaybackType.AUDIO, title=title)


def _gui_present():
    """Context-manager patch making the player believe a GUI is connected.

    ``OCPMediaPlayer.validate_stream`` downgrades VIDEO/WEBVIEW playback to
    AUDIO when no GUI is present (headless = audio-only).  To exercise the
    real VIDEO / WEBVIEW routing branches in :meth:`OCPMediaPlayer.play` we
    must convince the player a GUI is available.

    Returns a tuple of two ``patch`` context managers; use with
    ``contextlib.ExitStack`` or nest them.
    """
    return (
        patch("ovos_media.player.is_gui_running", return_value=True),
        patch("ovos_media.player.is_gui_connected", return_value=True),
    )


# ---------------------------------------------------------------------------
# TestTransportLifecycle
# ---------------------------------------------------------------------------

class TestTransportLifecycle(unittest.TestCase):
    """Full transport lifecycle through the real daemon player."""

    def test_full_play_pause_resume_stop_cycle(self) -> None:
        """play→PLAYING, pause→PAUSED, resume→PLAYING, stop→STOPPED."""
        with OCPPlayerHarness() as h:
            uri = "http://example.com/song.mp3"
            h.play(_audio(uri))
            h.assert_player_state(PlayerState.PLAYING)
            h.assert_now_playing_uri(uri)

            h.pause()
            h.assert_player_state(PlayerState.PAUSED)

            h.resume()
            h.assert_player_state(PlayerState.PLAYING)

            h.stop()
            h.assert_player_state(PlayerState.STOPPED)

    def test_now_playing_uri_reflects_played_track(self) -> None:
        """now_playing.uri must reflect the URI handed to play()."""
        with OCPPlayerHarness() as h:
            h.play(_audio("http://example.com/abc.mp3"))
            h.assert_now_playing_uri("http://example.com/abc.mp3")

    def test_track_end_updates_media_state(self) -> None:
        """simulate_track_end must drive media_state to END_OF_MEDIA.

        With a single-track queue and no autoplay there is nothing to advance
        to, so the player records END_OF_MEDIA rather than rolling over.
        """
        with OCPPlayerHarness() as h:
            h.play(_audio("http://example.com/song.mp3"))
            h.player.ocp_config["autoplay"] = False
            h.simulate_track_end()
            h.assert_media_state(MediaState.END_OF_MEDIA)


# ---------------------------------------------------------------------------
# TestPlaylistNavigation
# ---------------------------------------------------------------------------

class TestPlaylistNavigation(unittest.TestCase):
    """Queue navigation: next/prev move now_playing; can_next / can_prev."""

    def _queue_three(self, h: OCPPlayerHarness) -> None:
        """Emit play with a three-track playlist into harness *h*."""
        t1 = _audio("http://example.com/1.mp3", "Track 1")
        t2 = _audio("http://example.com/2.mp3", "Track 2")
        t3 = _audio("http://example.com/3.mp3", "Track 3")
        h.bus.emit(Message("ovos.common_play.play", {
            "media": t1.as_dict,
            "playlist": [t1.as_dict, t2.as_dict, t3.as_dict],
        }))
        time.sleep(0.05)

    def test_next_then_prev_moves_now_playing(self) -> None:
        """next_track / prev_track must move now_playing across the queue."""
        with OCPPlayerHarness() as h:
            self._queue_three(h)
            h.assert_now_playing_uri("http://example.com/1.mp3")
            h.next_track()
            h.assert_now_playing_uri("http://example.com/2.mp3")
            h.next_track()
            h.assert_now_playing_uri("http://example.com/3.mp3")
            h.prev_track()
            h.assert_now_playing_uri("http://example.com/2.mp3")

    def test_can_next_true_until_last_track(self) -> None:
        """playlist.is_last_track must be False until the queue's final track."""
        with OCPPlayerHarness() as h:
            self._queue_three(h)
            pl = h.player.playlist
            self.assertFalse(pl.is_last_track, "Expected a next at queue start")
            h.next_track()
            h.next_track()  # now on the last (3rd) track
            self.assertTrue(pl.is_last_track, "Expected no next on last track")

    def test_can_prev_false_at_queue_start(self) -> None:
        """playlist.is_first_track must be True on track 1, False after next."""
        with OCPPlayerHarness() as h:
            self._queue_three(h)
            pl = h.player.playlist
            self.assertTrue(pl.is_first_track, "Expected no prev on first track")
            h.next_track()
            self.assertFalse(pl.is_first_track, "Expected a prev after advancing")


# ---------------------------------------------------------------------------
# TestBackendRouting
# ---------------------------------------------------------------------------

class TestBackendRouting(unittest.TestCase):
    """The three backend types route to the matching media service.

    The daemon routes by ``PlaybackType``:
        AUDIO   -> audio_service.play
        VIDEO   -> video_service.play
        WEBVIEW -> web_service.play

    A GUI must appear present, otherwise ``validate_stream`` downgrades
    VIDEO/WEBVIEW to AUDIO (headless audio-only fallback).  The three media
    services are MagicMocks in the harness, so we assert which service's
    ``play`` was invoked.
    """

    def test_audio_entry_routes_to_audio_service(self) -> None:
        """An AUDIO entry must reach audio_service.play and not video/web."""
        with OCPPlayerHarness() as h:
            h.play(_audio("http://example.com/song.mp3"))
            self.assertTrue(h.player.audio_service.play.called)
            self.assertFalse(h.player.video_service.play.called)
            self.assertFalse(h.player.web_service.play.called)

    def test_video_entry_routes_to_video_service(self) -> None:
        """A VIDEO entry must reach video_service.play (GUI present)."""
        with OCPPlayerHarness() as h:
            running, connected = _gui_present()
            with running, connected:
                h.play(MediaEntry(uri="http://example.com/clip.mp4",
                                  playback=PlaybackType.VIDEO))
            self.assertTrue(h.player.video_service.play.called)
            self.assertFalse(h.player.audio_service.play.called)
            self.assertFalse(h.player.web_service.play.called)

    def test_webview_entry_routes_to_web_service(self) -> None:
        """A WEBVIEW entry must reach web_service.play (GUI present)."""
        with OCPPlayerHarness() as h:
            running, connected = _gui_present()
            with running, connected:
                h.play(MediaEntry(uri="http://example.com/page",
                                  playback=PlaybackType.WEBVIEW))
            self.assertTrue(h.player.web_service.play.called)
            self.assertFalse(h.player.audio_service.play.called)
            self.assertFalse(h.player.video_service.play.called)

    def test_headless_video_downgrades_to_audio(self) -> None:
        """Without a GUI a VIDEO entry must fall back to the audio service.

        This is the documented headless behaviour: no display surface means
        playback is forced through audio only.
        """
        with OCPPlayerHarness() as h:
            # no GUI patch — is_gui_running()/is_gui_connected() are False
            h.play(MediaEntry(uri="http://example.com/clip.mp4",
                              playback=PlaybackType.VIDEO))
            self.assertTrue(h.player.audio_service.play.called)
            self.assertFalse(h.player.video_service.play.called)


# ---------------------------------------------------------------------------
# TestExternalMprisNowPlaying
# ---------------------------------------------------------------------------

class TestExternalMprisNowPlaying(unittest.TestCase):
    """External-MPRIS now_playing reflection via the bus primitive.

    ``ovos.common_play.mpris.now_playing`` lets an out-of-process MPRIS watcher
    reflect an external player (Spotify, a browser, VLC, …) into OCP without
    OCP driving playback.  See ``OCPMediaPlayer.set_external_now_playing``.
    """

    def _emit_external(self, h: OCPPlayerHarness, **data) -> None:
        """Emit an external MPRIS now_playing message with *data*."""
        h.bus.emit(Message("ovos.common_play.mpris.now_playing", data))
        time.sleep(0.05)

    def test_external_playing_reflected_as_now_playing(self) -> None:
        """External 'Playing' must set now_playing + PLAYING + MPRIS type."""
        with OCPPlayerHarness() as h:
            self._emit_external(
                h,
                external_player="spotify",
                uri="spotify:track:abc",
                title="External Song",
                artist="Some Artist",
                state="Playing",
            )
            h.assert_player_state(PlayerState.PLAYING)
            self.assertEqual(h.player.playback_type, PlaybackType.MPRIS)
            self.assertEqual(h.player.now_playing.title, "External Song")

    def test_external_paused_reflected_as_paused(self) -> None:
        """External 'Paused' state must reflect as PlayerState.PAUSED."""
        with OCPPlayerHarness() as h:
            self._emit_external(
                h,
                external_player="vlc",
                uri="file:///music/x.flac",
                title="Paused Track",
                state="Paused",
            )
            h.assert_player_state(PlayerState.PAUSED)
            self.assertEqual(h.player.playback_type, PlaybackType.MPRIS)

    def test_external_takeover_stops_current_backend(self) -> None:
        """A NEW external 'Playing' player must stop OCP's own backends.

        ``set_external_now_playing`` calls ``handle_MPRIS_takeover`` on the
        transition, which stops audio/video/web services so the external
        player and OCP do not overlap.
        """
        with OCPPlayerHarness() as h:
            # OCP is playing its own AUDIO track first
            h.play(_audio("http://example.com/song.mp3"))
            self.assertFalse(h.player.audio_service.stop.called)

            self._emit_external(
                h,
                external_player="spotify",
                uri="spotify:track:abc",
                title="External Song",
                state="Playing",
            )
            # takeover stopped OCP's own audio backend
            self.assertTrue(h.player.audio_service.stop.called)
            self.assertTrue(h.player.video_service.stop.called)
            self.assertTrue(h.player.web_service.stop.called)

    def test_external_no_player_id_is_ignored(self) -> None:
        """An external message with no player id must be ignored (no crash)."""
        with OCPPlayerHarness() as h:
            try:
                self._emit_external(h, uri="x://y", title="No ID", state="Playing")
            except Exception as exc:  # pragma: no cover
                self.fail(f"external now_playing with no id raised: {exc}")
            self.assertNotEqual(h.player.playback_type, PlaybackType.MPRIS)


# ---------------------------------------------------------------------------
# TestSeekAndPosition
# ---------------------------------------------------------------------------

class TestSeekAndPosition(unittest.TestCase):
    """seek / set_track_position / get_track_position bus APIs."""

    def test_set_track_position_seeks_audio_backend(self) -> None:
        """set_track_position must seek the audio service (milliseconds passthrough,
        per the OPM MediaBackend contract)."""
        with OCPPlayerHarness() as h:
            h.play(_audio("http://example.com/song.mp3"))
            h.bus.emit(Message("ovos.common_play.set_track_position",
                               {"position": 5000}))
            time.sleep(0.05)
            h.player.audio_service.set_track_position.assert_called_with(5000)

    def test_seek_request_moves_audio_position(self) -> None:
        """A seek request must reach audio_service.set_track_position."""
        with OCPPlayerHarness() as h:
            h.play(_audio("http://example.com/song.mp3"))
            # seekValue takes the absolute-position branch (ms passed straight
            # through to seek() -> set_track_position(ms))
            h.bus.emit(Message("ovos.common_play.seek", {"seekValue": 8000}))
            time.sleep(0.05)
            h.player.audio_service.set_track_position.assert_called_with(8000)

    def test_get_track_position_returns_backend_position(self) -> None:
        """get_track_position must reply with the audio backend's position."""
        with OCPPlayerHarness() as h:
            h.play(_audio("http://example.com/song.mp3"))
            h.player.audio_service.get_track_position.return_value = 4200
            got: dict = {}
            h.bus.on("ovos.common_play.get_track_position.response",
                     lambda m: got.update(m.data))
            h.bus.emit(Message("ovos.common_play.get_track_position"))
            time.sleep(0.05)
            self.assertEqual(got.get("position"), 4200)


# ---------------------------------------------------------------------------
# TestDuckCork
# ---------------------------------------------------------------------------

class TestDuckCork(unittest.TestCase):
    """Duck/unduck and cork/uncork reached through the daemon player."""

    def test_duck_unduck_cycle_keeps_playing(self) -> None:
        """duck lowers volume (still PLAYING); unduck restores it.

        Emits ``ovos.common_play.duck``/``unduck`` directly —
        ``OCPPlayerHarness.duck()``/``unduck()`` still emit the removed
        ``recognizer_loop:audio_output_start``/``_end`` aliases.
        """
        with OCPPlayerHarness() as h:
            h.play(_audio("http://example.com/song.mp3"))
            h.bus.emit(Message("ovos.common_play.duck"))
            time.sleep(0.05)
            h.player.audio_service.lower_volume.assert_called()
            h.assert_player_state(PlayerState.PLAYING)
            h.bus.emit(Message("ovos.common_play.unduck"))
            time.sleep(0.05)
            h.player.audio_service.restore_volume.assert_called()

    def test_cork_uncork_cycle_pauses_then_resumes(self) -> None:
        """cork pauses the player; uncork resumes it."""
        with OCPPlayerHarness() as h:
            h.play(_audio("http://example.com/song.mp3"))
            h.cork()
            h.assert_player_state(PlayerState.PAUSED)
            h.uncork()
            h.assert_player_state(PlayerState.PLAYING)


if __name__ == "__main__":
    unittest.main()

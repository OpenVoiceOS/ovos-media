"""Regression tests for the 2026-08 wave-4 defect fixes.

W4-1  BaseMediaService.stop() invoked the player-wide ``on_stop`` callback
      (which flags the single, player-wide ``_stop_requested``) even when
      THIS service instance had no active backend at all. All three services
      (audio/video/web) share the one flag, so a skill calling the video
      interface's stop() while only audio was playing suppressed the next
      natural audio END_OF_MEDIA advance.

W4-2  ``_mark_stop_requested`` did not cancel the pending invalid-stream
      retry timer (``_invalid_timer``), and neither did
      ``OCPMediaPlayer.stop()`` (only reset()/shutdown() did). A stop
      arriving during the 3s post-INVALID_MEDIA retry window left the timer
      armed; it fired play_next() after the stop had already settled the
      player into STOPPED, spontaneously resuming playback.

Both tests drive the scenario through the real objects (FakeBus + a stub
backend), mirroring test_wave3_end_of_track.py.
"""
import time
import unittest

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import MediaEntry, MediaState, PlaybackType, PlayerState


def _track(uri, title, playback=PlaybackType.AUDIO):
    return MediaEntry(uri=uri, title=title, playback=playback)


def _make_player(bus=None, config=None):
    from ovos_media.player import OCPMediaPlayer
    bus = bus or FakeBus()
    player = OCPMediaPlayer(bus, config=config if config is not None else {})
    player.now_playing.extract_stream = lambda: None
    return player


def _load(player, entries):
    player.playlist.clear()
    for e in entries:
        player.playlist.add_entry(e)
    player.set_now_playing(player.playlist[0])


class _StubBackend:
    """Minimal MediaBackend stand-in mirroring the OPM template's stop path."""

    def __init__(self, bus, name="stub", uris=("http", "https", "file")):
        self.bus = bus
        self.name = name
        self.aliases = [name]
        self._uris = list(uris)
        self.loaded = []
        self.stopped = 0
        self._playing = False

    def supported_uris(self):
        return self._uris

    def set_track_start_callback(self, cb):
        self._cb = cb

    def load_track(self, uri):
        self.loaded.append(uri)
        self._playing = True

    def play(self, repeat=False):
        pass

    def stop(self):
        self.stopped += 1
        was = self._playing
        self._playing = False
        return was

    def ocp_stop(self):
        self.bus.emit(Message("ovos.common_play.player.state",
                              {"state": PlayerState.STOPPED}))
        self.bus.emit(Message("ovos.common_play.media.state",
                              {"state": MediaState.END_OF_MEDIA}))

    def shutdown(self):
        pass


class _InvalidBackend:
    """Backend that always reports INVALID_MEDIA when asked to play."""

    def __init__(self, bus, name="badstub", uris=("http", "https", "file")):
        self.bus = bus
        self.name = name
        self.aliases = [name]
        self._uris = list(uris)
        self.stopped = 0
        self._playing = False

    def supported_uris(self):
        return self._uris

    def set_track_start_callback(self, cb):
        self._cb = cb

    def load_track(self, uri):
        # mirrors a real backend that fails to load and reports INVALID_MEDIA
        self.bus.emit(Message("ovos.common_play.media.state",
                              {"state": MediaState.INVALID_MEDIA}))

    def play(self, repeat=False):
        pass

    def stop(self):
        self.stopped += 1
        was = self._playing
        self._playing = False
        return was

    def ocp_stop(self):
        self.bus.emit(Message("ovos.common_play.player.state",
                              {"state": PlayerState.STOPPED}))
        self.bus.emit(Message("ovos.common_play.media.state",
                              {"state": MediaState.END_OF_MEDIA}))

    def shutdown(self):
        pass


# ---------------------------------------------------------------------------
# W4-1: on_stop must only fire for the service that actually has a backend
# ---------------------------------------------------------------------------

class TestStopScopedToActiveService(unittest.TestCase):

    def test_stopping_idle_video_service_does_not_suppress_audio_advance(self):
        """Stopping the (idle) video service while audio plays must not
        suppress the next real audio END_OF_MEDIA advance.
        """
        bus = FakeBus()
        player = _make_player(bus)
        audio_backend = _StubBackend(bus, name="audio_stub")
        player.audio_service.services = [audio_backend]
        player.audio_service.current = audio_backend
        audio_backend._playing = True

        a = _track("http://example.com/a.mp3", "A")
        b = _track("http://example.com/b.mp3", "B")
        _load(player, [a, b])
        player.set_player_state(PlayerState.PLAYING)
        player.media_state = MediaState.BUFFERED_MEDIA

        # video service has no active backend at all
        self.assertIsNone(player.video_service.current)

        # a skill (or OCPVideoServiceInterface.stop()) stops the idle video
        # service while audio is genuinely playing
        bus.emit(Message("ovos.video.service.stop"))

        # the player's stop-requested flag must NOT have been set by the
        # idle video service's stop
        self.assertFalse(player._stop_requested,
                         "stopping an idle video service flagged a "
                         "player-wide stop request")

        # a real, natural end of the audio track must still advance
        bus.emit(Message("ovos.common_play.media.state",
                         {"state": MediaState.END_OF_MEDIA}))

        self.assertEqual(player.now_playing.uri, b.uri,
                         "natural END_OF_MEDIA failed to advance after an "
                         "idle sibling service was stopped")
        player.shutdown()


# ---------------------------------------------------------------------------
# W4-2: stop must cancel the pending invalid-stream retry
# ---------------------------------------------------------------------------

class TestStopCancelsInvalidRetry(unittest.TestCase):

    def _player_with_invalid_backend(self):
        bus = FakeBus()
        player = _make_player(bus)
        player.invalid_stream_delay = 0.15
        backend = _InvalidBackend(bus)
        player.audio_service.services = [backend]
        player.audio_service.current = backend
        a = _track("http://example.com/a.mp3", "A")
        b = _track("http://example.com/b.mp3", "B")
        _load(player, [a, b])
        return bus, player, backend, a, b

    def test_external_service_stop_during_retry_window_stays_stopped(self):
        """INVALID_MEDIA arms a delayed play_next(); an external
        'ovos.audio.service.stop' arriving inside that window must cancel it,
        not let it fire afterwards and resurrect playback.
        """
        bus, player, backend, a, b = self._player_with_invalid_backend()

        player.play()
        # let INVALID_MEDIA propagate and on_invalid_stream() arm the timer
        time.sleep(0.05)
        self.assertIsNotNone(player._invalid_timer,
                             "invalid-stream retry timer was not armed")

        # simulate the backend having something to actually stop, so
        # BaseMediaService._perform_stop signals on_stop()
        backend._playing = True
        player.audio_service.play_start_time = time.monotonic() - 5
        bus.emit(Message("ovos.audio.service.stop"))

        self.assertIsNone(player._invalid_timer,
                          "stop() did not cancel the pending invalid-stream "
                          "retry timer")

        # wait past the original retry window. Note: an external
        # 'ovos.{ns}.service.stop' does not by itself set player.state (only
        # OCPMediaPlayer.stop() does that) — the observable defect here is
        # the deferred play_next() firing and silently advancing the queue
        # after the stop had already been acted on.
        self.assertIsNone(player._invalid_timer,
                          "invalid-stream retry timer reappeared before the "
                          "wait")
        time.sleep(0.3)
        self.assertIsNone(player._invalid_timer,
                          "a new invalid-stream retry timer was armed after "
                          "the stop, meaning the old one fired")
        self.assertNotEqual(player.now_playing.uri, b.uri,
                            "the deferred play_next() fired after stop and "
                            "loaded the next track anyway")
        player.shutdown()

    def test_ocp_player_stop_during_retry_window_stays_stopped(self):
        """Same scenario, but through OCPMediaPlayer.stop() (eg. the
        MPRIS-Stop -> player.stop() path) instead of the external bus event.
        """
        bus, player, backend, a, b = self._player_with_invalid_backend()

        player.play()
        time.sleep(0.05)
        self.assertIsNotNone(player._invalid_timer,
                             "invalid-stream retry timer was not armed")

        backend._playing = True
        player.audio_service.play_start_time = time.monotonic() - 5
        player.stop()

        self.assertIsNone(player._invalid_timer,
                          "OCPMediaPlayer.stop() did not cancel the pending "
                          "invalid-stream retry timer")

        time.sleep(0.3)

        self.assertEqual(player.state, PlayerState.STOPPED,
                         "player spontaneously left STOPPED after the "
                         "invalid-stream retry window elapsed")
        self.assertNotEqual(player.now_playing.uri, b.uri,
                            "the deferred play_next() fired after stop and "
                            "loaded the next track anyway")
        player.shutdown()


# ---------------------------------------------------------------------------
# W4-3: play() must cancel a pending invalid-stream retry from an EARLIER
# track — otherwise a new play() request arriving inside the retry window
# leaves the stale timer armed against the NEW track.
# ---------------------------------------------------------------------------

class TestPlayCancelsStaleInvalidRetry(unittest.TestCase):

    def _arm_stale_timer(self, bus, player):
        """Play an always-invalid track to arm player._invalid_timer, then
        return once it is confirmed armed."""
        backend = _InvalidBackend(bus)
        player.invalid_stream_delay = 0.15
        player.audio_service.services = [backend]
        player.audio_service.current = backend
        bad = _track("http://example.com/bad.mp3", "Bad")
        _load(player, [bad])

        player.play()
        time.sleep(0.05)
        self.assertIsNotNone(player._invalid_timer,
                             "invalid-stream retry timer was not armed")

    def test_new_play_cancels_stale_timer_before_it_skips_the_new_track(self):
        """A new play() of a valid, multi-track queue arriving inside the
        retry window must cancel the stale timer. If it does not, the timer
        fires play_next() against the NEW now_playing (identity-matched via
        _current_entry, not by uri) and silently skips it mid-playback.
        """
        bus = FakeBus()
        player = _make_player(bus)
        self._arm_stale_timer(bus, player)

        # a genuinely new play request for an unrelated, valid, multi-track
        # queue arrives while the stale timer from the earlier bad track is
        # still pending
        good_backend = _StubBackend(bus, name="good_stub")
        player.audio_service.services = [good_backend]
        player.audio_service.current = good_backend
        c = _track("http://example.com/c.mp3", "C")
        d = _track("http://example.com/d.mp3", "D")
        _load(player, [c, d])
        player.play()
        player.set_player_state(PlayerState.PLAYING)

        self.assertIsNone(player._invalid_timer,
                          "play() did not cancel the stale invalid-stream "
                          "retry timer left over from the earlier track")

        # wait past the original retry window
        time.sleep(0.3)

        self.assertEqual(player.now_playing.uri, c.uri,
                         "the stale timer fired and skipped the new track "
                         "(now_playing advanced to the next queue entry)")
        self.assertEqual(player.state, PlayerState.PLAYING,
                         "the stale timer's play_next() disturbed player "
                         "state for the unrelated new track")
        self.assertEqual(good_backend.loaded, [c.uri],
                         "the stale timer asked the backend to load another "
                         "track that was never requested")
        self.assertEqual(good_backend.stopped, 0,
                         "the stale timer stopped the backend for the "
                         "unrelated new track")
        player.shutdown()

    def test_new_play_last_in_queue_stays_playing_not_stopped(self):
        """Same scenario, but the new play() is for a single-track (i.e.
        last-in-queue) selection. If the stale timer is not cancelled, its
        play_next() finds no next entry and drives PlayerState.STOPPED even
        though the new track's audio keeps playing.
        """
        bus = FakeBus()
        player = _make_player(bus)
        self._arm_stale_timer(bus, player)

        good_backend = _StubBackend(bus, name="good_stub_solo")
        player.audio_service.services = [good_backend]
        player.audio_service.current = good_backend
        c = _track("http://example.com/c-solo.mp3", "C-solo")
        _load(player, [c])
        player.play()
        player.set_player_state(PlayerState.PLAYING)

        self.assertIsNone(player._invalid_timer,
                          "play() did not cancel the stale invalid-stream "
                          "retry timer left over from the earlier track")

        time.sleep(0.3)

        self.assertEqual(player.state, PlayerState.PLAYING,
                         "the stale timer's play_next() drove the player to "
                         "STOPPED even though the new (last-in-queue) track "
                         "kept playing")
        self.assertEqual(player.now_playing.uri, c.uri)
        self.assertEqual(good_backend.stopped, 0,
                         "the stale timer stopped the backend for the "
                         "unrelated new track")
        player.shutdown()


if __name__ == "__main__":
    unittest.main()

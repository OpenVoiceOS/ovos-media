"""Regression tests pinning end-of-track handling under concurrent bus dispatch.

The end-of-track path has TWO independent subscribers to
'ovos.common_play.media.state' — NowPlaying.handle_media_state_change and
OCPMediaPlayer.handle_player_media_update — with no ordering guarantee between
them (pyee's ExecutorEventEmitter submits every handler to a thread pool
independently). They coordinate through a stash on the player. Each test below
pins one invariant that coordination must hold:

- under contention the autoplay decision must not read a half-reset NowPlaying
  and silently drop the advance;
- two END_OF_MEDIA events must not both pass the compare-and-set and advance
  the queue twice;
- an explicit stop must not advance the queue, even though OPM backends emit
  END_OF_MEDIA from ocp_stop() same as a track ending naturally;
- a playlist with a repeated uri ([a, b, a]) must not ping-pong forever from
  locating the current track by uri alone;
- a 1-track repeat playlist on a permanently failing backend must not spin
  without bound from INVALID_MEDIA calling play_next() inline;
- a stop within 1s of playback starting must not be dropped silently, leaving
  the player reporting STOPPED while audio keeps playing.

Every test drives the scenario through the real objects (FakeBus + a stub
backend), never by hand-calling the handler under test in isolation.
"""
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import (
    LoopState,
    MediaEntry,
    MediaState,
    PlaybackType,
    PlayerState,
)


def _track(uri, title, playback=PlaybackType.AUDIO):
    return MediaEntry(uri=uri, title=title, playback=playback)


def _make_player(bus=None, config=None):
    """Real OCPMediaPlayer on a FakeBus, with stream extraction stubbed out."""
    from ovos_media.player import OCPMediaPlayer
    bus = bus or FakeBus()
    player = OCPMediaPlayer(bus, config=config if config is not None else {})
    # extract_stream() would hit the network; the uris here are already streams
    player.now_playing.extract_stream = lambda: None
    return player


def _load(player, entries):
    """Load *entries* as the player's playlist and select the first one."""
    player.playlist.clear()
    for e in entries:
        player.playlist.add_entry(e)
    player.set_now_playing(player.playlist[0])


class _StubBackend:
    """Minimal MediaBackend stand-in mirroring the OPM template's stop path.

    ``ocp_stop()`` emits PlayerState.STOPPED then MediaState.END_OF_MEDIA,
    exactly as ovos_plugin_manager.templates.media.MediaBackend does — which is
    what makes an external stop indistinguishable from a track ending.
    """

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


# ---------------------------------------------------------------------------
# W3-1 / W3-2: single writer, deterministic advance
# ---------------------------------------------------------------------------

class TestSingleWriterEndOfMedia(unittest.TestCase):

    def test_raced_end_of_media_handlers_still_advance(self):
        """END_OF_MEDIA delivered while other media.state subscribers run
        concurrently must still advance the queue, every time.

        Pre-fix this lost roughly 15% of advances (29/200 with barriers): the
        NowPlaying subscriber reset now_playing.playback to UNDEFINED before
        the player's handler read it, and the UNDEFINED guard suppressed the
        autoplay.
        """
        for attempt in range(40):
            bus = FakeBus()
            player = _make_player(bus)
            a = _track("http://example.com/a.mp3", "A")
            b = _track("http://example.com/b.mp3", "B")
            _load(player, [a, b])
            player.set_player_state(PlayerState.PLAYING)
            player.media_state = MediaState.BUFFERED_MEDIA

            barrier = threading.Barrier(2)

            def racer(_msg):
                # a competing media.state subscriber, released simultaneously
                barrier.wait(timeout=5)

            bus.on("ovos.common_play.media.state", racer)

            with patch.object(player, "play"):
                t = threading.Thread(target=barrier.wait, kwargs={"timeout": 5})
                t.start()
                bus.emit(Message("ovos.common_play.media.state",
                                 {"state": MediaState.END_OF_MEDIA}))
                t.join(timeout=5)

            self.assertEqual(player.now_playing.uri, b.uri,
                             f"attempt {attempt}: end of track A did not "
                             f"advance to B")
            player.shutdown()

    def test_late_now_playing_reset_cannot_wipe_the_next_track(self):
        """The adverse interleaving, simulated directly.

        Pre-fix, the two media.state subscribers had no ordering guarantee. If
        the player's handler won the race it advanced to track B — and then the
        OTHER subscriber (NowPlaying) ran and reset now_playing, wiping the
        track that had just been selected. Post-fix the player is the sole
        subscriber, so no late handler exists that can undo the advance.
        """
        bus = FakeBus()
        player = _make_player(bus)
        a = _track("http://example.com/a.mp3", "A")
        b = _track("http://example.com/b.mp3", "B")
        _load(player, [a, b])
        player.set_player_state(PlayerState.PLAYING)
        player.media_state = MediaState.BUFFERED_MEDIA

        msg = Message("ovos.common_play.media.state",
                      {"state": MediaState.END_OF_MEDIA})
        with patch.object(player, "play"):
            bus.emit(msg)
            self.assertEqual(player.now_playing.uri, b.uri)
            # now let every OTHER media.state subscriber process the same
            # event, as a losing thread would have done
            for fn in bus.ee.listeners("ovos.common_play.media.state"):
                if getattr(fn, "__self__", None) is not player:
                    fn(msg)

        self.assertEqual(player.now_playing.uri, b.uri,
                         "a late media.state subscriber wiped the track the "
                         "player had just advanced to")
        player.shutdown()

    def test_two_concurrent_end_of_media_advance_exactly_once(self):
        """Two END_OF_MEDIA events racing must advance the queue ONCE.

        Pre-fix this double-advanced in 263/300 runs: both threads passed the
        unsynchronised `state == self.media_state` compare-and-set.
        """
        for attempt in range(40):
            bus = FakeBus()
            player = _make_player(bus)
            tracks = [_track(f"http://example.com/{n}.mp3", n)
                      for n in ("a", "b", "c")]
            _load(player, tracks)
            player.set_player_state(PlayerState.PLAYING)
            player.media_state = MediaState.BUFFERED_MEDIA

            with patch.object(player, "play"):
                start = threading.Barrier(2)

                def fire():
                    start.wait(timeout=5)
                    bus.emit(Message("ovos.common_play.media.state",
                                     {"state": MediaState.END_OF_MEDIA}))

                threads = [threading.Thread(target=fire) for _ in range(2)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=5)

            self.assertEqual(player.now_playing.uri, tracks[1].uri,
                             f"attempt {attempt}: two concurrent END_OF_MEDIA "
                             f"events advanced past track b")
            player.shutdown()

    def test_now_playing_no_longer_subscribes_to_media_state(self):
        """NowPlaying must not be a second subscriber to the topic."""
        bus = FakeBus()
        player = _make_player(bus)
        listeners = bus.ee.listeners("ovos.common_play.media.state")
        owners = [getattr(fn, "__self__", None) for fn in listeners]
        self.assertNotIn(player.now_playing, owners)
        self.assertIn(player, owners)
        player.shutdown()


# ---------------------------------------------------------------------------
# W3-3: stop must not advance
# ---------------------------------------------------------------------------

class TestStopDoesNotAdvance(unittest.TestCase):

    def _player_with_backend(self):
        bus = FakeBus()
        player = _make_player(bus)
        backend = _StubBackend(bus)
        player.audio_service.services = [backend]
        player.audio_service.current = backend
        backend._playing = True  # so stop() reports a real stop -> ocp_stop()
        a = _track("http://example.com/a.mp3", "A")
        b = _track("http://example.com/b.mp3", "B")
        _load(player, [a, b])
        player.set_player_state(PlayerState.PLAYING)
        player.media_state = MediaState.BUFFERED_MEDIA
        # pretend playback started long enough ago to clear the stop guard
        player.audio_service.play_start_time = time.monotonic() - 5
        return bus, player, backend, a, b

    def test_api_stop_does_not_advance(self):
        bus, player, backend, a, b = self._player_with_backend()
        player.stop()
        time.sleep(0.1)
        self.assertNotEqual(player.now_playing.uri, b.uri,
                            "stop() advanced now_playing to the next track")
        self.assertEqual(player.state, PlayerState.STOPPED)
        # the END_OF_MEDIA emitted by ocp_stop() was consumed as a stop
        self.assertEqual(backend.stopped, 1)
        player.shutdown()

    def test_external_service_stop_does_not_advance(self):
        """'ovos.audio.service.stop' from outside must not advance either."""
        bus, player, backend, a, b = self._player_with_backend()
        bus.emit(Message("ovos.audio.service.stop"))
        time.sleep(0.1)
        self.assertNotEqual(player.now_playing.uri, b.uri,
                            "external stop advanced to the next track")
        player.shutdown()

    def test_play_clears_the_stop_flag(self):
        bus, player, backend, a, b = self._player_with_backend()
        player.stop()
        self.assertTrue(player._stop_requested)
        _load(player, [a, b])
        player.play()
        self.assertFalse(player._stop_requested)
        player.shutdown()


# ---------------------------------------------------------------------------
# W3-4: duplicate uris in a playlist
# ---------------------------------------------------------------------------

class TestDuplicateUriPlaylist(unittest.TestCase):

    def test_playlist_a_b_a_advances_then_stops(self):
        """[a, b, a] must play b, then a (index 2), then STOP.

        Pre-fix _queue_index() matched on uri and always returned 0 for the
        third entry, so the queue ping-ponged a -> b -> a -> b forever.
        """
        bus = FakeBus()
        player = _make_player(bus)
        a1 = _track("http://example.com/a.mp3", "A")
        b = _track("http://example.com/b.mp3", "B")
        a2 = _track("http://example.com/a.mp3", "A again")
        _load(player, [a1, b, a2])
        player.set_player_state(PlayerState.PLAYING)

        seen = []
        with patch.object(player, "play",
                          side_effect=lambda: seen.append(player.now_playing.uri)):
            for _ in range(3):
                player.media_state = MediaState.BUFFERED_MEDIA
                bus.emit(Message("ovos.common_play.media.state",
                                 {"state": MediaState.END_OF_MEDIA}))

        self.assertEqual(seen, [b.uri, a2.uri],
                         "expected a -> b -> a(index 2) -> end of queue")
        self.assertEqual(player.state, PlayerState.STOPPED)
        player.shutdown()


# ---------------------------------------------------------------------------
# W3-5: bounded retries on a permanently broken queue
# ---------------------------------------------------------------------------

class TestBoundedInvalidRetries(unittest.TestCase):

    def test_repeat_with_always_invalid_backend_stops(self):
        """1 track + LoopState.REPEAT + a backend that always fails must make a
        bounded number of attempts and end STOPPED — not spin.

        Pre-fix the INVALID_MEDIA bus handler called play_next() inline, which
        re-entered play() immediately: an unbounded hot loop (in-process it
        blew the recursion limit).
        """
        bus = FakeBus()
        player = _make_player(bus)
        player.invalid_stream_delay = 0.05
        player.loop_state = LoopState.REPEAT
        broken = _track("http://example.com/broken.mp3", "Broken")
        _load(player, [broken])

        calls = []
        real_play = player.play

        def counting_play():
            calls.append(time.monotonic())
            return real_play()

        player.play = counting_play
        # no backend supports this uri -> BaseMediaService.play emits INVALID_MEDIA
        player.audio_service.services = []

        player.play()
        time.sleep(2.0)

        self.assertLessEqual(len(calls), 4,
                             f"unbounded retry loop: play() ran {len(calls)} "
                             f"times in 2s")
        self.assertEqual(player.state, PlayerState.STOPPED)
        player.shutdown()

    def test_loaded_media_alone_does_not_clear_the_failed_uri_memory(self):
        """LOADED_MEDIA is reached by a track that loads fine but then
        fails to play (base.py's handle_media_state_change emits
        LOADED_MEDIA then INVALID_MEDIA for exactly that case) — clearing
        _failed_uris here degraded the rate limit to per-track chatter and
        let a REPEAT queue of load-ok/play-fail tracks loop unbounded,
        since _all_tracks_failed() never accumulated enough of
        _failed_uris to trip. The guard is cleared only on evidence of
        PLAYBACK (TrackState.PLAYING_* via NowPlaying, see the
        related tests in test_stop_and_retry_scoping.py /
        test_spoken_failure_dialogs.py)."""
        bus = FakeBus()
        player = _make_player(bus)
        player._failed_uris.add("http://example.com/a.mp3")
        player.media_state = MediaState.LOADING_MEDIA
        bus.emit(Message("ovos.common_play.media.state",
                         {"state": MediaState.LOADED_MEDIA}))
        self.assertEqual(player._failed_uris, {"http://example.com/a.mp3"})
        player.shutdown()

    def test_confirmed_playback_clears_the_failed_uri_memory(self):
        """Real evidence of playback — TrackState.PLAYING_AUDIO on
        'ovos.common_play.track.state', which base.py only emits after
        current.play() returns without raising — DOES clear the guard."""
        from ovos_utils.ocp import TrackState
        bus = FakeBus()
        player = _make_player(bus)
        player._failed_uris.add("http://example.com/a.mp3")
        bus.emit(Message("ovos.common_play.track.state",
                         {"state": TrackState.PLAYING_AUDIO}))
        self.assertEqual(player._failed_uris, set())
        player.shutdown()


class TestLoadOkPlayFailQueue(unittest.TestCase):
    """A queue where every track LOADS fine but FAILS TO PLAY (base.py emits
    LOADED_MEDIA then INVALID_MEDIA for exactly this case — see
    handle_media_state_change's try/except around current.play()). Since the
    guards reset only on confirmed PLAYBACK, none of these tracks ever reset
    them, so the rate limit and the bounded-repeat check behave correctly."""

    def _four_track_queue(self, bus):
        player = _make_player(bus)
        player.media.speak_dialog = MagicMock()
        player.invalid_stream_delay = 0.02
        tracks = [_track(f"http://example.com/{n}.mp3", n)
                  for n in ("a", "b", "c", "d")]
        _load(player, tracks)

        # patch play() itself to reproduce, for whichever track is current,
        # "loaded fine but failed to play": emit LOADED_MEDIA then
        # INVALID_MEDIA, exactly the sequence base.py's
        # handle_media_state_change produces when current.play() raises
        # after a successful load. Hooking play() (rather than manually
        # driving bus events from the test) means the player's own
        # on_invalid_stream -> _schedule_play_next -> play_next -> play()
        # retry chain drives every advance itself, through the real
        # deferred timer, with no test-side racing against it.
        calls = []

        def fake_play():
            calls.append(player.now_playing.uri if player.now_playing else None)
            # mirror real play(): mark PLAYING before the (here, simulated)
            # backend dispatch — otherwise player.state never leaves its
            # initial STOPPED and a test polling for "reached STOPPED"
            # would trivially pass without the retry chain ever running.
            player.set_player_state(PlayerState.PLAYING)
            with player._state_lock:
                player.media_state = MediaState.LOADING_MEDIA
            player.bus.emit(Message("ovos.common_play.media.state",
                                    {"state": MediaState.LOADED_MEDIA}))
            with player._state_lock:
                player.media_state = MediaState.BUFFERED_MEDIA
            player.bus.emit(Message("ovos.common_play.media.state",
                                    {"state": MediaState.INVALID_MEDIA}))

        player.play = fake_play
        return player, tracks, calls

    def test_track_failed_spoken_exactly_once_across_four_failing_tracks(self):
        bus = FakeBus()
        player, tracks, calls = self._four_track_queue(bus)
        player.play()  # kick off track a; on_invalid_stream's timer cascades
        deadline = time.monotonic() + 5
        while len(calls) < len(tracks) and time.monotonic() < deadline:
            time.sleep(0.02)

        speak_calls = [c for c in player.media.speak_dialog.call_args_list
                      if c.args and c.args[0] == "track.failed"]
        self.assertEqual(len(speak_calls), 1,
                        "track.failed must be spoken once per queue, not "
                        "once per failing track")
        player.shutdown()

    def test_repeat_over_all_failing_queue_is_bounded_and_ends_stopped(self):
        """LoopState.REPEAT over a queue where every track loads-ok/plays-fail
        must not spin: _all_tracks_failed() must eventually trip and stop
        the player, within a small bounded number of advances."""
        bus = FakeBus()
        player, tracks, calls = self._four_track_queue(bus)
        player.loop_state = LoopState.REPEAT

        player.play()  # kick off track a; the retry chain cascades itself
        deadline = time.monotonic() + 5
        while player.state != PlayerState.STOPPED and time.monotonic() < deadline:
            time.sleep(0.02)

        self.assertEqual(player.state, PlayerState.STOPPED,
                        f"REPEAT over an all-failing queue did not stop "
                        f"within 5s — unbounded loop ({len(calls)} play() "
                        f"attempts made)")
        # bounded: at most a couple of full passes over the 4-track queue,
        # not hundreds of attempts
        self.assertLessEqual(len(calls), len(tracks) * 3,
                            f"play() ran {len(calls)} times — not bounded")
        self.assertTrue(player._all_tracks_failed(player._merged_queue()))
        player.shutdown()


# ---------------------------------------------------------------------------
# W3-6 / W3-7 / W3-8: BaseMediaService deferred play & stop
# ---------------------------------------------------------------------------

def _make_service(bus=None, backends=None):
    from ovos_media.media_backends.base import BaseMediaService
    bus = bus or FakeBus()
    svc = BaseMediaService.__new__(BaseMediaService)
    # set the bookkeeping fields explicitly rather than via
    # _init_runtime_state(), so this fixture also builds against the
    # pre-fix source when proving these tests fail before the fix
    svc.on_stop = None
    svc._deferred_stop_timer = None
    svc.bus = bus
    svc.namespace = "audio"
    svc.config = {}
    svc.service_lock = threading.Lock()
    svc.current = None
    svc.play_start_time = 0
    svc.volume_is_low = False
    svc.services = backends if backends is not None else [_StubBackend(bus)]
    return svc, bus


class TestDeferredStop(unittest.TestCase):

    def test_internal_stop_within_guard_window_is_deferred_not_dropped(self):
        """stop() 0.3s after playback starts must still reach the backend.

        Pre-fix the <1s guard dropped the stop silently: the player reported
        STOPPED while the backend kept playing forever.
        """
        svc, bus = _make_service()
        backend = svc.services[0]
        svc.play("http://example.com/1.mp3")
        self.assertIs(svc.current, backend)
        time.sleep(0.3)

        svc.stop()  # message=None -> internal path
        self.assertEqual(backend.stopped, 0,
                         "stop should be deferred, not immediate")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and backend.stopped == 0:
            time.sleep(0.02)
        self.assertEqual(backend.stopped, 1,
                         "the deferred stop never reached the backend")
        time.sleep(0.1)  # let _perform_stop finish clearing `current`
        self.assertIsNone(svc.current)

    def test_deferred_stop_is_cancelled_by_a_new_play(self):
        svc, bus = _make_service()
        backend = svc.services[0]
        svc.play("http://example.com/1.mp3")
        svc.stop()
        svc.play("http://example.com/2.mp3")
        time.sleep(1.5)
        self.assertEqual(backend.stopped, 0,
                         "a superseded deferred stop killed the new playback")


if __name__ == "__main__":
    unittest.main()

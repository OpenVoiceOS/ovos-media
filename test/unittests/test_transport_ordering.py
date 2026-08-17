"""Transport-command ordering, driven through the real worker thread.

These cases used to be races: two END_OF_MEDIA events landing at once, a
stop landing while an END_OF_MEDIA was in flight, an invalid-stream retry
firing against a track it was not scheduled for. Each was held together by
a lock or by cancellation bookkeeping, and each test asserted that
mechanism. With one worker draining commands in arrival order the same
questions have deterministic answers, so what is asserted here is the
outcome: submit A then B, and the player ends where the order says it
should.
"""
import threading
import unittest

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import MediaEntry, MediaState, PlaybackType, PlayerState

from ovos_media.bus.api import OCPBusApi
from ovos_media.player import OCPMediaPlayer
from ovos_media.player.dispatcher import Dispatcher


def _track(uri, title):
    return MediaEntry(uri=uri, title=title, playback=PlaybackType.AUDIO)


class _StubBackend:
    """Backend that loads anything and reports nothing on its own."""

    def __init__(self, bus, name="stub"):
        self.bus = bus
        self.name = name
        self.aliases = [name]
        self.loaded = []
        self.stopped = 0
        self._playing = False

    def supported_uris(self):
        return ["http", "https", "file"]

    def set_track_start_callback(self, cb):
        pass

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
        # what an OPM backend emits from stop(): indistinguishable from a
        # track ending naturally, except for the order it arrives in
        self.bus.emit(Message("ovos.common_play.media.state",
                              {"state": MediaState.END_OF_MEDIA}))

    def shutdown(self):
        pass


class _OrderingTest(unittest.TestCase):
    """Base fixture: a player whose commands run on a real worker thread."""

    config = {}

    def setUp(self):
        self.bus = FakeBus()
        self.player = OCPMediaPlayer(self.bus, config=dict(self.config))
        self.player.now_playing.extract_stream = lambda: None
        # replace the inline test dispatcher with a real one, then rebuild
        # the bus edge so its listeners submit to it
        self.player.dispatcher.shutdown()
        self.player.dispatcher = Dispatcher(immediate=False)
        self.player.dispatcher.post_hook = self.player.publish_snapshot
        self.player.bus_api.shutdown()
        self.player.bus_api = OCPBusApi(self.bus, player=self.player)

        self.backend = _StubBackend(self.bus)
        self.player.audio_service.services = [self.backend]
        self.player.audio_service.current = self.backend
        self.player.audio_service.play_start_time = 0  # past the stop guard

        self.a = _track("http://example.com/a.mp3", "A")
        self.b = _track("http://example.com/b.mp3", "B")
        self.c = _track("http://example.com/c.mp3", "C")

    def tearDown(self):
        self.player.shutdown()

    def load(self, entries):
        self.player.playlist.clear()
        for e in entries:
            self.player.playlist.add_entry(e)
        self.drain(lambda: self.player.set_now_playing(self.player.playlist[0]))

    def drain(self, fn=None):
        """Run *fn* on the worker, then let the player settle.

        A command can queue more work by emitting on the bus, so waiting
        for what was queued when drain() was called is not enough: keep
        going until a full pass finds nothing left to run.
        """
        result = self.player.dispatcher.call(fn or (lambda: None), timeout=10)
        self.assertTrue(self.player.dispatcher.settle(timeout=10),
                        "the player never went idle")
        return result

    def end_of_media(self):
        self.bus.emit(Message("ovos.common_play.media.state",
                              {"state": MediaState.END_OF_MEDIA}))


class TestDoubleEndOfMedia(_OrderingTest):
    """Was: two END_OF_MEDIA events racing the compare-and-set, one
    reading media_state before the other wrote it, so a single end of a
    single track was acted on twice. The lock made the second one lose the
    compare; the queue does, because it runs after the first command
    finished and sees the state that command wrote."""

    config = {"autoplay": False}

    def test_a_duplicate_end_of_media_is_acted_on_once(self):
        self.load([self.a, self.b, self.c])
        self.drain(self.player.play)

        ends = []
        original = self.player.handle_playback_ended
        self.player.handle_playback_ended = \
            lambda *a, **kw: (ends.append(1), original(*a, **kw))[1]

        self.end_of_media()
        self.end_of_media()
        self.drain()

        self.assertEqual(len(ends), 1,
                         "one track ending was acted on twice")


class TestSequentialEndOfMedia(_OrderingTest):
    """Each genuine end of a track advances the queue by exactly one."""

    def test_a_third_advance_needs_a_state_change_first(self):
        self.load([self.a, self.b, self.c])
        self.drain(self.player.play)

        self.end_of_media()
        self.drain()
        self.assertEqual(self.player.now_playing.uri, self.b.uri)
        # play() of the new track resets media_state, so the NEXT genuine
        # end of media is a state change again and does advance
        self.end_of_media()
        self.drain()
        self.assertEqual(self.player.now_playing.uri, self.c.uri)


class TestStopVersusEndOfMedia(_OrderingTest):
    """Was: the on_stop callback, which existed only to make the stop flag
    win against the END_OF_MEDIA its own ocp_stop() emits. Now the stop
    command runs to completion first, so that END_OF_MEDIA is simply a
    later command."""

    def test_stop_does_not_advance_the_queue(self):
        self.load([self.a, self.b])
        self.drain(self.player.play)

        self.bus.emit(Message("ovos.common_play.stop"))
        self.drain()

        self.assertEqual(self.player.state, PlayerState.STOPPED)
        self.assertNotEqual(self.player.now_playing.uri, self.b.uri,
                            "an explicit stop advanced the queue")

    def test_an_end_of_media_queued_after_a_stop_is_ignored(self):
        self.load([self.a, self.b])
        self.drain(self.player.play)

        self.bus.emit(Message("ovos.common_play.stop"))
        self.end_of_media()
        self.drain()

        self.assertEqual(self.player.state, PlayerState.STOPPED)
        self.assertNotEqual(self.player.now_playing.uri, self.b.uri)

    def test_an_end_of_media_queued_before_a_play_still_advances(self):
        """The stop flag is per play attempt: a play command clears it, so
        a track ending after it advances normally."""
        self.load([self.a, self.b])
        self.drain(self.player.play)
        self.bus.emit(Message("ovos.common_play.stop"))
        self.drain()

        self.load([self.a, self.b])
        self.drain(self.player.play)
        self.end_of_media()
        self.drain()

        self.assertEqual(self.player.now_playing.uri, self.b.uri)


class TestSupersededInvalidRetry(_OrderingTest):
    """Was: cancelling _invalid_timer from four places and asserting the
    handle was None afterwards. The retry is now tagged with the epoch it
    was scheduled under and dropped when a newer command has bumped it."""

    def test_a_retry_from_an_earlier_track_never_skips_the_new_one(self):
        self.player.invalid_stream_delay = 0.05
        self.load([self.a, self.b])
        self.drain(self.player.play)
        self.drain(self.player.on_invalid_stream)  # arms the delayed retry

        # a new play request arrives inside the retry window
        self.load([self.c])
        self.drain(self.player.play)

        threading.Event().wait(0.3)
        self.drain()
        self.assertEqual(self.player.now_playing.uri, self.c.uri,
                         "the superseded retry skipped the new track")

    def test_a_retry_survives_when_nothing_supersedes_it(self):
        self.player.invalid_stream_delay = 0.05
        self.load([self.a, self.b])
        self.drain(self.player.play)
        self.drain(self.player.on_invalid_stream)

        threading.Event().wait(0.3)
        self.drain()
        self.assertEqual(self.player.now_playing.uri, self.b.uri,
                         "the invalid-stream retry never advanced the queue")

    def test_a_stop_supersedes_the_retry(self):
        self.player.invalid_stream_delay = 0.05
        self.load([self.a, self.b])
        self.drain(self.player.play)
        self.drain(self.player.on_invalid_stream)

        self.bus.emit(Message("ovos.common_play.stop"))
        self.drain()
        threading.Event().wait(0.3)
        self.drain()

        self.assertEqual(self.player.state, PlayerState.STOPPED,
                         "the superseded retry resumed playback after a stop")


class TestStopThenPlay(_OrderingTest):
    """A play arriving while a stop is still executing must win.

    The stop reaches the backend, whose stop path calls back into the
    player. Recording that callback as a new command put the stop flag
    back *after* the play had already cleared it, so the play's own
    END_OF_MEDIA was then read as a stop and wiped the new track. Nothing
    is drained between the two commands here: that gap is the defect.
    """

    def test_play_arriving_during_a_stop_wins(self):
        for attempt in range(20):
            self.load([self.a, self.b])
            self.drain(self.player.play)

            self.bus.emit(Message("ovos.common_play.stop"))
            self.bus.emit(Message("ovos.common_play.play",
                                  {"media": self.b.as_dict}))
            self.drain()

            self.assertEqual(self.player.state, PlayerState.PLAYING,
                             f"attempt {attempt}: the stop settled over the "
                             f"play that replaced it")
            self.assertEqual(self.player.now_playing.uri, self.b.uri,
                             f"attempt {attempt}: the requested track was "
                             f"wiped after the stop")
            self.assertFalse(self.player._stop_requested,
                             f"attempt {attempt}: the stop flag outlived the "
                             f"stop it belonged to")

    def test_a_skill_stopping_a_backend_directly_is_still_recorded(self):
        """The other caller of the same callback: a stop from a foreign
        thread, which the player has no other way to learn about."""
        self.load([self.a, self.b])
        self.drain(self.player.play)
        self.backend._playing = True

        self.player.audio_service.stop()  # as a skill would
        self.drain()

        self.assertTrue(self.player._stop_requested,
                        "a backend stopped from outside the player was read "
                        "as a track ending")
        self.assertNotEqual(self.player.now_playing.uri, self.b.uri,
                            "a skill-initiated stop advanced the queue")


class TestQueriesDoNotQueue(_OrderingTest):
    """Queries answer from the published snapshot, so a status request
    never waits behind a command (and never blocks the worker)."""

    def test_status_is_answered_while_a_command_is_in_flight(self):
        self.load([self.a, self.b])
        self.drain(self.player.play)

        replies = []
        self.bus.on("ovos.common_play.status.response",
                    lambda m: replies.append(m.data))

        block = threading.Event()
        self.player.dispatcher.submit(lambda: block.wait(5))
        self.bus.emit(Message("ovos.common_play.status"))

        self.assertEqual(len(replies), 1,
                         "the status query waited for the worker")
        self.assertEqual(replies[0]["player_state"], PlayerState.PLAYING)
        block.set()
        self.drain()

    def test_the_snapshot_follows_the_last_command(self):
        self.load([self.a, self.b])
        self.drain(self.player.play)
        self.assertEqual(self.player.snapshot.player_state, PlayerState.PLAYING)
        self.assertEqual(self.player.snapshot.title, self.a.title)

        self.bus.emit(Message("ovos.common_play.stop"))
        self.drain()
        self.assertEqual(self.player.snapshot.player_state, PlayerState.STOPPED)


if __name__ == "__main__":
    unittest.main()

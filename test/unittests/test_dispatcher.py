"""Tests for the player's execution model: one worker, FIFO order, epochs."""
import threading
import time
import unittest

from ovos_media.player.dispatcher import Dispatcher, PlayerSnapshot


def _real() -> Dispatcher:
    """A dispatcher with a real worker thread (see conftest.py)."""
    return Dispatcher(immediate=False)


class TestOrdering(unittest.TestCase):

    def test_commands_run_in_submission_order(self):
        d = _real()
        seen = []
        done = threading.Event()
        for i in range(50):
            d.submit(lambda i=i: seen.append(i))
        d.submit(done.set)
        self.assertTrue(done.wait(5))
        self.assertEqual(seen, list(range(50)))
        d.shutdown()

    def test_commands_never_overlap(self):
        """A second command cannot start while the first is still running."""
        d = _real()
        overlaps = []
        running = []
        done = threading.Event()

        def slow(tag):
            if running:
                overlaps.append(tag)
            running.append(tag)
            time.sleep(0.02)
            running.pop()

        for tag in range(5):
            d.submit(lambda tag=tag: slow(tag))
        d.submit(done.set)
        self.assertTrue(done.wait(5))
        self.assertEqual(overlaps, [])
        d.shutdown()

    def test_all_commands_share_one_thread(self):
        d = _real()
        threads = set()
        done = threading.Event()
        for _ in range(5):
            d.submit(lambda: threads.add(threading.current_thread().name))
        d.submit(done.set)
        self.assertTrue(done.wait(5))
        self.assertEqual(len(threads), 1)
        self.assertNotIn(threading.current_thread().name, threads)
        d.shutdown()

    def test_a_failing_command_does_not_stop_the_worker(self):
        d = _real()
        seen = []
        done = threading.Event()
        d.submit(lambda: 1 / 0)
        d.submit(lambda: seen.append("after"))
        d.submit(done.set)
        self.assertTrue(done.wait(5))
        self.assertEqual(seen, ["after"])
        d.shutdown()


class TestOnThread(unittest.TestCase):

    def test_on_thread_is_false_outside_and_true_inside(self):
        d = _real()
        inside = []
        done = threading.Event()
        self.assertFalse(d.on_thread())
        d.submit(lambda: inside.append(d.on_thread()))
        d.submit(done.set)
        self.assertTrue(done.wait(5))
        self.assertEqual(inside, [True])
        d.shutdown()

    def test_immediate_dispatcher_is_always_on_thread(self):
        d = Dispatcher(immediate=True)
        self.assertTrue(d.on_thread())


class TestCall(unittest.TestCase):

    def test_call_returns_the_result_from_the_worker(self):
        d = _real()
        thread_name = d.call(lambda: threading.current_thread().name)
        self.assertNotEqual(thread_name, threading.current_thread().name)
        self.assertEqual(d.call(lambda: 6 * 7), 42)
        d.shutdown()

    def test_call_reraises_in_the_caller(self):
        d = _real()
        with self.assertRaises(ValueError):
            d.call(lambda: (_ for _ in ()).throw(ValueError("boom")))
        d.shutdown()

    def test_call_from_the_worker_runs_inline(self):
        """A command may use call() without waiting for itself."""
        d = _real()
        box = []
        done = threading.Event()
        d.submit(lambda: box.append(d.call(lambda: "inline")))
        d.submit(done.set)
        self.assertTrue(done.wait(5))
        self.assertEqual(box, ["inline"])
        d.shutdown()

    def test_call_times_out_behind_a_stuck_command(self):
        d = _real()
        block = threading.Event()
        d.submit(block.wait)
        with self.assertRaises(TimeoutError):
            d.call(lambda: None, timeout=0.1)
        block.set()
        d.shutdown()


class TestEpochs(unittest.TestCase):

    def test_call_later_runs_when_its_epoch_still_stands(self):
        d = _real()
        ran = threading.Event()
        d.call_later(0.01, ran.set, d.epoch)
        self.assertTrue(ran.wait(5))
        d.shutdown()

    def test_call_later_is_dropped_after_a_bump(self):
        d = _real()
        ran = []
        d.call_later(0.05, lambda: ran.append(1), d.epoch)
        d.bump_epoch()
        time.sleep(0.2)
        self.assertEqual(ran, [])
        d.shutdown()

    def test_a_bump_drops_only_earlier_work(self):
        d = _real()
        ran = []
        d.call_later(0.05, lambda: ran.append("old"), d.epoch)
        d.bump_epoch()
        later = threading.Event()
        d.call_later(0.05, lambda: (ran.append("new"), later.set()), d.epoch)
        self.assertTrue(later.wait(5))
        self.assertEqual(ran, ["new"])
        d.shutdown()

    def test_bump_returns_a_new_token_every_time(self):
        d = _real()
        tokens = [d.epoch] + [d.bump_epoch() for _ in range(3)]
        self.assertEqual(len(set(tokens)), 4)
        d.shutdown()

    def test_pending_counts_armed_delayed_work(self):
        d = _real()
        self.assertEqual(d.pending, 0)
        d.call_later(0.05, lambda: None, d.epoch)
        self.assertEqual(d.pending, 1)
        time.sleep(0.2)
        self.assertEqual(d.pending, 0)
        d.shutdown()


class TestDelayedWorkUnderLoad(unittest.TestCase):
    """Delayed work is armed from whichever thread schedules it and
    disarmed from the timer threads themselves, so the bookkeeping is
    touched concurrently by design. It must never raise there: an
    exception on that path used to happen before the command was queued,
    so the command was lost."""

    def test_scheduling_while_timers_fire_loses_nothing(self):
        d = _real()
        ran = []
        errors = []

        def hammer():
            for _ in range(300):
                try:
                    d.call_later(0.001, lambda: ran.append(1), d.epoch)
                except Exception as e:  # pragma: no cover
                    errors.append(e)

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and len(ran) < 1200:
            time.sleep(0.02)
        d.call(lambda: None, timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(len(ran), 1200,
                         "delayed commands were dropped under concurrent "
                         "scheduling")
        self.assertEqual(d.pending, 0)
        d.shutdown()


class TestShutdown(unittest.TestCase):

    def test_shutdown_abandons_queued_commands(self):
        d = _real()
        block = threading.Event()
        ran = []
        started = threading.Event()
        d.submit(lambda: (started.set(), block.wait(2)))
        d.submit(lambda: ran.append("queued"))
        self.assertTrue(started.wait(5))
        block.set()
        d.shutdown()
        self.assertEqual(ran, [])

    def test_submitting_after_shutdown_is_a_no_op(self):
        d = _real()
        d.shutdown()
        ran = []
        d.submit(lambda: ran.append(1))
        time.sleep(0.1)
        self.assertEqual(ran, [])

    def test_shutdown_drops_delayed_work(self):
        d = _real()
        ran = []
        d.call_later(0.05, lambda: ran.append(1), d.epoch)
        d.shutdown()
        time.sleep(0.2)
        self.assertEqual(ran, [])

    def test_shutdown_is_idempotent(self):
        d = _real()
        d.submit(lambda: None)
        d.shutdown()
        d.shutdown()

    def test_post_hook_runs_after_every_command(self):
        d = _real()
        hooks = []
        d.post_hook = lambda: hooks.append(len(hooks))
        done = threading.Event()
        d.submit(lambda: None)
        d.submit(lambda: 1 / 0)  # a failure still publishes the new state
        d.submit(done.set)
        self.assertTrue(done.wait(5))
        self.assertEqual(len(hooks), 3)
        d.shutdown()


class TestImmediateMode(unittest.TestCase):

    def test_submit_runs_inline_and_in_order(self):
        d = Dispatcher(immediate=True)
        seen = []
        d.submit(lambda: seen.append(1))
        self.assertEqual(seen, [1])
        d.submit(lambda: seen.append(2))
        self.assertEqual(seen, [1, 2])

    def test_call_runs_inline(self):
        d = Dispatcher(immediate=True)
        self.assertEqual(d.call(lambda: "x"),  "x")


class TestSnapshot(unittest.TestCase):

    def test_status_dict_carries_the_status_payload(self):
        snap = PlayerSnapshot(player_state="PLAYING", shuffle=True,
                              title="T", artist="A", playlist_size=3)
        data = snap.as_status_dict
        self.assertEqual(data["player_state"], "PLAYING")
        self.assertTrue(data["shuffle"])
        self.assertEqual(data["title"], "T")
        self.assertEqual(data["playlist_size"], 3)
        self.assertNotIn("track_info", data)

    def test_snapshot_is_immutable(self):
        snap = PlayerSnapshot()
        with self.assertRaises(Exception):
            snap.shuffle = True


if __name__ == "__main__":
    unittest.main()

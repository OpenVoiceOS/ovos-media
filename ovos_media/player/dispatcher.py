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
"""Single-threaded execution model for the player.

Every command that mutates player state runs on one worker thread, in the
order it was submitted. The bus edge, the MPRIS thread and the delayed
invalid-stream retry all hand their work to this queue instead of touching
the player from their own threads, so the state machine has exactly one
writer and needs no locks: two END_OF_MEDIA events are two queued commands,
the second one seeing what the first one did.

Reads are not serialized. Queries answer from :class:`PlayerSnapshot`, an
immutable view published after every command; the live track position is
the one sanctioned off-thread read, because it must reach the plugin.
"""

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Tuple

from ovos_utils.log import LOG


class Dispatcher:
    """FIFO command queue drained by a single worker thread.

    The worker starts on the first submitted command, so a player that is
    only ever driven by direct calls never spawns a thread.

    ``immediate=True`` runs every command inline in the calling thread. It
    exists for tests that drive the player directly and assert its state on
    the next line; the ordering guarantee is then the calling thread's own.
    """

    def __init__(self, name: str = "ocp-dispatcher", immediate: bool = False):
        self.name = name
        self.immediate = immediate
        self.post_hook: Optional[Callable[[], None]] = None
        self._queue: "queue.Queue[Optional[Callable[[], None]]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._epoch: int = 0
        self._timers: set = set()
        # guards _timers only: timers are added from whichever thread
        # schedules delayed work and removed from the timer threads
        # themselves, so the set is touched concurrently by design
        self._timers_lock = threading.Lock()
        self._lock = threading.Lock()
        self._local = threading.local()
        self._stopped = False

    @classmethod
    def immediate_dispatcher(cls, name: str = "ocp-dispatcher-immediate") -> "Dispatcher":
        """A dispatcher that runs everything inline (see class docstring)."""
        return cls(name=name, immediate=True)

    # --- worker -----------------------------------------------------------
    def _ensure_worker(self) -> None:
        with self._lock:
            if self._thread is not None or self._stopped:
                return
            self._thread = threading.Thread(target=self._run, name=self.name,
                                            daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            fn = self._queue.get()
            if fn is None:  # shutdown sentinel
                return
            self._execute(fn)

    def _execute(self, fn: Callable[[], None]) -> None:
        depth = getattr(self._local, "depth", 0)
        self._local.depth = depth + 1
        try:
            fn()
        except Exception as e:
            LOG.exception(f"dispatched command failed: {e}")
        finally:
            self._local.depth = depth
        if self.post_hook is not None:
            try:
                self.post_hook()
            except Exception as e:
                LOG.exception(f"dispatcher post hook failed: {e}")

    def on_thread(self) -> bool:
        """True when the caller reads and writes as the dispatcher does.

        In immediate mode every caller does, since commands run inline.
        """
        if self.immediate:
            return True
        return threading.current_thread() is self._thread

    def in_command(self) -> bool:
        """True only while a submitted command is running.

        Unlike :meth:`on_thread` this is False for a caller that reaches
        the player some other way, which is what tells a callback from a
        backend whether the stop it reports is one of our own commands.
        """
        return getattr(self._local, "depth", 0) > 0

    # --- submission -------------------------------------------------------
    def submit(self, fn: Callable[[], None]) -> None:
        """Queue *fn* to run on the worker. Fire and forget."""
        if self._stopped:
            LOG.debug("dispatcher is shut down, dropping command")
            return
        if self.immediate:
            self._execute(fn)
            return
        self._ensure_worker()
        self._queue.put(fn)

    def call(self, fn: Callable[[], Any], timeout: float = 5.0) -> Any:
        """Run *fn* on the worker and return its result.

        For the rare read that cannot be answered from a snapshot. Called
        from the worker itself it runs inline, so a command may use it
        without deadlocking.
        """
        if self.immediate or self.on_thread():
            return fn()
        done = threading.Event()
        box: list = [None, None]

        def wrapper():
            try:
                box[0] = fn()
            except Exception as e:  # re-raised in the caller
                box[1] = e
            finally:
                done.set()

        self.submit(wrapper)
        if not done.wait(timeout):
            raise TimeoutError(f"dispatched call did not complete in {timeout}s")
        if box[1] is not None:
            raise box[1]
        return box[0]

    def settle(self, timeout: float = 10.0) -> bool:
        """Wait until the queue has been empty across a full idle pass.

        A command can queue more work — most of the player's commands emit
        on the bus, and the edge submits what comes back — so draining
        what was queued when this was called is not enough. Returns False
        if the player was still busy when *timeout* ran out.
        """
        if self.immediate:
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._queue.empty():
                try:
                    self.call(lambda: None,
                              timeout=max(deadline - time.monotonic(), 0.01))
                except TimeoutError:
                    return False
                if self._queue.empty():
                    return True
            threading.Event().wait(0.005)
        return False

    # --- epochs and delayed work -----------------------------------------
    @property
    def epoch(self) -> int:
        """Token identifying the current generation of delayed work."""
        return self._epoch

    def bump_epoch(self) -> int:
        """Supersede every delayed command scheduled so far.

        Nothing is cancelled: a superseded command is dropped when its
        timer fires, which is what makes "a new play request wins" a plain
        comparison instead of cancellation bookkeeping.
        """
        self._epoch += 1
        return self._epoch

    def call_later(self, delay: float, fn: Callable[[], None],
                   epoch: Optional[int] = None) -> threading.Timer:
        """Submit *fn* after *delay* seconds, unless its epoch is stale.

        The epoch is compared at execution time, not when the timer fires,
        so a command already queued behind an epoch bump is still dropped.
        """
        if epoch is None:
            epoch = self._epoch
        handle: list = []
        timer = threading.Timer(max(delay, 0.0),
                                lambda: self._fire(fn, epoch, handle[0]))
        handle.append(timer)
        timer.daemon = True
        with self._timers_lock:
            self._timers.add(timer)
        timer.start()
        return timer

    def _fire(self, fn: Callable[[], None], epoch: int,
              timer: threading.Timer) -> None:
        # forget this timer only — never rebuild the set, which another
        # thread may be adding to at the same moment. The bookkeeping is
        # also kept off the path to submit(): a delayed command must be
        # queued (and then dropped on its epoch, or run) whatever happens
        # to the housekeeping.
        try:
            with self._timers_lock:
                self._timers.discard(timer)
        except Exception as e:
            LOG.exception(f"delayed command bookkeeping failed: {e}")
        if self._stopped:
            return

        def guarded():
            if epoch != self._epoch:
                LOG.debug("dropping superseded delayed command")
                return
            fn()

        self.submit(guarded)

    @property
    def pending(self) -> int:
        """How many delayed commands are still armed for the current epoch."""
        with self._timers_lock:
            return len([t for t in self._timers if t.is_alive()])

    # --- teardown ---------------------------------------------------------
    def shutdown(self) -> None:
        """Stop the worker, abandoning whatever is still queued.

        Abandon rather than drain: shutdown is reached from stop paths, and
        running the commands that a stop just made irrelevant is how a
        shutting-down player used to resume playing. Delayed work is
        superseded by the same epoch bump every stop does.
        """
        if self._stopped:
            return
        self._stopped = True
        self.bump_epoch()
        with self._timers_lock:
            timers = list(self._timers)
            self._timers.clear()
        for timer in timers:
            timer.cancel()
        thread = self._thread
        if thread is None:
            return
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        self._queue.put(None)
        if thread is not threading.current_thread():
            thread.join(timeout=2)
        self._thread = None


@dataclass(frozen=True)
class PlayerSnapshot:
    """Immutable view of the player, published after every command.

    Query handlers answer from this instead of reading the live state from
    another thread. It carries exactly what the status/track_info bus
    responses have always carried.
    """
    player_state: Any = None
    media_state: Any = None
    loop_state: Any = None
    shuffle: bool = False
    playback_type: Any = None
    media_type: Any = None
    playlist_position: int = 0
    playlist_size: int = 0
    title: str = ""
    artist: str = ""
    image: str = ""
    track_info: dict = field(default_factory=dict)
    queue: Tuple[dict, ...] = ()
    candidates: Tuple[dict, ...] = ()

    @property
    def as_status_dict(self) -> dict:
        """The 'ovos.common_play.status' response payload."""
        return {
            "playback_type": self.playback_type,
            "media_type": self.media_type,
            "player_state": self.player_state,
            "loop_state": self.loop_state,
            "media_state": self.media_state,
            "shuffle": self.shuffle,
            "playlist_position": self.playlist_position,
            "playlist_size": self.playlist_size,
            "title": self.title,
            "artist": self.artist,
            "image": self.image,
        }

    @property
    def as_disambiguation_dict(self) -> dict:
        """The 'ovos.common_play.disambiguation' response payload: the
        candidate set (OCP-1 §4.4.2) the last playback request was chosen
        from, in descending match order."""
        return {"entries": list(self.candidates)}

    @classmethod
    def of(cls, player) -> "PlayerSnapshot":
        """Take a snapshot of *player*'s current state."""
        now_playing = player.now_playing
        playlist = player.playlist
        return cls(
            player_state=player.state,
            media_state=player.media_state,
            loop_state=player.loop_state,
            shuffle=player.shuffle,
            playback_type=player.playback_type,
            media_type=now_playing.media_type,
            playlist_position=playlist.position,
            playlist_size=len(playlist),
            title=now_playing.title,
            artist=now_playing.artist,
            image=now_playing.image,
            track_info=dict(now_playing.as_dict),
            queue=tuple(e.as_dict for e in playlist.entries),
            candidates=tuple(e.as_dict for e in player.media.search_playlist.entries),
        )

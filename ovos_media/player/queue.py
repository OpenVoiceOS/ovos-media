"""The playback queue owned by :class:`~ovos_media.player.OCPMediaPlayer`.

:class:`PlayQueue` holds the user queue (the entries the player was asked to
play), the identity of the entry currently selected, and the uris that failed
to load since the last successful one. It also answers every "which track
comes next" question — merging in search results, locating the current entry,
picking a shuffle candidate — but it never decides what to *do* with the
answer: repeat, autoplay and stop remain player policy.
"""
import random
from typing import List, Optional, Union

from ovos_utils.log import LOG
from ovos_utils.ocp import MediaEntry, Playlist, PluginStream, dict2entry


class QueueEnd:
    """Returned by a selection when the queue has no further track."""


class AllFailed:
    """Returned by a selection when every track in the queue failed to load.

    Distinct from :class:`QueueEnd` because a repeat cycle must break on it
    instead of restarting an entirely broken queue.
    """


class KeepCurrent:
    """Returned by a shuffle pick when the current track should keep playing.

    There is nothing meaningful to shuffle to (empty/singleton queue, or
    repeat is on and only the current, unfailed track remains).
    """


QUEUE_END = QueueEnd()
ALL_FAILED = AllFailed()
KEEP_CURRENT = KeepCurrent()

Selection = Union[MediaEntry, QueueEnd, AllFailed, KeepCurrent]


class PlayQueue:
    """The player's own queue of :class:`MediaEntry` objects."""

    def __init__(self, entries: List[MediaEntry] = None, title: str = ""):
        self.title = title
        self.position = 0
        self._entries: List[MediaEntry] = []
        # The exact MediaEntry object currently selected. Located by identity
        # in index(), which makes duplicate uris in a queue (eg. [a, b, a])
        # advance correctly instead of ping-ponging, and survives the
        # END_OF_MEDIA reset that clears now_playing.uri.
        self.current: Optional[MediaEntry] = None
        # Uris that failed to load since the last successful load. Used to
        # stop LoopState.REPEAT from restarting a queue in which every track
        # is broken (unbounded hot loop).
        self.failed: set = set()
        for entry in entries or []:
            self.add_entry(entry)

    # container
    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(self._entries)

    def __getitem__(self, idx):
        return self._entries[idx]

    def __contains__(self, entry) -> bool:
        return entry in self._entries

    def __repr__(self) -> str:
        return f"PlayQueue({self._entries!r})"

    @property
    def entries(self) -> List[Union[MediaEntry, PluginStream]]:
        """The playable members of the queue.

        A track that is itself a playlist is a legitimate queue member (that
        is how a playlist result arrives over the wire, see bus.schemas) and
        stays in the backing list, where sanitization reaches it. It has no
        uri of its own, though, so every uri-based consumer — merging,
        locating the current track, advancing — reads this view instead.
        """
        return [e for e in self._entries
                if isinstance(e, (MediaEntry, PluginStream))]

    @property
    def length(self) -> int:
        """Total duration of the queue; -1 stands for a live stream."""
        return max(-1, sum(e.length for e in self.entries))

    @property
    def is_first_track(self) -> bool:
        if not self._entries:
            return True
        return self.position == 0

    @property
    def is_last_track(self) -> bool:
        if not self._entries:
            return True
        return self.position == len(self._entries) - 1

    def set_position(self, idx: int) -> None:
        self.position = idx
        self._validate_position()

    def _validate_position(self) -> None:
        if self.position < 0 or self.position >= len(self._entries):
            LOG.error(f"Queue pointer is in an invalid position "
                      f"({self.position})! Going to start of queue")
            self.position = 0

    def add_entry(self, entry: Union[dict, MediaEntry, PluginStream],
                  index: int = -1) -> None:
        """Add an entry at *index* (-1 appends)."""
        assert isinstance(index, int)
        if index > len(self._entries):
            raise ValueError(f"Invalid index {index} requested, "
                             f"queue only has {len(self._entries)} entries")
        if isinstance(entry, dict):
            entry = dict2entry(entry)
        # a nested Playlist is a legitimate entry: that is how a playlist
        # result arrives over the wire (see bus.schemas)
        assert isinstance(entry, (MediaEntry, PluginStream, Playlist))
        if index == -1:
            index = len(self._entries)
        had_current_track = len(self._entries) > 0
        self._entries.insert(index, entry)
        if had_current_track and index <= self.position:
            self.set_position(self.position + 1)

    def replace(self, new_list: List[Union[dict, MediaEntry]]) -> None:
        self.clear()
        for entry in new_list:
            self.add_entry(entry)

    def clear(self) -> None:
        self._entries.clear()
        self.position = 0

    def goto_track(self, track: Union[dict, MediaEntry, PluginStream]) -> None:
        """Move the position pointer to *track*, matched by uri."""
        if isinstance(track, dict):
            track = dict2entry(track)
        assert isinstance(track, (MediaEntry, Playlist, PluginStream))
        requested_uri = self._uri_of(track)
        for idx, entry in enumerate(self._entries):
            if self._uri_of(entry) == requested_uri:
                self.set_position(idx)
                LOG.debug(f"New queue position: {self.position}")
                return
        LOG.error(f"requested track not in the queue: {track}")

    @staticmethod
    def _uri_of(track) -> str:
        """What a track is matched on: its uri, the stream of a plugin
        entry, or the title of a nested playlist, which has neither."""
        if isinstance(track, MediaEntry):
            return track.uri
        if isinstance(track, PluginStream):
            return track.stream
        return track.title

    # failed-uri bookkeeping
    def mark_failed(self, uri: str) -> None:
        if uri:
            self.failed.add(uri)

    def clear_failed(self) -> None:
        self.failed.clear()

    def all_failed(self, queue: List[MediaEntry]) -> bool:
        """True if every track in *queue* has failed since the last
        successful load."""
        return bool(queue) and all(e.uri in self.failed for e in queue)

    # queue algebra
    def merged(self, search_entries: List[MediaEntry],
               merge_search: bool = True,
               user_entries: List[MediaEntry] = None) -> List[MediaEntry]:
        """Return the merged, deduplicated playback queue.

        User entries come first (strict priority). Search results are appended
        afterwards, skipping any uri already present among the user entries.
        Deduplication is O(n) via a uri set. With *merge_search* disabled only
        the user entries are returned.
        """
        user_entries = list(self.entries if user_entries is None else user_entries)
        if not merge_search:
            return user_entries
        seen = {e.uri for e in user_entries}
        return user_entries + [e for e in search_entries if e.uri not in seen]

    def index(self, queue: List[MediaEntry], uri: str = None,
              position: int = None) -> int:
        """Return the index of the currently selected track in *queue*, or -1.

        Resolution order:

        1. **Entry identity** — the exact MediaEntry object last selected.
           This is the only reliable locator when the same uri appears more
           than once in a queue (``[a, b, a]``), and it survives the
           END_OF_MEDIA reset that clears the now-playing uri.
        2. **Queue position** — *position*, accepted only if it points at an
           entry whose uri matches the one we are looking for.
        3. **URI lookup** — first entry with a matching uri.
        """
        if self.current is not None:
            for i, entry in enumerate(queue):
                if entry is self.current:
                    return i

        uri = uri or (self.current.uri if self.current is not None else None)
        if not uri:
            return -1

        if isinstance(position, int) and 0 <= position < len(queue) and \
                queue[position].uri == uri:
            return position

        for i, entry in enumerate(queue):
            if entry.uri == uri:
                return i
        return -1

    def has_prev(self, queue: List[MediaEntry], uri: str = None,
                 position: int = None) -> bool:
        """True if there is a track before the current one in *queue*."""
        return self.index(queue, uri=uri, position=position) > 0

    def has_next(self, queue: List[MediaEntry], uri: str = None,
                 position: int = None) -> bool:
        """True if there is a track after the current one in *queue*."""
        idx = self.index(queue, uri=uri, position=position)
        return idx >= 0 and idx + 1 < len(queue)

    def select_next(self, queue: List[MediaEntry], uri: str = None,
                    position: int = None, repeat: bool = False) -> Selection:
        """Select the track that follows the current one.

        Returns the MediaEntry to play, ``ALL_FAILED`` when *repeat* would
        restart a queue whose every track is broken, or ``QUEUE_END``.
        """
        idx = self.index(queue, uri=uri, position=position)
        if idx >= 0 and idx + 1 < len(queue):
            next_track = queue[idx + 1]
            LOG.info(f"Next track: {next_track.title!r} "
                     f"(queue index {idx + 1}/{len(queue) - 1})")
            return next_track
        if repeat and queue:
            if self.all_failed(queue):
                return ALL_FAILED
            LOG.info("End of queue, repeat == True — restarting from beginning")
            return queue[0]
        return QUEUE_END

    def select_prev(self, queue: List[MediaEntry], uri: str = None,
                    position: int = None) -> Selection:
        """Select the track before the current one, or ``QUEUE_END``."""
        idx = self.index(queue, uri=uri, position=position)
        if idx > 0:
            prev_track = queue[idx - 1]
            LOG.debug(f"Previous track: {prev_track.title!r} "
                      f"(queue index {idx - 1}/{len(queue) - 1})")
            return prev_track
        return QUEUE_END

    def select_shuffle(self, queue: List[MediaEntry], current_uri: str = None,
                       repeat: bool = False) -> Selection:
        """Pick a random track from *queue*, excluding the current one and
        every uri already known to be broken.

        Returns the MediaEntry to play, ``KEEP_CURRENT`` when there is nothing
        meaningful to shuffle to and the current track should keep playing, or
        ``QUEUE_END`` when no viable track remains.
        """
        if not queue:
            if current_uri is not None and current_uri in self.failed:
                LOG.debug("Shuffle: queue is empty and current track failed")
                return QUEUE_END
            LOG.debug("Shuffle: queue is empty, replaying current track")
            return KEEP_CURRENT

        candidates = [e for e in queue
                      if e.uri != current_uri and e.uri not in self.failed]
        if not candidates:
            if repeat and current_uri and current_uri not in self.failed:
                # nothing else to shuffle to, but repeat is on and the current
                # track itself hasn't failed - keep it playing instead of
                # stopping (mirrors the sequential end-of-queue restart)
                LOG.debug("Shuffle: no other viable tracks, repeat is on "
                          "— keeping current track")
                return KEEP_CURRENT
            return QUEUE_END

        pick = random.choice(candidates)
        LOG.debug(f"Shuffle pick: {pick.title!r}")
        return pick

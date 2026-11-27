"""The playback queue owned by :class:`~ovos_media.player.OCPMediaPlayer`.

:class:`PlayQueue` holds the user queue (the entries the player was asked to
play) as a :class:`~ovos_media.player.tree.PlayTree`, the identity of the
entry currently selected, and the uris that failed to load since the last
successful one. It also answers every "which track comes next" question —
merging in search results, locating the current entry, picking a shuffle
candidate — but it never decides what to *do* with the answer: repeat-track,
autoplay and stop remain player policy.

Tree inside, flat outside: ``len``, iteration, indexing, :attr:`position` and
:attr:`entries` all read the leaves in traversal order, so a queue with no
nested playlist behaves exactly like a flat list. A nested playlist becomes
an internal node and its tracks play in its own order. Shuffle and repeat on
the root node are the player's global flags; they act on the flat merged
queue and never reorder the flat view. Shuffle and repeat on a nested node
act inside that node.
"""
import random
from typing import List, Optional, Union

from ovos_utils.log import LOG
from ovos_utils.ocp import MediaEntry, Playlist, PluginStream, dict2entry

from ovos_media.player.tree import (ALL_FAILED, DEFAULT_MAX_DEPTH, AllFailed,
                                    PlayNode, PlayTree, Repeat, TreeEnd)


class QueueEnd:
    """Returned by a selection when the queue has no further track."""


class KeepCurrent:
    """Returned by a shuffle pick when the current track should keep playing.

    There is nothing meaningful to shuffle to (empty/singleton queue, or
    repeat is on and only the current, unfailed track remains).
    """


QUEUE_END = QueueEnd()
KEEP_CURRENT = KeepCurrent()

Selection = Union[MediaEntry, QueueEnd, AllFailed, KeepCurrent]


def _node_of(entry, depth: int = 0, path: frozenset = frozenset()) -> Optional[PlayNode]:
    """Build a node from a queue member.

    A non-empty Playlist becomes an internal node with fresh child nodes, so
    the queue never shares structure with the caller's Playlist. Any other
    member, an empty Playlist included, becomes a leaf. Returns None for a
    playlist nested past ``DEFAULT_MAX_DEPTH`` or one that contains itself.
    """
    if isinstance(entry, dict):
        entry = dict2entry(entry)
    if not isinstance(entry, Playlist) or not len(entry):
        return PlayNode(entry=entry)
    if id(entry) in path:
        LOG.warning(f"Dropping playlist that contains itself: {entry.title!r}")
        return None
    if depth >= DEFAULT_MAX_DEPTH:
        LOG.warning(f"Dropping playlist nested past max_depth="
                    f"{DEFAULT_MAX_DEPTH}: {entry.title!r}")
        return None
    node = PlayNode(title=entry.title)
    for member in list.__iter__(entry):
        child = _node_of(member, depth + 1, path | {id(entry)})
        if child is not None:
            node.add_child(child)
    return node


def _walk(node: PlayNode, out: List[PlayNode]) -> None:
    if node.is_leaf:
        out.append(node)
        return
    for idx in node.child_order():
        _walk(node.children[idx], out)


class PlayQueue:
    """The player's own queue of :class:`MediaEntry` objects."""

    def __init__(self, entries: List[MediaEntry] = None, title: str = ""):
        self.title = title
        # index into the flat leaf view
        self.position = 0
        self.tree = PlayTree()
        # repeat-one replays the current leaf; it is not a node policy
        self.repeat_track = False
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

    @property
    def root(self) -> PlayNode:
        return self.tree.root

    def leaves(self) -> List[PlayNode]:
        """The leaves in traversal order. The root keeps its structural
        order: root shuffle picks per step and never reorders this view."""
        leaves: List[PlayNode] = []
        for child in self.root.children:
            _walk(child, leaves)
        return leaves

    def _members(self) -> list:
        return [leaf.entry for leaf in self.leaves()]

    # container
    def __len__(self) -> int:
        return len(self.leaves())

    def __iter__(self):
        return iter(self._members())

    def __getitem__(self, idx):
        return self._members()[idx]

    def __contains__(self, entry) -> bool:
        return entry in self._members()

    def __repr__(self) -> str:
        return f"PlayQueue({self._members()!r})"

    @property
    def entries(self) -> List[Union[MediaEntry, PluginStream]]:
        """The playable members of the queue.

        An empty playlist is a leaf with no uri of its own, so every
        uri-based consumer — merging, locating the current track,
        advancing — reads this view instead of the leaves.
        """
        return [e for e in self._members()
                if isinstance(e, (MediaEntry, PluginStream))]

    @property
    def length(self) -> int:
        """Total duration of the queue; -1 stands for a live stream."""
        return max(-1, sum(e.length for e in self.entries))

    @property
    def is_first_track(self) -> bool:
        if not len(self):
            return True
        return self.position == 0

    @property
    def is_last_track(self) -> bool:
        size = len(self)
        if not size:
            return True
        return self.position == size - 1

    def set_position(self, idx: int) -> None:
        self.position = idx
        self._validate_position()

    def _validate_position(self) -> None:
        if self.position < 0 or self.position >= len(self):
            LOG.error(f"Queue pointer is in an invalid position "
                      f"({self.position})! Going to start of queue")
            self.position = 0

    def add_entry(self, entry: Union[dict, MediaEntry, PluginStream],
                  index: int = -1) -> None:
        """Add an entry before the leaf at flat *index* (-1 appends).

        A Playlist is copied into new nodes (graft by copy): a later change
        to the caller's Playlist never reaches the queue.
        """
        assert isinstance(index, int)
        leaves = self.leaves()
        if index > len(leaves):
            raise ValueError(f"Invalid index {index} requested, "
                             f"queue only has {len(leaves)} entries")
        if isinstance(entry, dict):
            entry = dict2entry(entry)
        # a nested Playlist is a legitimate entry: that is how a playlist
        # result arrives over the wire (see bus.schemas)
        assert isinstance(entry, (MediaEntry, PluginStream, Playlist))
        node = _node_of(entry)
        if node is None:
            return
        if index == -1 or index == len(leaves):
            index = len(leaves)
            self.root.add_child(node)
        else:
            anchor = leaves[index]
            anchor.parent.add_child(node, anchor.parent.children.index(anchor))
        if leaves and index <= self.position:
            self.set_position(self.position + len(node.leaves()))

    def replace(self, new_list: List[Union[dict, MediaEntry]]) -> None:
        self.clear()
        for entry in new_list:
            self.add_entry(entry)

    def clear(self) -> None:
        self.root.children.clear()
        self.root.reshuffle()
        self.position = 0

    def goto_track(self, track: Union[dict, MediaEntry, PluginStream]) -> None:
        """Move the position pointer to *track*, matched by uri."""
        if isinstance(track, dict):
            track = dict2entry(track)
        assert isinstance(track, (MediaEntry, Playlist, PluginStream))
        requested_uri = self._uri_of(track)
        for idx, entry in enumerate(self._members()):
            if self._uri_of(entry) == requested_uri:
                self.set_position(idx)
                LOG.debug(f"New queue position: {self.position}")
                return
        LOG.error(f"requested track not in the queue: {track}")

    @staticmethod
    def _uri_of(track) -> str:
        """What a track is matched on: its uri, the stream of a plugin
        entry, or the title of an empty playlist, which has neither."""
        if isinstance(track, MediaEntry):
            return track.uri
        if isinstance(track, PluginStream):
            return track.stream
        return track.title

    def _leaf_of(self, entry) -> Optional[PlayNode]:
        for leaf in self.leaves():
            if leaf.entry is entry:
                return leaf
        return None

    def _top_node(self, leaf: PlayNode) -> PlayNode:
        """The root child that holds *leaf*."""
        while leaf.parent is not self.root:
            leaf = leaf.parent
        return leaf

    def _nested_step(self, queue: list, idx: int, forward: bool):
        """Step from ``queue[idx]`` inside its nested node.

        Returns the entry to select, or the flat index to continue from when
        the nested node is exhausted: the edge of that node's run in *queue*.
        A track that is not inside a nested node returns *idx* unchanged.
        """
        leaf = self._leaf_of(queue[idx])
        if leaf is None or leaf.parent is self.root:
            return idx
        playable = {id(e) for e in queue}
        node = leaf
        for _ in range(len(queue) + 1):
            node = self.tree.sibling_leaf(node, forward, stop=self.root)
            if isinstance(node, TreeEnd):
                break
            if id(node.entry) in playable:
                return node.entry
        members = {id(l.entry) for l in self._top_node(leaf).leaves()}
        run = [i for i, e in enumerate(queue) if id(e) in members]
        return run[-1] if forward else run[0]

    # failed-uri bookkeeping
    def mark_failed(self, uri: str) -> None:
        if uri:
            self.failed.add(uri)
            for leaf in self.leaves():
                if self._uri_of(leaf.entry) == uri:
                    leaf.failed = True

    def clear_failed(self) -> None:
        self.failed.clear()
        for leaf in self.leaves():
            leaf.failed = False

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
                    position: int = None, repeat: bool = None) -> Selection:
        """Select the track that follows the current one.

        Inside a nested node the next leaf follows that node's order and
        repeat policy. Returns the MediaEntry to play, ``ALL_FAILED`` when
        *repeat* would restart a queue whose every track is broken, or
        ``QUEUE_END``. *repeat* defaults to the root node's repeat policy.

        Logs nothing: this is also called from a read-only preview (see
        OCPMediaPlayer.next_track_preview), which runs on every status
        query - the caller that actually acts on the selection logs it.
        """
        if repeat is None:
            repeat = self.root.repeat == Repeat.REPEAT
        idx = self.index(queue, uri=uri, position=position)
        if idx >= 0:
            idx = self._nested_step(queue, idx, forward=True)
            if not isinstance(idx, int):
                return idx
        if idx >= 0 and idx + 1 < len(queue):
            return queue[idx + 1]
        if repeat and queue:
            if self.all_failed(queue):
                return ALL_FAILED
            return queue[0]
        return QUEUE_END

    def select_prev(self, queue: List[MediaEntry], uri: str = None,
                    position: int = None) -> Selection:
        """Select the track before the current one, or ``QUEUE_END``."""
        idx = self.index(queue, uri=uri, position=position)
        if idx >= 0:
            idx = self._nested_step(queue, idx, forward=False)
            if not isinstance(idx, int):
                return idx
        if idx > 0:
            prev_track = queue[idx - 1]
            LOG.debug(f"Previous track: {prev_track.title!r} "
                      f"(queue index {idx - 1}/{len(queue) - 1})")
            return prev_track
        return QUEUE_END

    def select_shuffle(self, queue: List[MediaEntry], current_uri: str = None,
                       repeat: bool = None) -> Selection:
        """Pick a random track from *queue*, excluding the current one and
        every uri already known to be broken.

        Root shuffle picks whole root children: a nested node is entered at
        its first leaf and played through in its own order before the next
        pick. *repeat* defaults to the root node's repeat policy.

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

        if repeat is None:
            repeat = self.root.repeat == Repeat.REPEAT
        current = self._leaf_of(self.current) if self.current is not None \
            else None
        skip = set()
        if current is not None and current.parent is not self.root:
            idx = next((i for i, e in enumerate(queue) if e is current.entry), -1)
            if idx >= 0:
                step = self._nested_step(queue, idx, forward=True)
                if not isinstance(step, int) and step.uri not in self.failed:
                    return step
            skip = {id(l.entry) for l in self._top_node(current).leaves()}
        for node in self.root.children:
            if not node.is_leaf:
                inner = []
                _walk(node, inner)
                skip.update(id(l.entry) for l in inner[1:])
        candidates = [e for e in queue
                      if e.uri != current_uri and e.uri not in self.failed
                      and id(e) not in skip]
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

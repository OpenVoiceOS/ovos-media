"""The recursive queue model behind the player's three playback buffers.

A :class:`PlayTree` is a tree of :class:`PlayNode` objects. A node is either a
**leaf** (it wraps exactly one playable media-entry dict) or **internal** (it
has an ordered list of child nodes and no entry of its own). A node with a
non-empty ``playlist`` array in its wire dict is always internal — leaves and
"has children" are mutually exclusive by construction, never a runtime check.

The three familiar buffers become views of the tree, not separate storage:

- **Currently Playing** is the active leaf (:attr:`PlayTree.cursor`) plus the
  preload window a caller reads by walking :meth:`PlayTree.next_leaf` ahead
  without moving the cursor.
- **Playlist** is :meth:`PlayTree.flatten` from the cursor onward — the flat,
  wire-facing projection of the tree in its current traversal order.
  ``playlist_position``/``playlist_size`` index into this flattening, which is
  why it is recomputed (not cached) whenever a node reshuffles.
- **Search Results** are a separate :class:`PlayTree` built from query
  results; :meth:`PlayTree.graft` copies a subtree of it into the live tree
  when the user selects a result, so a later search can never mutate a queue
  it was never added to.

Playback policy — order (sequential/shuffled) and repeat — attaches to
*nodes*, not the tree. This is what lets an album play in shuffled order
while a spoken-word track inside it stays sequential, or a "repeat this
sub-playlist" policy coexist with a shuffled parent. The one exception is
repeat-one (replay the same leaf forever): that is not a node property, it is
a cursor-level behaviour — it does not change what the traversal considers
"next", it just means the caller re-selects the current leaf instead of
calling :meth:`next_leaf` at all. :class:`PlayTree` therefore only exposes
node-level ``REPEAT`` (cycle the node's children when traversal exhausts
them); repeat-one is the caller's job. A repeating node with exactly one leaf
repeats by replaying that leaf — that IS correct node-repeat semantics for a
single child, not a stall to escape from.

Construction is one-way from the wire: :meth:`PlayNode.from_entry` consumes
plain dicts (deep-copying each leaf's entry, so two occurrences of the same
dict in one payload become two independent leaves and the caller's copy can
be mutated afterwards without touching the tree), so a stored playlist can
never hand back a live node reference to itself — true cycles cannot arrive
this way. :meth:`PlayNode.add_child` is still guarded against grafting a node
into its own subtree, because grafts (:meth:`PlayTree.graft`) move
already-built nodes around, not raw dicts.

Nesting is bounded (``max_depth``, default 8): a *playlist* entry nested
deeper than that is refused with a logged warning and dropped — only that
subtree, its siblings are kept. A non-dict playlist member is likewise
skipped with a warning rather than built into a node. A tree left with no
leaves anywhere (everything dropped, or simply never populated) has a
``None`` cursor and reports ``TREE_END``/size 0, not ``ALL_FAILED`` —
"nothing here" and "everything here failed" are different states.

Grafting into a node that is ``SHUFFLED`` and already has a computed
traversal order preserves that order's already-played prefix: the new child
lands at a random position in the *unplayed* remainder, at or after the
cursor's successor, never before it. Queuing one more track into a shuffled
album mid-playback must never replay an already-heard track or move the
cursor's flat :meth:`PlayTree.position`.

A lazy provider handle is a node with ``unexpanded=True`` and an opaque
``handle`` uri instead of children; :meth:`PlayNode.expand` fills its
children in from a callback. :meth:`PlayTree.needs_expansion_ahead` answers
"is the node after the cursor still a lazy handle", the query the preload
window uses to trigger expansion one node ahead of playback — this module
does not perform the provider I/O itself.

This module has no bus and no threading; it is a pure data structure.
"""
import random
from copy import deepcopy
from enum import Enum
from typing import Callable, List, Optional, Union

from ovos_utils.log import LOG

DEFAULT_MAX_DEPTH = 8


class Order(Enum):
    SEQUENTIAL = 0
    SHUFFLED = 1


class Repeat(Enum):
    NONE = 0
    REPEAT = 1


class TreeEnd:
    """Returned by traversal when there is no further/previous leaf."""


class AllFailed:
    """Returned by traversal when every leaf under the tree has failed.

    Distinct from :class:`TreeEnd` because a repeat cycle must break on it
    instead of restarting an entirely broken tree.
    """


TREE_END = TreeEnd()
ALL_FAILED = AllFailed()


class PlayNode:
    """One node of a :class:`PlayTree`.

    A leaf wraps ``entry`` (a media-entry dict) and has no children. An
    internal node has ordered ``children`` and ``entry`` is ``None``. The two
    shapes never mix: ``from_entry`` decides which one to build and nothing
    later flips a node from one to the other.
    """

    def __init__(self, entry: Optional[dict] = None,
                 children: Optional[List["PlayNode"]] = None,
                 order: Order = Order.SEQUENTIAL,
                 repeat: Repeat = Repeat.NONE,
                 title: str = "",
                 unexpanded: bool = False,
                 handle: str = ""):
        self.entry = entry
        self.children: List["PlayNode"] = children or []
        self.order = order
        self.repeat = repeat
        self.title = title
        # lazy provider handle: no children yet, ``handle`` is the opaque uri
        # a provider callback resolves into children via ``expand``
        self.unexpanded = unexpanded
        self.handle = handle
        self.failed = False
        self.parent: Optional["PlayNode"] = None
        # persisted per-node shuffle permutation over ``children`` indices;
        # rebuilt on first shuffled traversal and whenever the node is
        # re-entered after its shuffled order is exhausted
        self._shuffle_order: Optional[List[int]] = None

    def __repr__(self) -> str:
        if self.is_leaf:
            return f"PlayNode(leaf, {self.entry!r})"
        return f"PlayNode({self.title!r}, {len(self.children)} children)"

    @property
    def is_leaf(self) -> bool:
        return self.entry is not None

    @classmethod
    def from_entry(cls, entry: dict, max_depth: int = DEFAULT_MAX_DEPTH,
                    _depth: int = 0) -> Optional["PlayNode"]:
        """Build a node from a wire media-entry dict.

        A dict with a non-empty ``playlist`` array becomes an internal node,
        its members recursed into the same way. Anything else becomes a leaf
        wrapping a deep copy of *entry*, so the tree never aliases the
        caller's dict. Playlists nested past *max_depth* are refused (logged,
        dropped) rather than raising, so one bad subtree doesn't take its
        siblings down with it; a playlist member that isn't a dict at all is
        skipped the same way. Callers building a node's children from a list
        should skip the ``None`` results.
        """
        playlist = entry.get("playlist") if isinstance(entry, dict) else None
        if not playlist:
            return cls(entry=deepcopy(entry))
        if _depth >= max_depth:
            LOG.warning(f"Dropping playlist nested past max_depth="
                        f"{max_depth}: {entry.get('title', entry)!r}")
            return None
        children = []
        for child_entry in playlist:
            if not isinstance(child_entry, dict):
                LOG.warning(f"Skipping non-dict playlist member: "
                            f"{child_entry!r}")
                continue
            child = cls.from_entry(child_entry, max_depth=max_depth,
                                    _depth=_depth + 1)
            if child is not None:
                children.append(child)
        node = cls(entry=None, title=entry.get("title", ""))
        for child in children:
            node.add_child(child)
        return node

    def add_child(self, node: "PlayNode", index: int = -1) -> None:
        """Append (or insert) *node* as a child, guarding against cycles.

        Refuses *node* if it already sits in this node's ancestor chain, or
        is this node itself — either would turn the tree into a cycle.
        """
        if node is self or node.is_ancestor_of(self):
            raise ValueError("refusing to add a node as its own descendant "
                             "or ancestor - would create a cycle")
        node.parent = self
        self._shuffle_order = None
        if index == -1:
            self.children.append(node)
        else:
            self.children.insert(index, node)

    def is_ancestor_of(self, node: "PlayNode") -> bool:
        """True if *self* is an ancestor of *node* (or *node* itself)."""
        cur = node
        while cur is not None:
            if cur is self:
                return True
            cur = cur.parent
        return False

    def leaves(self) -> List["PlayNode"]:
        """All leaf descendants of this node, structural order (not the
        shuffled traversal order) - used for counting/membership checks."""
        if self.is_leaf:
            return [self]
        result: List["PlayNode"] = []
        for child in self.children:
            result.extend(child.leaves())
        return result

    # traversal order over this node's own children
    def child_order(self) -> List[int]:
        """Indices into ``children`` in this node's current traversal order."""
        if self.order == Order.SEQUENTIAL:
            return list(range(len(self.children)))
        if self._shuffle_order is None or \
                sorted(self._shuffle_order) != list(range(len(self.children))):
            self._shuffle_order = list(range(len(self.children)))
            random.shuffle(self._shuffle_order)
        return self._shuffle_order

    def reshuffle(self) -> None:
        """Force a new permutation on next :meth:`child_order` call."""
        self._shuffle_order = None

    # failure propagation
    @property
    def all_leaves_failed(self) -> bool:
        """True if every leaf under this node has failed.

        Vacuously true for a node with no leaves at all (an empty playlist,
        or one every child of which got dropped): it contributes nothing
        either way, so it must not block an ``all()`` aggregation over its
        siblings from correctly seeing "every real leaf here is failed".
        Callers deciding whether the whole *tree* is dead check
        ``root.all_leaves_failed`` only after confirming the root actually
        has a leaf (``PlayTree.cursor is not None``); a genuinely empty tree
        is "nothing here", not "everything here failed".
        """
        if self.is_leaf:
            return self.failed
        return all(c.all_leaves_failed for c in self.children)

    # lazy expansion
    def expand(self, callback: Callable[[str], List[dict]]) -> None:
        """Resolve a lazy handle's children via *callback(self.handle)*.

        *callback* returns a list of wire entry dicts, built into children
        the same way :meth:`from_entry` builds any other playlist. No I/O
        happens in this module; the callback is entirely the caller's.
        """
        if not self.unexpanded:
            return
        for child_entry in callback(self.handle):
            child = PlayNode.from_entry(child_entry)
            if child is not None:
                self.add_child(child)
        self.unexpanded = False


class PlayTree:
    """A :class:`PlayNode` tree with a cursor addressing the active leaf."""

    def __init__(self, root: Optional[PlayNode] = None):
        self.root = root if root is not None else PlayNode(title="root")
        self.cursor: Optional[PlayNode] = self._first_leaf(self.root)

    @staticmethod
    def _first_leaf(node: PlayNode) -> Optional[PlayNode]:
        if node.is_leaf:
            return node
        for idx in node.child_order():
            leaf = PlayTree._first_leaf(node.children[idx])
            if leaf is not None:
                return leaf
        return None

    @staticmethod
    def _last_leaf(node: PlayNode) -> Optional[PlayNode]:
        if node.is_leaf:
            return node
        for idx in reversed(node.child_order()):
            leaf = PlayTree._last_leaf(node.children[idx])
            if leaf is not None:
                return leaf
        return None

    def _repeat_reentry_leaf(self, parent: PlayNode, just_finished: PlayNode,
                              reverse: bool = False) -> Optional[PlayNode]:
        """The leaf to resume at when *parent* cycles under its REPEAT policy.

        Only *live* (unfailed) leaves are candidates - a repeat cycle must
        never hand back a corpse. A node with exactly one live leaf repeats
        by replaying it, even if that leaf is the one that just finished:
        that's correct node-repeat semantics for a single playable child, not
        a stall to avoid. With more than one live leaf, a fresh permutation
        is drawn each lap (that's what "shuffle repeat" means); a draw that
        hands back the leaf that just finished isn't a new lap at all, so
        it's retried (bounded), and any residual collision is resolved by
        walking the live leaves directly for one that isn't *just_finished*
        rather than drawing again - so the node never falls through to the
        parent while it still has a live leaf to offer, and two calls that
        consume the same random draws from the same tree state always agree.
        """
        live = [l for l in parent.leaves() if not l.failed]
        if len(live) <= 1:
            return live[0] if live else None
        pick = self._last_leaf if reverse else self._first_leaf
        for _ in range(3):
            parent.reshuffle()
            leaf = pick(parent)
            if leaf is not None and not leaf.failed and leaf is not just_finished:
                return leaf
        for candidate in live:
            if candidate is not just_finished:
                return candidate
        return live[0]

    # depth-first leaf traversal honoring each node's order policy
    def _next_sibling_leaf(self, node: PlayNode) -> Union[PlayNode, TreeEnd]:
        just_finished = node
        child = node
        parent = node.parent
        while parent is not None:
            order = parent.child_order()
            pos = order.index(parent.children.index(child))
            for idx in order[pos + 1:]:
                leaf = self._first_leaf(parent.children[idx])
                if leaf is not None:
                    return leaf
            # exhausted this level: cycle it if it repeats and still has a
            # live leaf somewhere under it, else climb. *just_finished* is
            # always the originating leaf, not the intermediate node climbed
            # through - comparing against an internal node here would never
            # detect a real replay and silently defeat the collision guard.
            if parent.repeat == Repeat.REPEAT and not parent.all_leaves_failed:
                leaf = self._repeat_reentry_leaf(parent, just_finished)
                if leaf is not None:
                    return leaf
            child = parent
            parent = parent.parent
        return TREE_END

    def _prev_sibling_leaf(self, node: PlayNode) -> Union[PlayNode, TreeEnd]:
        just_finished = node
        child = node
        parent = node.parent
        while parent is not None:
            order = parent.child_order()
            pos = order.index(parent.children.index(child))
            for idx in reversed(order[:pos]):
                leaf = self._last_leaf(parent.children[idx])
                if leaf is not None:
                    return leaf
            if parent.repeat == Repeat.REPEAT and not parent.all_leaves_failed:
                leaf = self._repeat_reentry_leaf(parent, just_finished, reverse=True)
                if leaf is not None:
                    return leaf
            child = parent
            parent = parent.parent
        return TREE_END

    def next_leaf(self, skip_failed: bool = True) -> Union[PlayNode, TreeEnd, AllFailed]:
        """Advance the cursor to the next leaf, depth-first.

        Failed leaves are skipped when *skip_failed*. Returns ``ALL_FAILED``
        if every leaf under the root has failed, ``TREE_END`` if traversal
        ran out without finding a live leaf, or the new cursor leaf.
        """
        if self.cursor is None:
            return TREE_END
        if skip_failed and self.root.all_leaves_failed:
            return ALL_FAILED
        node = self.cursor
        seen = 0
        total = len(self.flatten())
        while True:
            nxt = self._next_sibling_leaf(node)
            if isinstance(nxt, TreeEnd):
                return TREE_END
            if not (skip_failed and nxt.failed):
                self.cursor = nxt
                return nxt
            node = nxt
            seen += 1
            if seen > total:
                # shouldn't normally trigger: a repeating node whose leaves
                # are all failed climbs out instead of cycling (see
                # _next_sibling_leaf) - kept as a defensive bound so a
                # pathological tree degrades to a clean sentinel instead of
                # spinning, and still only reports ALL_FAILED if the root
                # actually is
                return ALL_FAILED if self.root.all_leaves_failed else TREE_END

    def prev_leaf(self, skip_failed: bool = True) -> Union[PlayNode, TreeEnd, AllFailed]:
        if self.cursor is None:
            return TREE_END
        if skip_failed and self.root.all_leaves_failed:
            return ALL_FAILED
        node = self.cursor
        seen = 0
        total = len(self.flatten())
        while True:
            prev = self._prev_sibling_leaf(node)
            if isinstance(prev, TreeEnd):
                return TREE_END
            if not (skip_failed and prev.failed):
                self.cursor = prev
                return prev
            node = prev
            seen += 1
            if seen > total:
                return ALL_FAILED if self.root.all_leaves_failed else TREE_END

    def mark_failed(self, leaf: PlayNode) -> None:
        leaf.failed = True

    # flat projection
    def flatten(self) -> List[PlayNode]:
        """All leaves, depth-first, in current traversal order."""
        leaves: List[PlayNode] = []

        def _walk(node: PlayNode) -> None:
            if node.is_leaf:
                leaves.append(node)
                return
            for idx in node.child_order():
                _walk(node.children[idx])

        _walk(self.root)
        return leaves

    def position(self) -> int:
        """Index of the cursor leaf in :meth:`flatten`, or -1."""
        flat = self.flatten()
        for i, leaf in enumerate(flat):
            if leaf is self.cursor:
                return i
        return -1

    def size(self) -> int:
        return len(self.flatten())

    def _shuffled_successor_position(self, parent: PlayNode) -> int:
        """Index into *parent*'s persisted shuffle order at/after which a
        graft must land to stay in the unplayed remainder.

        0 if *parent* isn't shuffled, has no persisted order yet, or the
        cursor isn't inside it - nothing there counts as "already played".
        """
        order = parent._shuffle_order
        if parent.order != Order.SHUFFLED or order is None or \
                self.cursor is None or not parent.is_ancestor_of(self.cursor):
            return 0
        node = self.cursor
        while node is not None and node.parent is not parent:
            node = node.parent
        if node is None or node not in parent.children:
            return 0
        top_idx = parent.children.index(node)
        if top_idx not in order:
            return 0
        return order.index(top_idx) + 1

    # copy-graft
    def graft(self, entries: List[dict], position: int = -1,
              parent: Optional[PlayNode] = None,
              max_depth: int = DEFAULT_MAX_DEPTH) -> List[PlayNode]:
        """Deep-copy *entries* into nodes and add them under *parent*.

        *entries* is copied (via ``deepcopy``) before any node is built, so
        mutating the caller's list/dicts afterwards never reaches the tree.
        If *parent* is ``SHUFFLED`` and already has a computed traversal
        order, that order's already-played prefix is preserved: each new
        child is spliced into the unplayed remainder at a random position,
        never before the cursor's successor, instead of forcing a fresh
        reshuffle of the whole node. Returns the grafted nodes.
        """
        parent = parent or self.root
        grafted = []
        for entry in deepcopy(entries):
            node = PlayNode.from_entry(entry, max_depth=max_depth)
            if node is None:
                continue
            old_order = parent._shuffle_order
            old_count = len(parent.children)
            succ = self._shuffled_successor_position(parent)
            insert_at = old_count if position == -1 else position
            parent.add_child(node, index=position)
            if position != -1:
                position += 1
            grafted.append(node)
            if parent.order == Order.SHUFFLED and old_order is not None and \
                    len(old_order) == old_count:
                remapped = [i if i < insert_at else i + 1 for i in old_order]
                slot = random.randint(min(succ, len(remapped)), len(remapped))
                remapped.insert(slot, insert_at)
                parent._shuffle_order = remapped
        return grafted

    # lazy expansion query
    def needs_expansion_ahead(self) -> Optional[PlayNode]:
        """The lazy handle sitting one leaf ahead of the cursor, if any.

        Walks the flattened order to find the node immediately after the
        cursor; if that slot is an unexpanded internal node (a lazy provider
        handle standing in for leaves not built yet), returns it so the
        caller can trigger :meth:`PlayNode.expand` before playback reaches
        it. Returns ``None`` if there is nothing ahead or it's already
        expanded.
        """
        node = self.cursor
        if node is None:
            return None
        nxt = self._peek_next_slot(node)
        if nxt is not None and nxt.unexpanded:
            return nxt
        return None

    def _peek_next_slot(self, node: PlayNode) -> Optional[PlayNode]:
        """Like ``_next_sibling_leaf`` but may return an unexpanded internal
        node instead of only leaves, since that node has no leaves yet."""
        child = node
        parent = node.parent
        while parent is not None:
            order = parent.child_order()
            pos = order.index(parent.children.index(child))
            for idx in order[pos + 1:]:
                candidate = parent.children[idx]
                if candidate.unexpanded:
                    return candidate
                leaf = self._first_leaf(candidate)
                if leaf is not None:
                    return leaf
            child = parent
            parent = parent.parent
        return None

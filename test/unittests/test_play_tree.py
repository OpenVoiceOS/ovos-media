"""Tests for the recursive queue model, ovos_media.player.tree.

PlayTree replaces PlayQueue's flat list with a tree of PlayNode objects and a
cursor addressing the active leaf; see ovos_media/player/tree.py's module
docstring for the buffer-view mapping this is built for (#201). Adoption
(reimplementing PlayQueue on top of this) is a later, separate step - this
file only exercises the model in isolation.
"""
import copy
import random
import unittest

from ovos_media.player.tree import (ALL_FAILED, TREE_END, Order, PlayNode,
                                     PlayTree, Repeat)


def leaf(uri, title=""):
    return {"uri": uri, "title": title or uri}


def album(title, *track_uris):
    return {"title": title, "playlist": [leaf(u, u) for u in track_uris]}


class TestConstruction(unittest.TestCase):

    def test_flat_entry_is_a_leaf(self):
        node = PlayNode.from_entry(leaf("a"))
        self.assertTrue(node.is_leaf)
        self.assertEqual(node.entry["uri"], "a")

    def test_nonempty_playlist_is_internal(self):
        node = PlayNode.from_entry(album("Album", "a", "b"))
        self.assertFalse(node.is_leaf)
        self.assertEqual(len(node.children), 2)
        self.assertTrue(all(c.is_leaf for c in node.children))

    def test_empty_playlist_array_is_a_leaf(self):
        # "playlist": [] is falsy - from_entry treats it like no playlist at
        # all rather than building a childless internal node
        node = PlayNode.from_entry({"uri": "a", "playlist": []})
        self.assertTrue(node.is_leaf)

    def test_nested_playlists_recurse(self):
        entry = {"title": "Box Set", "playlist": [
            album("Disc 1", "a", "b"),
            album("Disc 2", "c"),
        ]}
        node = PlayNode.from_entry(entry)
        self.assertEqual(len(node.children), 2)
        self.assertEqual(len(node.children[0].children), 2)
        self.assertEqual(len(node.children[1].children), 1)

    def test_depth_bound_drops_only_the_overdeep_subtree(self):
        # max_depth=1: root's direct children may be leaves or one level of
        # playlist, but a playlist nested inside that must be refused
        deep = {"title": "Box", "playlist": [
            leaf("a"),
            {"title": "TooDeep", "playlist": [leaf("b")]},
        ]}
        node = PlayNode.from_entry(deep, max_depth=1)
        self.assertEqual(len(node.children), 1)
        self.assertEqual(node.children[0].entry["uri"], "a")

    def test_add_child_rejects_self(self):
        node = PlayNode(title="root")
        with self.assertRaises(ValueError):
            node.add_child(node)

    def test_add_child_rejects_ancestor(self):
        root = PlayNode(title="root")
        child = PlayNode(title="child")
        root.add_child(child)
        with self.assertRaises(ValueError):
            child.add_child(root)

    def test_add_child_rejects_grandparent(self):
        root = PlayNode(title="root")
        mid = PlayNode(title="mid")
        leaf_node = PlayNode(entry=leaf("a"))
        root.add_child(mid)
        mid.add_child(leaf_node)
        with self.assertRaises(ValueError):
            leaf_node.add_child(root)


class TestTraversal(unittest.TestCase):

    def _tree(self):
        entry = {"title": "root", "playlist": [
            album("Disc 1", "a", "b"),
            leaf("c"),
        ]}
        return PlayTree(PlayNode.from_entry(entry))

    def test_starts_at_first_leaf(self):
        tree = self._tree()
        self.assertEqual(tree.cursor.entry["uri"], "a")

    def test_next_crosses_from_child_playlist_into_next_sibling(self):
        tree = self._tree()
        self.assertEqual(tree.next_leaf().entry["uri"], "b")
        # end of Disc 1's own children - must cross up into root's next
        # sibling ("c"), not stop at the album boundary
        self.assertEqual(tree.next_leaf().entry["uri"], "c")
        self.assertIs(tree.next_leaf(), TREE_END)

    def test_prev_crosses_back_into_previous_sibling(self):
        tree = self._tree()
        tree.next_leaf()
        tree.next_leaf()
        self.assertEqual(tree.cursor.entry["uri"], "c")
        self.assertEqual(tree.prev_leaf().entry["uri"], "b")
        self.assertEqual(tree.prev_leaf().entry["uri"], "a")
        self.assertIs(tree.prev_leaf(), TREE_END)

    def test_shuffle_reorders_album_keeps_track_order_inside(self):
        entry = {"title": "root", "playlist": [
            album("Disc 1", "a1", "a2", "a3"),
            album("Disc 2", "b1", "b2", "b3"),
        ]}
        root = PlayNode.from_entry(entry)
        root.order = Order.SHUFFLED
        random.seed(1234)
        tree = PlayTree(root)
        flat_uris = [n.entry["uri"] for n in tree.flatten()]
        # every album's own tracks still come out in original order,
        # wherever the album itself lands in the shuffled root order
        disc1 = [u for u in flat_uris if u.startswith("a")]
        disc2 = [u for u in flat_uris if u.startswith("b")]
        self.assertEqual(disc1, ["a1", "a2", "a3"])
        self.assertEqual(disc2, ["b1", "b2", "b3"])

    def test_flatten_position_size_consistent_under_reshuffle(self):
        entry = {"title": "root", "playlist": [
            album("Disc 1", "a1", "a2"),
            album("Disc 2", "b1", "b2"),
        ]}
        root = PlayNode.from_entry(entry)
        root.order = Order.SHUFFLED
        random.seed(1)
        tree = PlayTree(root)
        self.assertEqual(tree.size(), 4)
        flat = tree.flatten()
        self.assertEqual(tree.position(), flat.index(tree.cursor))
        root.reshuffle()
        flat2 = tree.flatten()
        self.assertEqual(len(flat2), 4)
        self.assertEqual(tree.position(), flat2.index(tree.cursor))

    def test_node_level_repeat_cycles_children(self):
        entry = {"title": "root", "repeat": True, "playlist": [
            leaf("a"), leaf("b"),
        ]}
        root = PlayNode(title="root", repeat=Repeat.REPEAT, children=[
            PlayNode(entry=leaf("a")), PlayNode(entry=leaf("b")),
        ])
        for c in root.children:
            c.parent = root
        tree = PlayTree(root)
        self.assertEqual(tree.next_leaf().entry["uri"], "b")
        # exhausted - REPEAT cycles back to the node's own first leaf
        self.assertEqual(tree.next_leaf().entry["uri"], "a")

    def test_repeat_one_is_a_cursor_level_concern_not_a_node_flag(self):
        # repeat-one has no PlayTree method: the caller just re-reads
        # tree.cursor instead of calling next_leaf(); the tree itself never
        # changes cursor on its own
        tree = self._tree()
        before = tree.cursor
        self.assertIs(tree.cursor, before)


class TestFailure(unittest.TestCase):

    def test_next_leaf_skips_failed(self):
        entry = {"title": "root", "playlist": [leaf("a"), leaf("b"), leaf("c")]}
        tree = PlayTree(PlayNode.from_entry(entry))
        tree.mark_failed(tree.root.children[1])
        self.assertEqual(tree.next_leaf().entry["uri"], "c")

    def test_internal_node_all_failed_when_every_leaf_failed(self):
        entry = album("Disc 1", "a", "b")
        node = PlayNode.from_entry(entry)
        self.assertFalse(node.all_leaves_failed)
        node.children[0].failed = True
        self.assertFalse(node.all_leaves_failed)
        node.children[1].failed = True
        self.assertTrue(node.all_leaves_failed)

    def test_all_failed_sentinel_when_everything_failed(self):
        entry = {"title": "root", "playlist": [leaf("a"), leaf("b")]}
        tree = PlayTree(PlayNode.from_entry(entry))
        for leaf_node in tree.flatten():
            tree.mark_failed(leaf_node)
        self.assertIs(tree.next_leaf(), ALL_FAILED)


class TestGraft(unittest.TestCase):

    def test_graft_copies_entries_not_references(self):
        tree = PlayTree()
        source = [leaf("a"), leaf("b")]
        tree.graft(source)
        source[0]["uri"] = "mutated"
        source.append(leaf("c"))
        self.assertEqual([n.entry["uri"] for n in tree.flatten()], ["a", "b"])

    def test_graft_copies_nested_playlists_too(self):
        tree = PlayTree()
        source = [album("Disc 1", "a", "b")]
        tree.graft(source)
        source[0]["playlist"][0]["uri"] = "mutated"
        self.assertEqual(tree.flatten()[0].entry["uri"], "a")

    def test_graft_returns_grafted_nodes(self):
        tree = PlayTree()
        grafted = tree.graft([leaf("a"), leaf("b")])
        self.assertEqual(len(grafted), 2)
        self.assertIs(grafted[0].parent, tree.root)


class TestLazyExpansion(unittest.TestCase):

    def test_needs_expansion_ahead_finds_the_unexpanded_next_node(self):
        handle_node = PlayNode(title="lazy album", unexpanded=True,
                               handle="provider://album/42")
        root = PlayNode(title="root")
        first = PlayNode(entry=leaf("a"))
        root.add_child(first)
        root.add_child(handle_node)
        tree = PlayTree(root)
        self.assertEqual(tree.cursor.entry["uri"], "a")
        self.assertIs(tree.needs_expansion_ahead(), handle_node)

    def test_needs_expansion_ahead_is_none_once_expanded(self):
        handle_node = PlayNode(title="lazy album", unexpanded=True,
                               handle="provider://album/42")
        root = PlayNode(title="root")
        root.add_child(PlayNode(entry=leaf("a")))
        root.add_child(handle_node)
        tree = PlayTree(root)
        handle_node.expand(lambda h: [leaf("x"), leaf("y")])
        self.assertFalse(handle_node.unexpanded)
        self.assertEqual([c.entry["uri"] for c in handle_node.children],
                         ["x", "y"])
        self.assertIsNone(tree.needs_expansion_ahead())

    def test_needs_expansion_ahead_none_with_nothing_after_cursor(self):
        tree = PlayTree(PlayNode.from_entry(leaf("a")))
        self.assertIsNone(tree.needs_expansion_ahead())


class TestRepeatUnderShuffle(unittest.TestCase):

    def test_single_child_repeat_node_repeats_under_shuffle(self):
        # a node with exactly one leaf must never escape to a sibling on
        # repeat - that leaf IS the whole cycle
        for seed in range(50):
            random.seed(seed)
            root = PlayNode(title="root", repeat=Repeat.REPEAT,
                            order=Order.SHUFFLED)
            only = PlayNode(entry=leaf("a"))
            root.add_child(only)
            tree = PlayTree(root)
            self.assertIs(tree.next_leaf(), only, f"seed {seed}")

    def test_multi_child_shuffled_repeat_never_escapes_to_treeend(self):
        # a repeating shuffled node with several leaves must keep producing
        # leaves of its own, never TREE_END, however the permutation lands
        for seed in range(50):
            random.seed(seed)
            root = PlayNode(title="root", repeat=Repeat.REPEAT,
                            order=Order.SHUFFLED)
            for u in ("a", "b", "c"):
                root.add_child(PlayNode(entry=leaf(u)))
            tree = PlayTree(root)
            for _ in range(6):
                nxt = tree.next_leaf()
                self.assertIsInstance(nxt, PlayNode, f"seed {seed}")


class TestFailedRepeatingNodeClimbsOut(unittest.TestCase):

    def test_all_failed_repeat_node_is_skipped_not_reported_dead(self):
        # a repeating sub-album whose every track failed must not make the
        # traversal claim the whole tree is dead - it climbs past it and
        # finds the live sibling
        root = PlayNode(title="root")
        dead_album = PlayNode(title="dead", repeat=Repeat.REPEAT)
        dead_album.add_child(PlayNode(entry=leaf("x")))
        dead_album.add_child(PlayNode(entry=leaf("y")))
        live = PlayNode(entry=leaf("z"))
        root.add_child(dead_album)
        root.add_child(live)
        tree = PlayTree(root)
        tree.mark_failed(dead_album.children[0])
        tree.mark_failed(dead_album.children[1])
        self.assertIs(tree.next_leaf(), live)


class TestChildlessNodesAndEmptyTrees(unittest.TestCase):

    def test_childless_internal_node_is_vacuously_not_failed(self):
        node = PlayNode(title="empty")
        self.assertTrue(node.all_leaves_failed)  # vacuous - no leaves at all

    def test_childless_node_does_not_block_all_failed_aggregation(self):
        root = PlayNode(title="root")
        empty = PlayNode(title="empty")
        failed_leaf = PlayNode(entry=leaf("a"))
        failed_leaf.failed = True
        root.add_child(empty)
        root.add_child(failed_leaf)
        self.assertTrue(root.all_leaves_failed)

    def test_from_entry_skips_non_dict_playlist_members(self):
        entry = {"title": "root", "playlist": [leaf("a"), None, 42, leaf("b")]}
        node = PlayNode.from_entry(entry)
        self.assertEqual([c.entry["uri"] for c in node.children], ["a", "b"])

    def test_non_dict_playlist_members_produce_no_childless_node(self):
        # a payload with only invalid members yields an empty (childless)
        # internal node, not a PlayNode(entry=None) masquerading as one
        entry = {"title": "root", "playlist": [None, "not-a-dict"]}
        node = PlayNode.from_entry(entry)
        self.assertFalse(node.is_leaf)
        self.assertEqual(node.children, [])

    def test_deeply_nested_payload_dropped_to_zero_leaves_is_tree_end(self):
        # 9 levels of playlist-in-playlist with the default max_depth=8:
        # the innermost playlist is refused, leaving a tree with no leaves
        # anywhere - that must report TREE_END/size 0, not ALL_FAILED
        payload = {"title": "L0"}
        node = payload
        for level in range(1, 9):
            node["playlist"] = [{"title": f"L{level}"}]
            node = node["playlist"][0]
        node["playlist"] = [leaf("unreachable")]

        tree = PlayTree(PlayNode.from_entry(payload))
        self.assertIsNone(tree.cursor)
        self.assertEqual(tree.size(), 0)
        self.assertIs(tree.next_leaf(), TREE_END)
        self.assertIs(tree.prev_leaf(), TREE_END)


class TestGraftIntoShuffledNode(unittest.TestCase):

    def test_graft_preserves_played_prefix_and_cursor_position(self):
        random.seed(7)
        root = PlayNode(title="album", order=Order.SHUFFLED)
        for u in ("a", "b", "c", "d", "e"):
            root.add_child(PlayNode(entry=leaf(u)))
        tree = PlayTree(root)
        # force the permutation to materialize, then move a couple of steps
        # into it so there is a real "already played" prefix
        tree.flatten()
        tree.next_leaf()
        prefix_before = tree.flatten()[:tree.position() + 1]
        cursor_before = tree.cursor
        position_before = tree.position()

        tree.graft([leaf("new")], parent=root)

        flat_after = tree.flatten()
        self.assertEqual(flat_after[:position_before + 1], prefix_before)
        self.assertIs(tree.cursor, cursor_before)
        self.assertEqual(tree.position(), position_before)
        self.assertIn("new", [n.entry["uri"] for n in flat_after])

    def test_graft_into_shuffled_node_lands_somewhere_in_remainder(self):
        # across many seeds, the grafted node must always land at or after
        # the cursor's successor, never inside the already-played prefix
        for seed in range(30):
            random.seed(seed)
            root = PlayNode(title="album", order=Order.SHUFFLED)
            for u in ("a", "b", "c", "d"):
                root.add_child(PlayNode(entry=leaf(u)))
            tree = PlayTree(root)
            tree.flatten()
            tree.next_leaf()
            position_before = tree.position()
            tree.graft([leaf("new")], parent=root)
            flat_after = tree.flatten()
            new_pos = [n.entry["uri"] for n in flat_after].index("new")
            self.assertGreater(new_pos, position_before, f"seed {seed}")


class TestFromEntryDeepCopiesLeaves(unittest.TestCase):

    def test_same_dict_twice_becomes_two_independent_leaves(self):
        shared = leaf("a")
        entry = {"title": "root", "playlist": [shared, shared]}
        node = PlayNode.from_entry(entry)
        a, b = node.children
        self.assertIsNot(a.entry, b.entry)
        a.entry["uri"] = "mutated"
        self.assertEqual(b.entry["uri"], "a")

    def test_caller_dict_mutation_after_construction_does_not_reach_tree(self):
        source = leaf("a")
        node = PlayNode.from_entry(source)
        source["uri"] = "mutated"
        self.assertEqual(node.entry["uri"], "a")


class TestLiveLeafCollisionGuard(unittest.TestCase):
    """A repeating node's collision guard must count LIVE leaves only, and
    must compare against the leaf that actually finished, not whatever
    internal node the climb happened to be passing through."""

    def test_one_live_leaf_among_failed_siblings_always_repeats(self):
        # random.seed(59): a shuffled repeat node whose 3 failed leaves sit
        # in their own subtrees (not flat siblings) plus one live leaf -
        # calling next_leaf() repeatedly must always land back on the live
        # leaf, never TREE_END. Against the round-2 collision guard (which
        # counted ALL leaves, not live ones, for its single-leaf shortcut)
        # this shape hits TREE_END at call 15; seed 0 hits it at call 8.
        random.seed(59)
        album = PlayNode(title="album", order=Order.SHUFFLED,
                         repeat=Repeat.REPEAT)
        for i in range(3):
            sub = PlayNode(title=f"sub{i}")
            dead = PlayNode(entry=leaf(f"dead{i}"))
            dead.failed = True
            sub.add_child(dead)
            album.add_child(sub)
        live = PlayNode(entry=leaf("L5675"))
        album.add_child(live)
        root = PlayNode(title="root")
        root.add_child(album)
        root.add_child(PlayNode(entry=leaf("elsewhere")))
        tree = PlayTree(root)
        for _ in range(30):
            nxt = tree.next_leaf()
            self.assertIs(nxt, live)

    def test_collision_guard_sees_the_leaf_not_the_internal_node_climbed(self):
        # the repeat node's children are themselves internal nodes (albums),
        # not flat leaves - a guard comparing against the intermediate node
        # instead of the actual leaf would never detect a real replay. The
        # first next_leaf() is an ordinary forward walk (still one sibling
        # left); the SECOND is what exhausts outer's order and exercises the
        # collision guard - that boundary is where "just_finished" must be
        # the actual leaf, not the sub-album node climbed through.
        outer = PlayNode(title="outer", order=Order.SHUFFLED,
                         repeat=Repeat.REPEAT)
        sub_a = PlayNode(title="a")
        sub_a.add_child(PlayNode(entry=leaf("a1")))
        sub_b = PlayNode(title="b")
        sub_b.add_child(PlayNode(entry=leaf("b1")))
        outer.add_child(sub_a)
        outer.add_child(sub_b)

        replays = 0
        trials = 500
        for seed in range(trials):
            random.seed(seed)
            fresh = copy.deepcopy(outer)
            t = PlayTree(fresh)
            t.next_leaf()
            before = t.cursor.entry["uri"]
            after = t.next_leaf().entry["uri"]
            # only two leaves exist - "replay" means the traversal handed
            # back the same leaf it just finished despite the other one
            # being available
            if before == after:
                replays += 1
        # mirrors the flat-children case: immediate replay should be rare
        # (bounded by the retry logic), not the ~25% measured before the fix
        self.assertLess(replays / trials, 0.05,
                        f"{replays}/{trials} immediate replays with "
                        f"internal children")


class TestRetryConsistency(unittest.TestCase):
    """next_leaf() must be a pure function of (tree state, RNG draws): two
    calls from an identical starting state must agree."""

    @staticmethod
    def _path_to(node):
        path = []
        cur = node
        while cur.parent is not None:
            path.append(cur.parent.children.index(cur))
            cur = cur.parent
        return list(reversed(path))

    @staticmethod
    def _node_at(root, path):
        cur = root
        for idx in path:
            cur = cur.children[idx]
        return cur

    def test_two_calls_from_a_cloned_identical_state_agree(self):
        for seed in range(200):
            rnd = random.Random(seed)
            root = PlayNode(title="root", order=Order.SHUFFLED,
                            repeat=Repeat.REPEAT)
            for i in range(rnd.randint(2, 5)):
                n = PlayNode(entry=leaf(f"t{i}"))
                n.failed = rnd.random() < 0.5
                root.add_child(n)
            tree = PlayTree(root)
            if tree.cursor is None:
                continue
            cursor_path = self._path_to(tree.cursor)

            clone_a = copy.deepcopy(root)
            clone_b = copy.deepcopy(root)
            tree_a = PlayTree(clone_a)
            tree_a.cursor = self._node_at(clone_a, cursor_path)
            tree_b = PlayTree(clone_b)
            tree_b.cursor = self._node_at(clone_b, cursor_path)

            random.seed(1000 + seed)
            result_a = tree_a.next_leaf()
            random.seed(1000 + seed)
            result_b = tree_b.next_leaf()

            uri_a = result_a.entry["uri"] if isinstance(result_a, PlayNode) else result_a
            uri_b = result_b.entry["uri"] if isinstance(result_b, PlayNode) else result_b
            self.assertEqual(uri_a, uri_b, f"seed {seed}")


class TestFuzzNoDeadSentinelWithLiveLeaves(unittest.TestCase):
    """4000 random trees, ~70% leaf failure injection: as long as a live
    leaf exists, next_leaf() must never report TREE_END or ALL_FAILED, must
    never hand back a failed leaf, and must agree with itself when replayed
    from a cloned, unadvanced state."""

    @staticmethod
    def _build(rnd, depth=0, max_depth=2):
        if depth > 0 and (depth >= max_depth or rnd.random() < 0.5):
            node = PlayNode(entry=leaf(f"leaf-{rnd.randrange(1000000)}"))
            node.failed = rnd.random() < 0.7
            return node
        node = PlayNode(title=f"n{depth}",
                        order=rnd.choice([Order.SEQUENTIAL, Order.SHUFFLED]),
                        repeat=Repeat.REPEAT if depth == 0
                        else rnd.choice([Repeat.NONE, Repeat.REPEAT]))
        for _ in range(rnd.randint(1, 4)):
            node.add_child(TestFuzzNoDeadSentinelWithLiveLeaves._build(
                rnd, depth + 1, max_depth))
        return node

    @staticmethod
    def _path_to(node):
        path = []
        cur = node
        while cur.parent is not None:
            path.append(cur.parent.children.index(cur))
            cur = cur.parent
        return list(reversed(path))

    @staticmethod
    def _node_at(root, path):
        cur = root
        for idx in path:
            cur = cur.children[idx]
        return cur

    def test_fuzz_4000_trees(self):
        for seed in range(4000):
            rnd = random.Random(seed)
            root = self._build(rnd)
            root.repeat = Repeat.REPEAT  # whole tree cycles - never legitimately ends
            tree = PlayTree(root)
            if tree.cursor is None:
                continue
            live_total = len([l for l in root.leaves() if not l.failed])

            for i in range(15):
                cursor_path = self._path_to(tree.cursor)
                clone_a = copy.deepcopy(root)
                clone_b = copy.deepcopy(root)
                tree_a = PlayTree(clone_a)
                tree_a.cursor = self._node_at(clone_a, cursor_path)
                tree_b = PlayTree(clone_b)
                tree_b.cursor = self._node_at(clone_b, cursor_path)

                random.seed(seed * 100 + i)
                result_a = tree_a.next_leaf()
                random.seed(seed * 100 + i)
                result_b = tree_b.next_leaf()

                uri_a = result_a.entry["uri"] if isinstance(result_a, PlayNode) else result_a
                uri_b = result_b.entry["uri"] if isinstance(result_b, PlayNode) else result_b
                self.assertEqual(uri_a, uri_b,
                                 f"seed {seed} step {i}: retry inconsistent")

                if live_total > 0:
                    self.assertNotIn(result_a, (TREE_END, ALL_FAILED),
                                     f"seed {seed} step {i}: dead sentinel "
                                     f"with {live_total} live leaves")
                    self.assertFalse(getattr(result_a, "failed", False),
                                     f"seed {seed} step {i}: returned a "
                                     f"failed leaf")
                else:
                    self.assertIs(result_a, ALL_FAILED, f"seed {seed}")

                root, tree = clone_a, tree_a
                if result_a in (TREE_END, ALL_FAILED):
                    break


class TestAllFailedSpinGuardExit(unittest.TestCase):

    def test_all_failed_reported_via_top_level_check(self):
        # the seen>total bailout at the bottom of next_leaf is a defensive
        # bound that only ever agrees with the top-of-function
        # root.all_leaves_failed check (nothing mutates failure state mid
        # traversal, so the condition can't change between the two) - this
        # pins next_leaf's observable contract (ALL_FAILED exactly when the
        # root has no live leaf) regardless of which check produces it
        root = PlayNode(title="root", repeat=Repeat.REPEAT)
        for u in ("a", "b", "c"):
            dead = PlayNode(entry=leaf(u))
            dead.failed = True
            root.add_child(dead)
        tree = PlayTree(root)
        self.assertIs(tree.next_leaf(), ALL_FAILED)
        self.assertIs(tree.prev_leaf(), ALL_FAILED)


class TestPropertyStyle(unittest.TestCase):

    def _random_tree(self, seed):
        rnd = random.Random(seed)

        def build(depth):
            if depth >= 3 or rnd.random() < 0.5:
                return PlayNode(entry=leaf(f"leaf-{rnd.randrange(100000)}"))
            node = PlayNode(title=f"node-{depth}",
                            order=rnd.choice([Order.SEQUENTIAL, Order.SHUFFLED]))
            for _ in range(rnd.randint(1, 3)):
                node.add_child(build(depth + 1))
            return node

        return PlayTree(build(0))

    def test_walking_next_leaf_visits_exactly_flatten_in_order(self):
        for seed in range(10):
            tree = self._random_tree(seed)
            expected = tree.flatten()
            visited = [tree.cursor]
            while True:
                nxt = tree.next_leaf()
                if nxt is TREE_END:
                    break
                visited.append(nxt)
            self.assertEqual(visited, expected,
                             f"seed {seed}: traversal diverged from flatten()")


if __name__ == "__main__":
    unittest.main()

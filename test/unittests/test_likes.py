"""Tests for LikedSongsStore: malformed-entry tolerance, the MediaEntry
shape search results are built from, and the lock that serializes the
write-through against readers.

The store is persisted JSON: store() does a json.dump that iterates the
dict while like/unlike/play-count writers mutate it from separate
bus-dispatch threads, so an unserialized read raises "dictionary changed
size during iteration" under concurrent like+search traffic.
"""
import threading
import time
import unittest
from unittest.mock import MagicMock

from ovos_utils.ocp import MediaEntry, MediaType, PlaybackType

from ovos_media.catalog import LikedSongsStore


class _FakeStore(dict):
    """A dict with the JsonStorageXDG surface the store writes through."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.path = "/tmp/fake_liked_songs.json"
        self.store_calls = 0

    def store(self):
        self.store_calls += 1


class _RacyStore(_FakeStore):
    """A store whose reads and writes walk the dict in pure Python, with a
    yield point per entry — JsonStorageXDG.store() does a json.dump that
    iterates the same dict, and a reader snapshotting it iterates too. The
    C-level equivalents are atomic under the GIL, which would hide the very
    interleaving the lock exists to prevent.
    """

    def items(self):
        # a live view, deliberately not snapshotted: only the caller's lock
        # can keep a concurrent mutation out of this walk
        out = []
        for pair in super().items():
            time.sleep(0)
            out.append(pair)
        return out

    def store(self):
        super().store()
        for _ in super().items():
            time.sleep(0)


class _LockProbe:
    """Records acquire()/release() calls, standing in for an RLock so a
    test can assert a critical section actually took the lock."""

    def __init__(self):
        self.acquire_count = 0
        self.release_count = 0

    def __enter__(self):
        self.acquire_count += 1
        return self

    def __exit__(self, *exc):
        self.release_count += 1
        return False


class TestMalformedEntryTolerance(unittest.TestCase):
    """A malformed store entry must not crash daemon startup — the store
    is editable outside this process."""

    def test_entry_missing_title_is_skipped(self):
        likes = LikedSongsStore(_FakeStore({
            "http://a.mp3": {"uri": "http://a.mp3"},  # no "title"
            "http://b.mp3": {"uri": "http://b.mp3", "title": "Good Song"},
        }))
        self.assertEqual(likes.titles(), ["Good Song"])

    def test_entry_non_dict_value_is_skipped(self):
        likes = LikedSongsStore(_FakeStore({
            "http://a.mp3": "notadict",
            "http://b.mp3": {"uri": "http://b.mp3", "title": "Good Song"},
        }))
        self.assertEqual(likes.titles(), ["Good Song"])

    def test_entry_list_value_is_skipped(self):
        likes = LikedSongsStore(_FakeStore({"http://a.mp3": [1, 2, 3]}))
        self.assertEqual(likes.titles(), [])


class TestWriteThrough(unittest.TestCase):
    def test_like_persists_the_track(self):
        store = _FakeStore()
        likes = LikedSongsStore(store)

        likes.like("http://x.mp3", title="X", artist="A", image="i.png")

        self.assertEqual(store["http://x.mp3"],
                         {"title": "X", "artist": "A", "image": "i.png",
                          "uri": "http://x.mp3"})
        self.assertEqual(store.store_calls, 1)

    def test_unlike_removes_and_persists(self):
        store = _FakeStore({"http://x.mp3": {"title": "X"}})
        likes = LikedSongsStore(store)

        self.assertTrue(likes.unlike("http://x.mp3"))

        self.assertEqual(dict(store), {})
        self.assertEqual(store.store_calls, 1)

    def test_unlike_of_an_unknown_uri_does_not_persist(self):
        store = _FakeStore()
        likes = LikedSongsStore(store)

        self.assertFalse(likes.unlike("http://x.mp3"))

        self.assertEqual(store.store_calls, 0)

    def test_play_count_increments_and_persists(self):
        store = _FakeStore({"http://liked.mp3": {"title": "Liked"}})
        likes = LikedSongsStore(store)

        self.assertTrue(likes.increment_play_count("http://liked.mp3"))

        self.assertEqual(store["http://liked.mp3"]["play_count"], 1)
        self.assertEqual(store.store_calls, 1)

    def test_play_count_of_an_unliked_track_does_not_persist(self):
        store = _FakeStore()
        likes = LikedSongsStore(store)

        self.assertFalse(likes.increment_play_count("http://nope.mp3"))

        self.assertEqual(store.store_calls, 0)

    def test_play_count_survives_a_concurrent_unlike(self):
        """The entry can be popped by another bus-handler thread between
        the lookup and the mutation - a real race, since bus handlers
        dispatch on a thread pool."""

        class _PoppedBetweenCheckAndIndex(_FakeStore):
            def __contains__(self, key):
                return True

            def get(self, key, default=None):
                return default

        store = _PoppedBetweenCheckAndIndex()
        likes = LikedSongsStore(store)

        self.assertFalse(likes.increment_play_count("http://liked.mp3"))
        self.assertEqual(store.store_calls, 0)


class TestEntryShape(unittest.TestCase):
    """as_entries() must yield canonical MediaEntry objects - the playback
    path and the search results both use attribute access on them."""

    def test_returns_media_entries_sorted_most_played_first(self):
        likes = LikedSongsStore(_FakeStore({
            "file://a.mp3": {"title": "Alpha", "play_count": 2},
            "file://b.mp3": {"title": "Beta", "play_count": 5},
        }))

        entries = likes.as_entries()

        self.assertTrue(all(isinstance(e, MediaEntry) for e in entries))
        self.assertEqual([e.title for e in entries], ["Beta", "Alpha"])
        self.assertTrue(all(e.playback == PlaybackType.AUDIO for e in entries))
        self.assertTrue(all(e.media_type == MediaType.MUSIC for e in entries))

    def test_does_not_mutate_the_persisted_store(self):
        store = _FakeStore({"file://a.mp3": {"title": "Alpha", "play_count": 1}})
        likes = LikedSongsStore(store)

        _ = likes.as_entries()

        self.assertEqual(store["file://a.mp3"],
                         {"title": "Alpha", "play_count": 1})


class TestLocking(unittest.TestCase):
    def test_reads_and_writes_take_the_lock(self):
        likes = LikedSongsStore(_FakeStore({"file://a.mp3": {"title": "A"}}))
        probe = _LockProbe()
        likes._lock = probe

        _ = likes.as_entries()
        likes.like("file://b.mp3", title="B")
        likes.unlike("file://b.mp3")
        likes.increment_play_count("file://a.mp3")

        self.assertGreaterEqual(probe.acquire_count, 4)
        self.assertEqual(probe.acquire_count, probe.release_count)

    def test_concurrent_read_and_write_no_runtime_error(self):
        """Adversarial: readers snapshotting the store while a writer
        mutates it must never raise RuntimeError (dictionary changed size
        during iteration).

        The backing store here iterates in pure Python with a yield point
        on every entry, the way JsonStorageXDG.store()'s json.dump walks
        the dict — a C-level iteration would be atomic under the GIL and
        the race would be undetectable. Replacing the store's lock with a
        no-op makes this fail within ~2s.
        """
        likes = LikedSongsStore(_RacyStore({
            f"file://{i}.mp3": {"title": f"T{i}", "play_count": i}
            for i in range(20)
        }))
        errors = []
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    list(likes.as_entries())
                except RuntimeError as e:
                    errors.append(e)
                    return

        def writer():
            i = 0
            while not stop.is_set():
                uri = f"file://{i % 40}.mp3"
                try:
                    if uri in likes:
                        likes.unlike(uri)
                    else:
                        likes.like(uri, title="T")
                except RuntimeError as e:
                    errors.append(e)
                    return
                i += 1

        threads = [threading.Thread(target=reader) for _ in range(4)]
        threads.append(threading.Thread(target=writer))
        for t in threads:
            t.start()
        time.sleep(2)
        stop.set()
        for t in threads:
            t.join(timeout=2)

        self.assertEqual(errors, [], f"race triggered: {errors}")


class TestStoreSurface(unittest.TestCase):
    def test_membership_and_length_read_the_backing_store(self):
        likes = LikedSongsStore(_FakeStore({"file://a.mp3": {"title": "A"}}))
        self.assertIn("file://a.mp3", likes)
        self.assertNotIn("file://b.mp3", likes)
        self.assertEqual(len(likes), 1)

    def test_path_reports_the_backing_store_path(self):
        likes = LikedSongsStore(_FakeStore())
        self.assertEqual(likes.path, "/tmp/fake_liked_songs.json")

    def test_items_returns_a_snapshot_not_a_live_view(self):
        store = _FakeStore({"file://a.mp3": {"title": "A"}})
        likes = LikedSongsStore(store)

        snapshot = likes.items()
        store["file://b.mp3"] = {"title": "B"}

        self.assertEqual(len(snapshot), 1)

    def test_defaults_to_the_persisted_xdg_store(self):
        with unittest.mock.patch("ovos_media.catalog.likes.JsonStorageXDG") as mock:
            mock.return_value = MagicMock(path="/xdg/OCP_liked_songs.json")
            likes = LikedSongsStore()
        self.assertEqual(likes.path, "/xdg/OCP_liked_songs.json")
        mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()

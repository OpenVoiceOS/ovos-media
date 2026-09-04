"""Tests for PlayHistoryStore: upsert/count/recency ordering, the bound
and its eviction policy, and malformed-entry tolerance - the same idiom
as test_likes.py, since the store is modeled closely on LikedSongsStore.
"""
import unittest
from unittest.mock import MagicMock, patch

from ovos_utils.ocp import MediaEntry, MediaType, PlaybackType

from ovos_media.catalog import PlayHistoryStore

from player_fixture import make_player


class _FakeStore(dict):
    """A dict with the JsonStorageXDG surface the store writes through."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.path = "/tmp/fake_play_history.json"
        self.store_calls = 0

    def store(self):
        self.store_calls += 1


class TestRecordPlay(unittest.TestCase):
    def test_first_play_creates_the_entry(self):
        store = _FakeStore()
        history = PlayHistoryStore(store)

        history.record_play({"uri": "http://a.mp3", "title": "Alpha"})

        self.assertEqual(store["http://a.mp3"]["title"], "Alpha")
        self.assertEqual(store["http://a.mp3"]["play_count"], 1)
        self.assertGreater(store["http://a.mp3"]["last_played"], 0)
        self.assertEqual(store.store_calls, 1)

    def test_second_play_increments_count_and_refreshes_entry(self):
        store = _FakeStore()
        history = PlayHistoryStore(store)

        history.record_play({"uri": "http://a.mp3", "title": "Alpha"})
        first_ts = store["http://a.mp3"]["last_played"]
        history.record_play({"uri": "http://a.mp3", "title": "Alpha (renamed)"})

        self.assertEqual(store["http://a.mp3"]["play_count"], 2)
        self.assertEqual(store["http://a.mp3"]["title"], "Alpha (renamed)")
        self.assertGreaterEqual(store["http://a.mp3"]["last_played"], first_ts)

    def test_missing_uri_is_skipped(self):
        store = _FakeStore()
        history = PlayHistoryStore(store)

        history.record_play({"title": "No URI"})

        self.assertEqual(dict(store), {})
        self.assertEqual(store.store_calls, 0)


class TestEviction(unittest.TestCase):
    def test_bound_reached_evicts_lowest_play_count(self):
        store = _FakeStore({
            "http://low.mp3": {"title": "Low", "play_count": 1, "last_played": 1},
            "http://high.mp3": {"title": "High", "play_count": 9, "last_played": 2},
        })
        history = PlayHistoryStore(store, max_entries=2)

        history.record_play({"uri": "http://new.mp3", "title": "New"})

        self.assertNotIn("http://low.mp3", store)
        self.assertIn("http://high.mp3", store)
        self.assertIn("http://new.mp3", store)
        self.assertEqual(len(store), 2)

    def test_bound_reached_evicts_oldest_last_played_on_tie(self):
        store = _FakeStore({
            "http://old.mp3": {"title": "Old", "play_count": 3, "last_played": 1},
            "http://newer.mp3": {"title": "Newer", "play_count": 3, "last_played": 100},
        })
        history = PlayHistoryStore(store, max_entries=2)

        history.record_play({"uri": "http://newest.mp3", "title": "Newest"})

        self.assertNotIn("http://old.mp3", store)
        self.assertIn("http://newer.mp3", store)
        self.assertIn("http://newest.mp3", store)

    def test_replaying_an_existing_uri_at_capacity_does_not_evict(self):
        store = _FakeStore({
            "http://a.mp3": {"title": "A", "play_count": 1, "last_played": 1},
            "http://b.mp3": {"title": "B", "play_count": 1, "last_played": 2},
        })
        history = PlayHistoryStore(store, max_entries=2)

        history.record_play({"uri": "http://a.mp3", "title": "A"})

        self.assertEqual(len(store), 2)
        self.assertEqual(store["http://a.mp3"]["play_count"], 2)


class TestWritePathMalformedRowTolerance(unittest.TestCase):
    """record_play must never propagate an exception from a malformed
    existing row - that would crash play() and kill playback of every
    track, not just the poisoned one."""

    def test_record_play_over_a_non_dict_row_succeeds(self):
        store = _FakeStore({"http://a.mp3": ["garbage", "row"]})
        history = PlayHistoryStore(store)

        history.record_play({"uri": "http://a.mp3", "title": "Alpha"})

        self.assertEqual(store["http://a.mp3"]["title"], "Alpha")
        self.assertEqual(store["http://a.mp3"]["play_count"], 1)

    def test_record_play_over_a_string_row_succeeds(self):
        store = _FakeStore({"http://a.mp3": "garbage"})
        history = PlayHistoryStore(store)

        history.record_play({"uri": "http://a.mp3", "title": "Alpha"})

        self.assertEqual(store["http://a.mp3"]["play_count"], 1)


class TestMediaTypeRoundTrip(unittest.TestCase):
    """A played movie must round-trip as a video, not silently become
    MUSIC/AUDIO and get handed to an audio backend."""

    def test_video_entry_round_trips_as_video(self):
        store = _FakeStore()
        history = PlayHistoryStore(store)

        history.record_play({"uri": "http://movie.mp4", "title": "Movie",
                             "media_type": MediaType.MOVIE,
                             "playback": PlaybackType.VIDEO})

        entry = history.recent()[0]
        self.assertEqual(entry.media_type, MediaType.MOVIE)
        self.assertEqual(entry.playback, PlaybackType.VIDEO)

    def test_row_without_media_type_defaults_to_music_audio(self):
        history = PlayHistoryStore(_FakeStore({
            "http://a.mp3": {"title": "Alpha", "play_count": 1}}))

        entry = history.recent()[0]
        self.assertEqual(entry.media_type, MediaType.MUSIC)
        self.assertEqual(entry.playback, PlaybackType.AUDIO)

    def test_unrecognised_int_media_type_defaults_without_raising(self):
        """A row written under a newer ovos-utils enum member (or any
        stray int) must not raise ValueError out of recent()/most_played()
        - it defaults to MUSIC/AUDIO instead of crashing the accessor."""
        history = PlayHistoryStore(_FakeStore({
            "http://a.mp3": {"title": "Alpha", "play_count": 1,
                             "media_type": 999, "playback": 999}}))

        entries = history.recent()

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].media_type, MediaType.MUSIC)
        self.assertEqual(entries[0].playback, PlaybackType.AUDIO)

    def test_unrecognised_string_media_type_defaults_without_raising(self):
        history = PlayHistoryStore(_FakeStore({
            "http://a.mp3": {"title": "Alpha", "play_count": 1,
                             "media_type": "music", "playback": "audio"}}))

        entries = history.most_played()

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].media_type, MediaType.MUSIC)
        self.assertEqual(entries[0].playback, PlaybackType.AUDIO)


class TestMalformedEntryTolerance(unittest.TestCase):
    def test_entry_missing_title_is_skipped(self):
        history = PlayHistoryStore(_FakeStore({
            "http://a.mp3": {"uri": "http://a.mp3"},  # no title
            "http://b.mp3": {"uri": "http://b.mp3", "title": "Good", "play_count": 1},
        }))
        entries = history.recent()
        self.assertEqual([e.title for e in entries], ["Good"])

    def test_non_dict_value_is_skipped(self):
        history = PlayHistoryStore(_FakeStore({
            "http://a.mp3": "notadict",
            "http://b.mp3": {"title": "Good", "play_count": 1},
        }))
        self.assertEqual([e.title for e in history.most_played()], ["Good"])


class TestAccessors(unittest.TestCase):
    def test_recent_orders_by_last_played_desc(self):
        history = PlayHistoryStore(_FakeStore({
            "http://a.mp3": {"title": "A", "last_played": 1},
            "http://b.mp3": {"title": "B", "last_played": 3},
            "http://c.mp3": {"title": "C", "last_played": 2},
        }))
        self.assertEqual([e.title for e in history.recent()], ["B", "C", "A"])

    def test_most_played_orders_by_play_count_desc_then_recency(self):
        history = PlayHistoryStore(_FakeStore({
            "http://a.mp3": {"title": "A", "play_count": 1, "last_played": 5},
            "http://b.mp3": {"title": "B", "play_count": 3, "last_played": 1},
            "http://c.mp3": {"title": "C", "play_count": 3, "last_played": 4},
        }))
        self.assertEqual([e.title for e in history.most_played()], ["C", "B", "A"])

    def test_recent_respects_limit(self):
        store = _FakeStore({f"http://{i}.mp3": {"title": f"T{i}", "last_played": i}
                            for i in range(5)})
        history = PlayHistoryStore(store)
        self.assertEqual(len(history.recent(limit=2)), 2)

    def test_entries_are_media_entries(self):
        history = PlayHistoryStore(_FakeStore({
            "http://a.mp3": {"title": "A", "artist": "Artist", "play_count": 2}}))
        entries = history.recent()
        self.assertTrue(all(isinstance(e, MediaEntry) for e in entries))
        self.assertEqual(entries[0].media_type, MediaType.MUSIC)
        self.assertEqual(entries[0].playback, PlaybackType.AUDIO)

    def test_empty_store_yields_no_entries(self):
        history = PlayHistoryStore(_FakeStore())
        self.assertEqual(history.recent(), [])
        self.assertEqual(history.most_played(), [])


class TestStoreSurface(unittest.TestCase):
    def test_defaults_to_the_persisted_xdg_store(self):
        with patch("ovos_media.catalog.history.JsonStorageXDG") as mock:
            mock.return_value = MagicMock(path="/xdg/OCP_play_history.json")
            history = PlayHistoryStore()
        self.assertEqual(history.path, "/xdg/OCP_play_history.json")
        mock.assert_called_once()

    def test_len_and_contains(self):
        history = PlayHistoryStore(_FakeStore({"http://a.mp3": {"title": "A"}}))
        self.assertIn("http://a.mp3", history)
        self.assertEqual(len(history), 1)


class TestPlayerPlayRecordsHistory(unittest.TestCase):
    """play() records the track it starts into the history store, mirroring
    the liked-songs play-count bump (see test_likes.py)."""

    def test_play_records_the_now_playing_entry(self):
        p = make_player()
        p.ocp_config = {}

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        p.media.history.record_play.assert_called_once_with(p.now_playing.as_dict)

    def test_play_does_not_record_when_history_disabled(self):
        p = make_player()
        p.ocp_config = {"history": {"enabled": False}}

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        p.media.history.record_play.assert_not_called()

    def test_play_does_not_record_a_track_that_fails_validation(self):
        """A dead track's skip chain (on_invalid_stream -> play_next ->
        play -> ...) must not record+disk-write a corpse into either
        playlist."""
        p = make_player()
        p.ocp_config = {}

        with patch.object(p, "validate_stream", return_value=False), \
             patch.object(p, "on_invalid_stream"), \
             patch.object(p, "set_player_state"):
            p.play()

        p.media.history.record_play.assert_not_called()


class TestEvictionExemptsRecentWindow(unittest.TestCase):
    """A brand-new track (play_count 1) must not be evicted by its own
    successor once the store saturates - that would make "recently
    played" degenerate to one fresh entry plus a wall of old high-count
    tracks."""

    def test_new_tracks_all_survive_after_saturation(self):
        store = _FakeStore({
            f"http://old{i}.mp3": {"title": f"Old{i}", "play_count": 50,
                                   "last_played": i}
            for i in range(5)
        })
        history = PlayHistoryStore(store, max_entries=5)

        for i in range(5):
            history.record_play({"uri": f"http://new{i}.mp3",
                                 "title": f"New{i}"})

        recent_uris = {e.uri for e in history.recent(limit=10)}
        for i in range(5):
            self.assertIn(f"http://new{i}.mp3", recent_uris)


class TestPlayerLazyHistoryConstruction(unittest.TestCase):
    """OCPMediaPlayer must not open a PlayHistoryStore (no disk write)
    when media.history.enabled is false and no store was injected."""

    def test_no_default_store_constructed_when_disabled(self):
        with patch("ovos_media.player.AudioService"), \
             patch("ovos_media.player.VideoService"), \
             patch("ovos_media.player.WebService"), \
             patch("ovos_media.player.OcpMprisExporter"), \
             patch("ovos_media.player.OCPMediaCatalog") as MockCatalog, \
             patch("ovos_media.player.PlayHistoryStore") as MockHistory:
            from ovos_media.player import OCPMediaPlayer
            OCPMediaPlayer(bus=MagicMock(), config={"history": {"enabled": False}})

        MockHistory.assert_not_called()
        _, kwargs = MockCatalog.call_args
        self.assertIsNone(kwargs["history"])


if __name__ == "__main__":
    unittest.main()

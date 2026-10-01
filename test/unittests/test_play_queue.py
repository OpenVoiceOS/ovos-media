"""Tests for the player's owned queue, ovos_media.player.queue.PlayQueue.

The queue answers "which track comes next"; it never decides what to do with
the answer. Every selection therefore returns either a MediaEntry or one of
the QueueEnd / AllFailed / KeepCurrent results the player branches on.
"""
import unittest
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import MediaEntry, PlaybackType, Playlist

from ovos_media.bus.schemas import validated_entries
from ovos_media.player.queue import (AllFailed, KeepCurrent, PlayQueue,
                                     QueueEnd)


def _entry(uri, title="") -> MediaEntry:
    return MediaEntry(uri=uri, title=title or uri,
                      playback=PlaybackType.AUDIO)


A = _entry("http://a.mp3")
B = _entry("http://b.mp3")
C = _entry("http://c.mp3")


class TestContainer(unittest.TestCase):

    def test_entries_are_owned_not_shared_with_the_caller(self):
        seed = [A, B]
        q = PlayQueue(seed)
        seed.clear()
        self.assertEqual(len(q), 2)

    def test_add_entry_appends_by_default(self):
        q = PlayQueue()
        q.add_entry(A)
        q.add_entry(B)
        self.assertEqual([e.uri for e in q], [A.uri, B.uri])

    def test_add_entry_from_dict_is_deserialized(self):
        q = PlayQueue()
        q.add_entry({"uri": "http://d.mp3", "title": "D"})
        self.assertIsInstance(q[0], MediaEntry)
        self.assertEqual(q[0].title, "D")

    def test_insert_before_the_pointer_moves_the_pointer_along(self):
        q = PlayQueue([A, B])
        q.set_position(1)
        q.add_entry(C, index=0)
        self.assertEqual(q.position, 2)
        self.assertEqual(q[q.position].uri, B.uri)

    def test_index_beyond_the_end_is_refused(self):
        q = PlayQueue([A])
        with self.assertRaises(ValueError):
            q.add_entry(B, index=5)

    def test_clear_resets_the_pointer(self):
        q = PlayQueue([A, B])
        q.set_position(1)
        q.clear()
        self.assertEqual(len(q), 0)
        self.assertEqual(q.position, 0)

    def test_replace_swaps_the_contents(self):
        q = PlayQueue([A])
        q.replace([B, C])
        self.assertEqual([e.uri for e in q], [B.uri, C.uri])

    def test_goto_track_matches_by_uri(self):
        q = PlayQueue([A, B, C])
        q.goto_track(MediaEntry(uri=C.uri))
        self.assertEqual(q.position, 2)

    def test_goto_track_matches_an_empty_uri_against_an_empty_uri(self):
        # a MediaEntry is matched on its uri unconditionally, empty or not,
        # so two untitled empty-uri entries both resolve to the first one
        q = PlayQueue([MediaEntry(uri="", title="first"),
                       MediaEntry(uri="", title="second")])
        q.goto_track(MediaEntry(uri="", title="second"))
        self.assertEqual(q.position, 0)

    def test_goto_track_matches_a_nested_playlist_by_title(self):
        q = PlayQueue([A, Playlist(title="nested")])
        q.goto_track(Playlist(title="nested"))
        self.assertEqual(q.position, 1)

    def test_goto_track_refuses_a_track_that_is_not_a_media_object(self):
        q = PlayQueue([A])
        with self.assertRaises(AssertionError):
            q.goto_track("http://a.mp3")

    def test_add_entry_refuses_a_track_that_is_not_a_media_object(self):
        q = PlayQueue()
        with self.assertRaises(AssertionError):
            q.add_entry("http://a.mp3")

    def test_goto_track_leaves_the_pointer_on_an_unknown_track(self):
        q = PlayQueue([A, B])
        q.set_position(1)
        q.goto_track(MediaEntry(uri="http://nope.mp3"))
        self.assertEqual(q.position, 1)

    def test_out_of_range_position_falls_back_to_the_start(self):
        q = PlayQueue([A, B])
        q.set_position(7)
        self.assertEqual(q.position, 0)

    def test_first_and_last_track_flags_track_the_pointer(self):
        q = PlayQueue([A, B])
        self.assertTrue(q.is_first_track)
        self.assertFalse(q.is_last_track)
        q.set_position(1)
        self.assertFalse(q.is_first_track)
        self.assertTrue(q.is_last_track)

    def test_empty_queue_is_both_first_and_last(self):
        q = PlayQueue()
        self.assertTrue(q.is_first_track)
        self.assertTrue(q.is_last_track)

    def test_length_sums_the_entries(self):
        q = PlayQueue([MediaEntry(uri="a", length=1000),
                       MediaEntry(uri="b", length=500)])
        self.assertEqual(q.length, 1500)


class TestNestedPlaylistMembers(unittest.TestCase):
    """A ``playlist.set`` payload may carry a track that is itself a playlist.

    ``validated_entries`` accepts those, so they reach the queue as nested
    Playlist objects. They are kept in the backing list — sanitization walks
    them there — but they have no uri, so every uri-based consumer reads the
    filtered ``entries`` view instead.
    """

    def _queue_with_nested(self):
        entries = validated_entries([
            {"uri": "http://a.mp3", "title": "A"},
            {"title": "nested", "playlist": [{"uri": "http://b.mp3",
                                              "title": "B"}]}])
        self.assertEqual([type(e).__name__ for e in entries],
                         ["MediaEntry", "Playlist"])
        q = PlayQueue()
        for entry in entries:
            q.add_entry(entry)
        return q

    def test_the_nested_playlist_stays_in_the_backing_list(self):
        q = self._queue_with_nested()
        self.assertEqual(len(q), 2)
        self.assertIsInstance(q[1], Playlist)

    def test_entries_offers_only_playable_members(self):
        q = self._queue_with_nested()
        self.assertEqual([e.uri for e in q.entries], ["http://a.mp3"])

    def test_merging_search_results_survives_a_nested_playlist(self):
        q = self._queue_with_nested()
        merged = q.merged([MediaEntry(uri="http://c.mp3")])
        self.assertEqual([e.uri for e in merged],
                         ["http://a.mp3", "http://c.mp3"])

    def test_advancing_survives_a_nested_playlist(self):
        q = self._queue_with_nested()
        merged = q.merged([MediaEntry(uri="http://c.mp3")])
        self.assertTrue(q.has_next(merged, uri="http://a.mp3"))
        self.assertEqual(q.select_next(merged, uri="http://a.mp3").uri,
                         "http://c.mp3")

    def test_length_ignores_a_nested_playlist(self):
        q = self._queue_with_nested()
        self.assertIsInstance(q.length, (int, float))


class TestPlayerWithNestedPlaylistPayload(unittest.TestCase):
    """The same payload, arriving over the bus edge on
    'ovos.common_play.playlist.set', must leave the player able to advance."""

    def _player(self):
        from ovos_media.player import OCPMediaPlayer
        bus = FakeBus()
        with patch("ovos_media.player.AudioService"), \
             patch("ovos_media.player.VideoService"), \
             patch("ovos_media.player.WebService"), \
             patch("ovos_media.player.OcpMprisExporter"), \
             patch("ovos_media.player.Configuration", return_value={"media": {}}), \
             patch("ovos_media.player.OCPMediaCatalog"):
            player = OCPMediaPlayer(bus, config={})
        player.media.search_playlist.entries = []
        bus.emit(Message("ovos.common_play.playlist.set",
                         {"tracks": [{"uri": "http://a.mp3", "title": "A"},
                                     {"title": "nested",
                                      "playlist": [{"uri": "http://b.mp3",
                                                    "title": "B"}]},
                                     {"uri": "http://c.mp3", "title": "C"}]}))
        return player

    def test_the_payload_reaches_the_queue(self):
        player = self._player()
        self.assertEqual(len(player.playlist), 3)
        player.shutdown()

    def test_next_and_prev_still_work(self):
        player = self._player()
        player.now_playing.uri = "http://a.mp3"
        with patch.object(player, "play"):
            player.play_next()
            self.assertEqual(player.now_playing.uri, "http://c.mp3")
            player.play_prev()
            self.assertEqual(player.now_playing.uri, "http://a.mp3")
        player.shutdown()

    def test_can_next_still_answers(self):
        player = self._player()
        player.now_playing.uri = "http://a.mp3"
        self.assertTrue(player.can_next)
        player.shutdown()


class TestMerge(unittest.TestCase):

    def test_user_entries_come_first_and_search_results_follow(self):
        q = PlayQueue([A])
        self.assertEqual([e.uri for e in q.merged([B, C])],
                         [A.uri, B.uri, C.uri])

    def test_a_search_result_already_queued_is_not_repeated(self):
        q = PlayQueue([A, B])
        self.assertEqual([e.uri for e in q.merged([B, C])],
                         [A.uri, B.uri, C.uri])

    def test_merge_search_off_returns_the_user_queue_alone(self):
        q = PlayQueue([A])
        self.assertEqual([e.uri for e in q.merged([B], merge_search=False)],
                         [A.uri])

    def test_explicit_user_entries_override_the_owned_ones(self):
        q = PlayQueue([A])
        self.assertEqual([e.uri for e in q.merged([], user_entries=[C])],
                         [C.uri])


class TestIndex(unittest.TestCase):

    def test_nothing_selected_and_no_uri_resolves_to_nothing(self):
        q = PlayQueue()
        self.assertEqual(q.index([A, B]), -1)

    def test_the_selected_entry_is_located_by_identity(self):
        q = PlayQueue()
        dup = _entry(A.uri)
        queue = [A, B, dup]
        q.current = dup
        self.assertEqual(q.index(queue), 2)

    def test_a_duplicate_uri_does_not_ping_pong_between_positions(self):
        # [a, b, a]: advancing from the second 'a' must run off the end,
        # not jump back to the first one
        q = PlayQueue()
        second_a = _entry(A.uri)
        queue = [A, B, second_a]
        q.current = second_a
        self.assertIsInstance(q.select_next(queue), QueueEnd)

    def test_the_selected_entry_survives_a_cleared_uri(self):
        q = PlayQueue()
        q.current = B
        # the now-playing uri is gone (end-of-media reset), identity remains
        self.assertEqual(q.index([A, B], uri=None), 1)

    def test_the_position_is_used_when_it_agrees_with_the_uri(self):
        q = PlayQueue()
        queue = [A, B, _entry(B.uri)]
        self.assertEqual(q.index(queue, uri=B.uri, position=2), 2)

    def test_a_position_pointing_elsewhere_is_ignored(self):
        q = PlayQueue()
        self.assertEqual(q.index([A, B], uri=B.uri, position=0), 1)

    def test_an_out_of_range_position_is_ignored(self):
        q = PlayQueue()
        self.assertEqual(q.index([A, B], uri=B.uri, position=99), 1)

    def test_a_uri_absent_from_the_queue_resolves_to_nothing(self):
        q = PlayQueue()
        self.assertEqual(q.index([A, B], uri="http://gone.mp3"), -1)

    def test_the_selected_entry_uri_is_the_last_resort_locator(self):
        q = PlayQueue()
        q.current = _entry(B.uri)  # a different object with the same uri
        self.assertEqual(q.index([A, B]), 1)


class TestNeighbours(unittest.TestCase):

    def test_no_previous_track_at_the_start(self):
        q = PlayQueue()
        self.assertFalse(q.has_prev([A, B], uri=A.uri))

    def test_a_previous_track_after_the_start(self):
        q = PlayQueue()
        self.assertTrue(q.has_prev([A, B], uri=B.uri))

    def test_a_next_track_before_the_end(self):
        q = PlayQueue()
        self.assertTrue(q.has_next([A, B], uri=A.uri))

    def test_no_next_track_on_the_last_one(self):
        q = PlayQueue()
        self.assertFalse(q.has_next([A, B], uri=B.uri))

    def test_an_unlocatable_track_has_no_next(self):
        q = PlayQueue()
        self.assertFalse(q.has_next([A, B], uri="http://gone.mp3"))


class TestSelectNext(unittest.TestCase):

    def test_the_following_track_is_selected(self):
        q = PlayQueue()
        self.assertIs(q.select_next([A, B], uri=A.uri), B)

    def test_the_end_of_the_queue_is_reported(self):
        q = PlayQueue()
        self.assertIsInstance(q.select_next([A, B], uri=B.uri), QueueEnd)

    def test_repeat_restarts_from_the_first_track(self):
        q = PlayQueue()
        self.assertIs(q.select_next([A, B], uri=B.uri, repeat=True), A)

    def test_repeat_on_an_empty_queue_is_still_the_end(self):
        q = PlayQueue()
        self.assertIsInstance(q.select_next([], repeat=True), QueueEnd)

    def test_repeat_refuses_to_restart_a_wholly_broken_queue(self):
        q = PlayQueue()
        q.mark_failed(A.uri)
        q.mark_failed(B.uri)
        self.assertIsInstance(q.select_next([A, B], uri=B.uri, repeat=True),
                              AllFailed)

    def test_repeat_restarts_while_one_track_still_works(self):
        q = PlayQueue()
        q.mark_failed(A.uri)
        self.assertIs(q.select_next([A, B], uri=B.uri, repeat=True), A)

    def test_a_failed_track_is_still_advanced_past(self):
        # failures bound the repeat cycle; they do not filter the sequence
        q = PlayQueue()
        q.mark_failed(B.uri)
        self.assertIs(q.select_next([A, B], uri=A.uri), B)


class TestSelectPrev(unittest.TestCase):

    def test_the_preceding_track_is_selected(self):
        q = PlayQueue()
        self.assertIs(q.select_prev([A, B], uri=B.uri), A)

    def test_the_start_of_the_queue_is_reported(self):
        q = PlayQueue()
        self.assertIsInstance(q.select_prev([A, B], uri=A.uri), QueueEnd)

    def test_an_unlocatable_track_reports_the_start(self):
        q = PlayQueue()
        self.assertIsInstance(q.select_prev([A, B], uri="http://gone.mp3"),
                              QueueEnd)


class TestSelectShuffle(unittest.TestCase):

    def test_the_current_track_is_never_picked(self):
        q = PlayQueue()
        for _ in range(20):
            self.assertIs(q.select_shuffle([A, B], current_uri=A.uri), B)

    def test_a_failed_track_is_never_picked(self):
        q = PlayQueue()
        q.mark_failed(C.uri)
        for _ in range(20):
            self.assertIs(q.select_shuffle([A, B, C], current_uri=A.uri), B)

    def test_the_pick_comes_from_random_choice_over_the_candidates(self):
        q = PlayQueue()
        with patch("ovos_media.player.queue.random.choice",
                   side_effect=lambda c: c[-1]) as choice:
            pick = q.select_shuffle([A, B, C], current_uri=A.uri)
        self.assertIs(pick, C)
        self.assertEqual([e.uri for e in choice.call_args[0][0]],
                         [B.uri, C.uri])

    def test_an_empty_queue_keeps_the_current_track(self):
        q = PlayQueue()
        self.assertIsInstance(q.select_shuffle([], current_uri=A.uri),
                              KeepCurrent)

    def test_an_empty_queue_whose_current_track_failed_is_the_end(self):
        q = PlayQueue()
        q.mark_failed(A.uri)
        self.assertIsInstance(q.select_shuffle([], current_uri=A.uri), QueueEnd)

    def test_a_lone_track_with_repeat_off_is_the_end(self):
        q = PlayQueue()
        self.assertIsInstance(q.select_shuffle([A], current_uri=A.uri), QueueEnd)

    def test_a_lone_track_with_repeat_on_keeps_playing(self):
        q = PlayQueue()
        self.assertIsInstance(q.select_shuffle([A], current_uri=A.uri,
                                               repeat=True), KeepCurrent)

    def test_repeat_does_not_keep_a_failed_current_track(self):
        q = PlayQueue()
        q.mark_failed(A.uri)
        self.assertIsInstance(q.select_shuffle([A], current_uri=A.uri,
                                               repeat=True), QueueEnd)


class TestFailedBookkeeping(unittest.TestCase):

    def test_an_empty_uri_is_not_recorded(self):
        q = PlayQueue()
        q.mark_failed("")
        q.mark_failed(None)
        self.assertEqual(q.failed, set())

    def test_all_failed_needs_every_track_to_have_failed(self):
        q = PlayQueue()
        q.mark_failed(A.uri)
        self.assertFalse(q.all_failed([A, B]))
        q.mark_failed(B.uri)
        self.assertTrue(q.all_failed([A, B]))

    def test_an_empty_queue_never_counts_as_wholly_failed(self):
        q = PlayQueue()
        self.assertFalse(q.all_failed([]))

    def test_clearing_forgets_earlier_failures(self):
        q = PlayQueue()
        q.mark_failed(A.uri)
        q.clear_failed()
        self.assertFalse(q.all_failed([A]))


if __name__ == "__main__":
    unittest.main()

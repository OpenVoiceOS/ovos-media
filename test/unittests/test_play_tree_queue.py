"""The player's queue on PlayTree: nested playlists through the player.

A nested playlist in a ``playlist.set`` payload is a node of the queue tree.
next/previous move by leaf, a node's own order and repeat policy act inside
it, and every wire field stays a flat projection in traversal order.
"""
import unittest
from unittest.mock import patch

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import LoopState, MediaEntry, PlaybackType, Playlist

from ovos_media.player.dispatcher import PlayerSnapshot
from ovos_media.player.queue import PlayQueue, QueueEnd
from ovos_media.player.tree import DEFAULT_MAX_DEPTH, Order, Repeat

A, B1, B2, C = ("http://a.mp3", "http://b1.mp3", "http://b2.mp3",
                "http://c.mp3")


def _track(uri):
    return {"uri": uri, "title": uri, "playback": PlaybackType.AUDIO.value}


class _NestedPlayer(unittest.TestCase):
    """Queue [A, album[B1, B2], C], set over the bus edge."""

    def setUp(self):
        from ovos_media.player import OCPMediaPlayer
        bus = FakeBus()
        with patch("ovos_media.player.AudioService"), \
             patch("ovos_media.player.VideoService"), \
             patch("ovos_media.player.WebService"), \
             patch("ovos_media.player.OcpMprisExporter"), \
             patch("ovos_media.player.Configuration", return_value={"media": {}}), \
             patch("ovos_media.player.OCPMediaCatalog"):
            self.player = OCPMediaPlayer(bus, config={})
        self.player.media.search_playlist.entries = []
        bus.emit(Message("ovos.common_play.playlist.set",
                         {"tracks": [_track(A),
                                     {"title": "album",
                                      "playlist": [_track(B1), _track(B2)]},
                                     _track(C)]}))
        self.player.set_now_playing(self.player.playlist[0])
        self.addCleanup(self.player.shutdown)

    @property
    def album(self):
        return self.player._queue.root.children[1]

    def _walk(self, steps, forward=True):
        uris = [self.player.now_playing.uri]
        with patch.object(self.player, "play"):
            for _ in range(steps):
                if forward:
                    self.player.play_next()
                else:
                    self.player.play_prev()
                uris.append(self.player.now_playing.uri)
        return uris


class TestNestedPlaylistThroughThePlayer(_NestedPlayer):

    def test_next_plays_the_nested_tracks_in_order(self):
        self.assertEqual(self._walk(3), [A, B1, B2, C])

    def test_prev_walks_back_through_the_nested_tracks(self):
        self._walk(3)
        self.assertEqual(self._walk(3, forward=False), [C, B2, B1, A])

    def test_status_reports_the_flat_projection(self):
        self._walk(2)
        snapshot = PlayerSnapshot.of(self.player)
        status = snapshot.as_status_dict
        self.assertEqual(status["playlist_size"], 4)
        self.assertEqual(status["playlist_position"], 2)
        self.assertEqual([e["uri"] for e in snapshot.queue], [A, B1, B2, C])

    def test_node_shuffle_reorders_only_its_own_tracks(self):
        self.album.order = Order.SHUFFLED
        with patch("ovos_media.player.tree.random.shuffle",
                   side_effect=lambda order: order.reverse()):
            self.assertEqual(self._walk(3), [A, B2, B1, C])
            queue = PlayerSnapshot.of(self.player).queue
        self.assertEqual([e["uri"] for e in queue], [A, B2, B1, C])

    def test_node_repeat_cycles_its_own_tracks(self):
        self.album.repeat = Repeat.REPEAT
        self.assertEqual(self._walk(5), [A, B1, B2, B1, B2, B1])

    def test_a_repeating_node_whose_tracks_all_failed_is_left(self):
        self.album.repeat = Repeat.REPEAT
        self._walk(1)
        self.player._queue.mark_failed(B1)
        self.player._queue.mark_failed(B2)
        self.assertEqual(self._walk(2), [B1, B2, C])

    def test_root_shuffle_plays_a_nested_node_through_before_the_next_pick(self):
        self._walk(1)
        self.player.shuffle = True
        self.assertEqual(self._walk(1), [B1, B2])

    def test_root_shuffle_enters_a_nested_node_at_its_first_track(self):
        self.player.shuffle = True
        with patch("ovos_media.player.queue.random.choice",
                   side_effect=lambda candidates: candidates[0]) as choice:
            self._walk(1)
        self.assertEqual([e.uri for e in choice.call_args.args[0]], [B1, C])

    def test_global_flags_are_the_root_policy(self):
        self.player.shuffle = True
        self.player.loop_state = LoopState.REPEAT
        root = self.player._queue.root
        self.assertEqual((root.order, root.repeat),
                         (Order.SHUFFLED, Repeat.REPEAT))
        self.player.loop_state = LoopState.REPEAT_TRACK
        self.assertEqual(root.repeat, Repeat.NONE)
        self.assertEqual(self.player.loop_state, LoopState.REPEAT_TRACK)


class TestQueueGuards(unittest.TestCase):

    def test_a_playlist_that_contains_itself_is_dropped(self):
        loop = Playlist(title="loop")
        loop.append(MediaEntry(uri=A))
        loop.append(loop)
        q = PlayQueue([loop])
        self.assertEqual([e.uri for e in q], [A])

    def test_a_playlist_nested_past_the_depth_bound_is_dropped(self):
        inner = Playlist(title="deep")
        inner.append(MediaEntry(uri=B1))
        for _ in range(DEFAULT_MAX_DEPTH):
            outer = Playlist(title="deep")
            outer.append(inner)
            inner = outer
        q = PlayQueue([MediaEntry(uri=A), inner])
        self.assertEqual([e.uri for e in q], [A])

    def test_adding_a_playlist_copies_its_structure(self):
        album = Playlist(title="album")
        album.append(MediaEntry(uri=B1))
        q = PlayQueue([album])
        album.append(MediaEntry(uri=B2))
        album.clear()
        self.assertEqual([e.uri for e in q], [B1])

    def test_a_nested_node_end_continues_into_the_flat_queue(self):
        album = Playlist(title="album")
        album.append(MediaEntry(uri=B1))
        q = PlayQueue([album])
        merged = q.merged([MediaEntry(uri=C)])
        q.current = merged[0]
        self.assertEqual(q.select_next(merged).uri, C)
        q.current = merged[1]
        self.assertIsInstance(q.select_next(merged), QueueEnd)


if __name__ == "__main__":
    unittest.main()

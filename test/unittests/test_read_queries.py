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
"""Tests for the two OCP-1 §4.4.2 read-only queries:
'ovos.common_play.disambiguation' (the last playback request's candidate
set) and 'ovos.common_play.likes' (the liked-songs store).

Handlers are called directly (the fixture's player is built with __new__,
so it has no live bus_api registration) — the registration itself is
covered by TestRegistrationTableCompleteness in test_bus_api.py.
"""
import unittest

from ovos_bus_client.message import Message
from ovos_utils.ocp import MediaEntry

from ovos_media.catalog import LikedSongsStore
from ovos_media.player.dispatcher import PlayerSnapshot

from player_fixture import make_player


class _FakeStore(dict):
    """A dict with the JsonStorageXDG surface LikedSongsStore writes
    through (mirrors test_likes.py's fixture)."""

    def store(self):
        pass


class TestDisambiguationQuery(unittest.TestCase):
    """'ovos.common_play.disambiguation' answers with the candidate set the
    current queue was chosen from, in descending match order."""

    def test_empty_when_nothing_requested_yet(self):
        p = make_player()

        replies = []
        p.bus.on("ovos.common_play.disambiguation.response",
                 lambda m: replies.append(m))
        p.handle_disambiguation_query(Message("ovos.common_play.disambiguation"))

        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].data, {"entries": []})

    def test_populated_candidate_set_in_descending_match_order(self):
        p = make_player()
        e1 = MediaEntry(uri="a", title="A", match_confidence=90)
        e2 = MediaEntry(uri="b", title="B", match_confidence=50)
        p.media.search_playlist.entries = [e1, e2]

        replies = []
        p.bus.on("ovos.common_play.disambiguation.response",
                 lambda m: replies.append(m))
        p.handle_disambiguation_query(Message("ovos.common_play.disambiguation"))

        entries = replies[0].data["entries"]
        self.assertEqual([e["uri"] for e in entries], ["a", "b"])
        self.assertEqual(entries, [e1.as_dict, e2.as_dict])

    def test_answers_as_a_reply_not_a_broadcast(self):
        """message.response() targets the querying source, per OCP-1
        §4.4.2 ('each is answered as a reply to the query message')."""
        p = make_player()
        replies = []
        p.bus.on("ovos.common_play.disambiguation.response",
                 lambda m: replies.append(m))

        p.handle_disambiguation_query(
            Message("ovos.common_play.disambiguation",
                   context={"source": "remote-client",
                            "destination": ["OCP"]}))

        self.assertEqual(replies[0].context.get("destination"),
                         "remote-client")

    def test_snapshot_carries_the_disambiguation_payload(self):
        snap = PlayerSnapshot(candidates=({"uri": "a"}, {"uri": "b"}))
        self.assertEqual(snap.as_disambiguation_dict,
                         {"entries": [{"uri": "a"}, {"uri": "b"}]})


class TestLikesQuery(unittest.TestCase):
    """'ovos.common_play.likes' answers with the liked-songs store's
    entries."""

    def test_empty_when_nothing_liked(self):
        p = make_player()
        p.media.likes = LikedSongsStore(store=_FakeStore())

        replies = []
        p.bus.on("ovos.common_play.likes.response",
                 lambda m: replies.append(m))
        p.handle_likes_query(Message("ovos.common_play.likes"))

        self.assertEqual(replies[0].data, {"entries": []})

    def test_like_round_trip_through_the_real_store(self):
        p = make_player()
        p.media.likes = LikedSongsStore(store=_FakeStore())

        p.media.likes.like("http://example.com/song.mp3", title="Song",
                           artist="Artist", image="http://img")

        replies = []
        p.bus.on("ovos.common_play.likes.response",
                 lambda m: replies.append(m))
        p.handle_likes_query(Message("ovos.common_play.likes"))

        entries = replies[0].data["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["uri"], "http://example.com/song.mp3")
        self.assertEqual(entries[0]["title"], "Song")
        self.assertEqual(entries[0]["artist"], "Artist")

    def test_unliked_song_drops_out(self):
        p = make_player()
        p.media.likes = LikedSongsStore(store=_FakeStore())
        p.media.likes.like("http://example.com/song.mp3", title="Song")
        p.media.likes.unlike("http://example.com/song.mp3")

        replies = []
        p.bus.on("ovos.common_play.likes.response",
                 lambda m: replies.append(m))
        p.handle_likes_query(Message("ovos.common_play.likes"))

        self.assertEqual(replies[0].data, {"entries": []})


if __name__ == "__main__":
    unittest.main()

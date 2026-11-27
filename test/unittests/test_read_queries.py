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
"""Tests for the OCP-1 §4.4.2/§4.4.3 read-only queries:
'ovos.common_play.disambiguation' (the last playback request's candidate
set), 'ovos.common_play.likes' (the liked-songs store) and
'ovos.common_play.collection' (named collections - "recently played" and
"most played" - backed by the play-history store).

Handlers are called directly (the fixture's player is built with __new__,
so it has no live bus_api registration) — the registration itself is
covered by TestRegistrationTableCompleteness in test_bus_api.py.
"""
import unittest

from ovos_bus_client.message import Message
from ovos_utils.ocp import MediaEntry

from ovos_media.catalog import LikedSongsStore, PlayHistoryStore
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


class TestCollectionQuery(unittest.TestCase):
    """'ovos.common_play.collection' answers a named lookup against the
    intrinsic playlists backed by the play-history store, per OCP-1
    §4.4.3."""

    def _query(self, p, name):
        replies = []
        p.bus.on("ovos.common_play.collection.response",
                 lambda m: replies.append(m))
        p.handle_collection_query(
            Message("ovos.common_play.collection", {"name": name}))
        return replies[0]

    def test_recently_played_reads_the_history_store_in_recency_order(self):
        p = make_player()
        p.media.history = PlayHistoryStore(store=_FakeStore())
        p.media.history.record_play({"uri": "http://a.mp3", "title": "A"})
        p.media.history.record_play({"uri": "http://b.mp3", "title": "B"})

        reply = self._query(p, "recently played")

        self.assertEqual(reply.data["name"], "recently played")
        self.assertEqual([e["uri"] for e in reply.data["entries"]],
                         ["http://b.mp3", "http://a.mp3"])

    def test_most_played_reads_the_history_store_in_play_count_order(self):
        p = make_player()
        p.media.history = PlayHistoryStore(store=_FakeStore())
        p.media.history.record_play({"uri": "http://a.mp3", "title": "A"})
        p.media.history.record_play({"uri": "http://b.mp3", "title": "B"})
        p.media.history.record_play({"uri": "http://b.mp3", "title": "B"})

        reply = self._query(p, "most played")

        self.assertEqual([e["uri"] for e in reply.data["entries"]],
                         ["http://b.mp3", "http://a.mp3"])

    def test_unknown_name_answers_empty_entries_not_an_error(self):
        p = make_player()
        p.media.history = PlayHistoryStore(store=_FakeStore())

        reply = self._query(p, "some made up playlist")

        self.assertEqual(reply.data, {"name": "some made up playlist",
                                      "entries": []})

    def test_liked_songs_is_reserved_and_answers_empty(self):
        """"liked songs" is exclusively 'ovos.common_play.likes' (OCP-1
        §4.4.2) - the collection query must not alias it to anything."""
        p = make_player()
        p.media.history = PlayHistoryStore(store=_FakeStore())
        p.media.history.record_play({"uri": "http://a.mp3", "title": "A"})

        reply = self._query(p, "liked songs")

        self.assertEqual(reply.data["entries"], [])

    def test_name_match_is_case_insensitive(self):
        p = make_player()
        p.media.history = PlayHistoryStore(store=_FakeStore())
        p.media.history.record_play({"uri": "http://a.mp3", "title": "A"})

        reply = self._query(p, "RECENTLY PLAYED")

        self.assertEqual(len(reply.data["entries"]), 1)

    def test_history_disabled_answers_empty_for_any_name(self):
        p = make_player()
        p.media.history = None

        reply = self._query(p, "recently played")

        self.assertEqual(reply.data, {"name": "recently played",
                                      "entries": []})

    def test_answers_as_a_reply_not_a_broadcast(self):
        p = make_player()
        p.media.history = None
        replies = []
        p.bus.on("ovos.common_play.collection.response",
                 lambda m: replies.append(m))

        p.handle_collection_query(
            Message("ovos.common_play.collection", {"name": "recently played"},
                   context={"source": "remote-client",
                            "destination": ["OCP"]}))

        self.assertEqual(replies[0].context.get("destination"),
                         "remote-client")


class TestCollectionQueryMalformedName(unittest.TestCase):
    """A ``name`` that is missing, None, or not a string must still get an
    answer - never an unhandled exception at the bus edge, which would
    swallow the response and leave the caller hanging to timeout - and
    the empty-entries behavior must not depend on whether history is
    enabled (OCP-1 §4.4.3: unrecognised name -> empty entries, always)."""

    def _message(self, name):
        if name is _MISSING:
            return Message("ovos.common_play.collection", {})
        return Message("ovos.common_play.collection", {"name": name})

    def _expected_echo(self, name):
        return "" if name is _MISSING else name

    def test_malformed_or_missing_names(self):
        for history_enabled in (True, False):
            for name in (_MISSING, None, 5, ["recently played"]):
                with self.subTest(history_enabled=history_enabled, name=name):
                    p = make_player()
                    if history_enabled:
                        p.media.history = PlayHistoryStore(store=_FakeStore())
                        p.media.history.record_play(
                            {"uri": "http://a.mp3", "title": "A"})
                    else:
                        p.media.history = None

                    replies = []
                    p.bus.on("ovos.common_play.collection.response",
                            lambda m: replies.append(m))
                    p.handle_collection_query(self._message(name))

                    self.assertEqual(len(replies), 1)
                    self.assertEqual(replies[0].data,
                                     {"name": self._expected_echo(name),
                                      "entries": []})


_MISSING = object()


if __name__ == "__main__":
    unittest.main()

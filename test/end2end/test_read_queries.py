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
"""End-to-end tests for the OCP-1 §4.4.2 read-only queries, driven through
a real OCPMediaPlayer on a FakeBus (OCPPlayerHarness).

OCPMediaCatalog is mocked by the harness (see ovoscope.media), so
'search_playlist' and 'likes' are swapped for real objects here — the same
ones a live daemon (unmocked catalog) owns — to exercise the actual
storage and serialization path play_media() and handle_like() write to.
"""
import time
import unittest

from ovos_bus_client.message import Message
from ovos_utils.ocp import MediaEntry, PlaybackType, Playlist

from ovoscope.media import OCPPlayerHarness

from ovos_media.catalog import LikedSongsStore


class _FakeStore(dict):
    """A dict with the JsonStorageXDG surface LikedSongsStore writes
    through (mirrors test/unittests/test_likes.py's fixture)."""

    def store(self):
        pass


def _audio_entry(uri: str, title: str, match_confidence: int) -> MediaEntry:
    return MediaEntry(uri=uri, playback=PlaybackType.AUDIO, title=title,
                      match_confidence=match_confidence)


def _query(h: OCPPlayerHarness, topic: str) -> dict:
    replies = []
    h.bus.on(f"{topic}.response", lambda m: replies.append(m))
    h.bus.emit(Message(topic))
    time.sleep(0.05)
    assert len(replies) == 1, f"expected exactly one reply to {topic}"
    return replies[0].data


class TestDisambiguationQueryE2E(unittest.TestCase):
    """'ovos.common_play.disambiguation' over a real player/bus."""

    def test_play_with_disambiguation_is_answered_in_match_order(self):
        with OCPPlayerHarness() as h:
            h.player.media.search_playlist = Playlist()
            low = _audio_entry("http://example.com/low.mp3", "Low", 20)
            high = _audio_entry("http://example.com/high.mp3", "High", 95)
            mid = _audio_entry("http://example.com/mid.mp3", "Mid", 50)

            h.bus.emit(Message("ovos.common_play.play", {
                "media": high.as_dict,
                "playlist": [high.as_dict],
                "disambiguation": [low.as_dict, high.as_dict, mid.as_dict],
            }))
            time.sleep(0.05)

            data = _query(h, "ovos.common_play.disambiguation")

            self.assertEqual([e["uri"] for e in data["entries"]],
                             ["http://example.com/high.mp3",
                              "http://example.com/mid.mp3",
                              "http://example.com/low.mp3"])
            for entry in data["entries"]:
                self.assertIn("title", entry)
                self.assertIn("uri", entry)

    def test_repicking_a_candidate_keeps_the_candidate_set(self):
        """Playing an entry the previous request already offered as a
        candidate must leave the full candidate set queryable."""
        with OCPPlayerHarness() as h:
            h.player.media.search_playlist = Playlist()
            a = _audio_entry("http://example.com/a.mp3", "A", 95)
            b = _audio_entry("http://example.com/b.mp3", "B", 50)
            candidates = [a.as_dict, b.as_dict]

            h.bus.emit(Message("ovos.common_play.play", {
                "media": a.as_dict, "disambiguation": candidates}))
            time.sleep(0.05)
            # the user picks the other candidate from the set just shown
            h.bus.emit(Message("ovos.common_play.play", {
                "media": b.as_dict, "disambiguation": candidates}))
            time.sleep(0.05)

            data = _query(h, "ovos.common_play.disambiguation")

            self.assertEqual([e["uri"] for e in data["entries"]],
                             ["http://example.com/a.mp3",
                              "http://example.com/b.mp3"])

    def test_empty_before_any_playback_request(self):
        with OCPPlayerHarness() as h:
            h.player.media.search_playlist = Playlist()

            data = _query(h, "ovos.common_play.disambiguation")

            self.assertEqual(data, {"entries": []})


class TestLikesQueryE2E(unittest.TestCase):
    """'ovos.common_play.likes' over a real player/bus."""

    def test_liked_track_appears_in_the_query(self):
        with OCPPlayerHarness() as h:
            h.player.media.likes = LikedSongsStore(store=_FakeStore())

            h.bus.emit(Message("ovos.common_play.like",
                               {"uri": "http://example.com/fav.mp3",
                                "title": "Favourite", "artist": "Someone"}))
            time.sleep(0.05)

            data = _query(h, "ovos.common_play.likes")

            self.assertEqual(len(data["entries"]), 1)
            self.assertEqual(data["entries"][0]["uri"],
                             "http://example.com/fav.mp3")
            self.assertEqual(data["entries"][0]["title"], "Favourite")

    def test_empty_when_nothing_liked(self):
        with OCPPlayerHarness() as h:
            h.player.media.likes = LikedSongsStore(store=_FakeStore())

            data = _query(h, "ovos.common_play.likes")

            self.assertEqual(data, {"entries": []})


if __name__ == "__main__":
    unittest.main()

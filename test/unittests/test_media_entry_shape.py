"""Regression tests for MediaEntry representation consistency.

Three invariants hold the representation together:

* ``NowPlaying.as_dict`` carries the full MediaEntry field set, so a field
  added to MediaEntry reaches the GUI/bus payload without further work.
* NowPlaying subclasses the ``@dataclass``-decorated MediaEntry but is not
  itself decorated and adds plain instance attributes (bus, _player, ...) in
  a custom ``__init__``. orjson only recognizes a type as a dataclass via
  that exact class's own ``__dict__``, not an inherited one, so serializing
  a NowPlaying instance directly raises
  ``TypeError: Type is not JSON serializable: NowPlaying`` when orjson is
  installed, so the bus payload path goes through ``as_dict``.
* the liked-songs search returns MediaEntry objects, never the raw
  persisted store dicts: the playback path expects entries, and handing out
  the stored dicts lets a caller mutate the on-disk store.
"""
import unittest
from unittest.mock import patch

import orjson

from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import MediaEntry, MediaType, PlaybackType


class TestNowPlayingSerializesAllFields(unittest.TestCase):
    """NowPlaying.as_dict must serialize the full MediaEntry field set."""

    def _make_now_playing(self):
        from ovos_media.player import NowPlaying
        with patch("ovos_media.player.now_playing.load_stream_extractors"):
            return NowPlaying(FakeBus())

    def test_as_dict_covers_every_dataclass_field(self):
        np = self._make_now_playing()
        expected = set(MediaEntry(uri="u").as_dict)
        self.assertEqual(set(np.as_dict), expected)

    def test_non_legacy_fields_survive_round_trip(self):
        # match_confidence and javascript were dropped by the old hard-coded
        # key list; they must now round-trip through as_dict.
        np = self._make_now_playing()
        np.match_confidence = 73
        np.javascript = "play();"
        self.assertEqual(np.as_dict["match_confidence"], 73)
        self.assertEqual(np.as_dict["javascript"], "play();")

    def test_as_dict_is_orjson_serializable(self):
        # NowPlaying is not itself @dataclass-decorated, so orjson can't
        # serialize the instance directly; as_dict must return a plain dict
        # built from a real MediaEntry, which orjson.dumps happily accepts.
        np = self._make_now_playing()
        np.uri = "file://track.mp3"
        as_dict = np.as_dict
        self.assertEqual(orjson.loads(orjson.dumps(as_dict))["uri"],
                          "file://track.mp3")

    def test_now_playing_used_in_bus_payload_is_orjson_serializable(self):
        # exercises the same shape now_playing=np.as_dict takes when
        # forwarded into a bus payload (eg. handle_status)
        np = self._make_now_playing()
        np.uri = "file://track.mp3"
        payload = {"now_playing": np.as_dict}
        self.assertEqual(orjson.loads(orjson.dumps(payload))["now_playing"]["uri"],
                          "file://track.mp3")


class TestSearchResultShape(unittest.TestCase):
    """The liked-songs search yields dicts (the OCP search contract)
    carrying the same data the MediaEntry path exposes as attributes."""

    def _make_skill(self, liked):
        from ovos_media.catalog import LikedSongsStore
        from ovos_media.skill import OCPVoiceSkill

        class _FakeStore(dict):
            def store(self):
                pass

        skill = OCPVoiceSkill.__new__(OCPVoiceSkill)
        skill.likes = LikedSongsStore(_FakeStore(liked))
        skill.skill_icon = "icon.svg"
        skill.skill_id = "ovos.common_play"
        # __new__ bypasses __init__, so the attributes search_db reads
        # directly (no defensive getattr - see skill.py) must be set here
        skill.catalog = None
        skill.history_enabled = True
        return skill

    def test_search_song_branch_yields_serializable_dicts(self):
        skill = self._make_skill(
            {"file://a.mp3": {"title": "Alpha", "play_count": 1}})

        with patch.object(skill, "ocp_voc_match",
                          side_effect=lambda phrase: {"song_name": "alpha"}):
            results = list(skill.search_db("alpha", MediaType.MUSIC))

        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["uri"], "file://a.mp3")
        self.assertEqual(r["title"], "Alpha")
        self.assertEqual(r["playback"], PlaybackType.AUDIO)
        self.assertEqual(MediaEntry.from_dict(r).title, "Alpha")

    def test_playlist_branch_yields_the_liked_songs_most_played_first(self):
        skill = self._make_skill({
            "file://a.mp3": {"title": "Alpha", "play_count": 2},
            "file://b.mp3": {"title": "Beta", "play_count": 5},
        })

        with patch.object(skill, "ocp_voc_match",
                          side_effect=lambda phrase: {"playlist_name": "liked songs"}):
            results = list(skill.search_db("liked songs", MediaType.MUSIC))

        self.assertEqual(len(results), 1)
        self.assertEqual([t["title"] for t in results[0]["playlist"]],
                         ["Beta", "Alpha"])


if __name__ == "__main__":
    unittest.main()

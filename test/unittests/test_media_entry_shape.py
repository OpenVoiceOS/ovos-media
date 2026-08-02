"""Regression tests for MediaEntry representation consistency.

Covers defects surfaced by the ecosystem audit:

* NowPlaying used to override ``as_dict`` with a hard-coded key list, so any
  field added to MediaEntry was silently dropped from the GUI/bus payload.
* NowPlaying subclasses the ``@dataclass``-decorated MediaEntry but is not
  itself decorated and adds plain instance attributes (bus, _player, ...) in
  a custom ``__init__``. orjson only recognizes a type as a dataclass via
  that exact class's own ``__dict__``, not an inherited one, so serializing
  a NowPlaying instance directly used to raise
  ``TypeError: Type is not JSON serializable: NowPlaying`` whenever orjson
  was installed (the GUI-update/bus payload path via ``as_dict``).
* ``OCPMediaCatalog.liked_songs_playlist`` used to return (and mutate) the raw
  persisted store dicts, mixing plain dicts with the MediaEntry objects the
  playback path expects and polluting the on-disk store.
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
        with patch("ovos_media.player.load_stream_extractors"):
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

    def test_now_playing_used_in_gui_payload_is_orjson_serializable(self):
        # exercises the same path OCPMediaPlayer._update_gui uses:
        # now_playing=np.as_dict passed straight into the GUI/bus payload
        np = self._make_now_playing()
        np.uri = "file://track.mp3"
        payload = {"now_playing": np.as_dict}
        self.assertEqual(orjson.loads(orjson.dumps(payload))["now_playing"]["uri"],
                          "file://track.mp3")


class TestLikedSongsPlaylistShape(unittest.TestCase):
    """liked_songs_playlist must yield canonical MediaEntry objects."""

    def _make_catalog(self, liked):
        from ovos_media.player import OCPMediaCatalog
        cat = OCPMediaCatalog.__new__(OCPMediaCatalog)
        cat.liked_songs = dict(liked)
        cat.skill_icon = "icon.svg"
        cat.skill_id = "ovos.common_play"
        return cat

    def test_returns_media_entries_with_attribute_access(self):
        cat = self._make_catalog({
            "file://a.mp3": {"title": "Alpha", "play_count": 2},
            "file://b.mp3": {"title": "Beta", "play_count": 5},
        })
        entries = cat.liked_songs_playlist
        self.assertTrue(all(isinstance(e, MediaEntry) for e in entries))
        # attribute access must work on every entry (the playback path uses it)
        self.assertEqual([e.title for e in entries], ["Beta", "Alpha"])
        self.assertTrue(all(e.playback == PlaybackType.AUDIO for e in entries))
        self.assertTrue(all(e.media_type == MediaType.MUSIC for e in entries))

    def test_does_not_mutate_persisted_store(self):
        stored = {"file://a.mp3": {"title": "Alpha", "play_count": 1}}
        cat = self._make_catalog(stored)
        _ = cat.liked_songs_playlist
        # the raw store dict must not gain search-only keys
        self.assertEqual(stored["file://a.mp3"],
                         {"title": "Alpha", "play_count": 1})

    def test_search_song_branch_yields_serializable_dicts(self):
        cat = self._make_catalog(
            {"file://a.mp3": {"title": "Alpha", "play_count": 1}})

        def fake_voc_match(phrase):
            return {"song_name": "alpha"}

        with patch.object(cat, "ocp_voc_match", side_effect=fake_voc_match):
            results = list(cat.search_db("alpha", MediaType.MUSIC))

        self.assertEqual(len(results), 1)
        r = results[0]
        # yielded results are dicts (OCP search contract) but carry the same
        # data the MediaEntry path exposes as attributes
        self.assertEqual(r["uri"], "file://a.mp3")
        self.assertEqual(r["title"], "Alpha")
        self.assertEqual(r["playback"], PlaybackType.AUDIO)
        self.assertEqual(MediaEntry.from_dict(r).title, "Alpha")


if __name__ == "__main__":
    unittest.main()

"""Tests for KeywordRegistrar: the liked-songs keyword samples handed to
the local NER matcher, and the bus half that keeps the OCP pipeline
classifier informed when the optional "ner" extra is missing.
"""
import unittest
from unittest.mock import MagicMock

from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import MediaType

from ovos_media.catalog.keywords import (MOST_PLAYED_KEYWORDS,
                                         PLAYLIST_KEYWORDS,
                                         RECENTLY_PLAYED_KEYWORDS,
                                         KeywordRegistrar, normalize_title)

SKILL_ID = "ovos.common_play.favorites"


def _registrar(bus, ner_register=None, langs=("en-us",), cache_dir="/tmp"):
    return KeywordRegistrar(bus, SKILL_ID, list(langs), cache_dir,
                            ner_register=ner_register)


def _likes(titles):
    return MagicMock(**{"titles.return_value": list(titles)})


class TestNormalizeTitle(unittest.TestCase):
    def test_strips_decoration(self):
        self.assertEqual(normalize_title("Song (Live)"), "Song")
        self.assertEqual(normalize_title("Song [Remaster]"), "Song")
        self.assertEqual(normalize_title("Song {Demo}"), "Song")
        self.assertEqual(normalize_title("Song | Bonus"), "Song")
        self.assertEqual(normalize_title("Song - Remaster"), "Song")

    def test_leaves_a_plain_title_alone(self):
        self.assertEqual(normalize_title("Bohemian Rhapsody"),
                         "Bohemian Rhapsody")


class TestNerBackedRegistration(unittest.TestCase):
    def test_registers_normalized_titles_and_playlist_synonyms(self):
        ner = MagicMock()
        _registrar(FakeBus(), ner).register_liked_songs(
            _likes(["Alpha (Live)", "Beta"]))

        ner.assert_any_call(MediaType.MUSIC, "song_name", ["Alpha", "Beta"])
        ner.assert_any_call(MediaType.MUSIC, "playlist_name",
                            PLAYLIST_KEYWORDS)

    def test_nothing_is_emitted_on_the_bus_when_ner_is_available(self):
        bus = FakeBus()
        emitted = []
        bus.on("ovos.common_play.register_keyword",
               lambda m: emitted.append(m.data))

        _registrar(bus, MagicMock()).register_liked_songs(_likes(["Alpha"]))

        self.assertEqual(emitted, [])


class TestFallbackEmit(unittest.TestCase):
    """Without the "ner" extra, register_ocp_keyword raises ImportError
    before its own bus emit runs, so the classifier would never hear about
    the keywords. The bus half is replicated here instead."""

    def _emitted_without_ner(self, titles, ner_register=None):
        bus = FakeBus()
        emitted = []
        bus.on("ovos.common_play.register_keyword",
               lambda m: emitted.append(m.data))
        _registrar(bus, ner_register).register_liked_songs(_likes(titles))
        return emitted

    def test_both_labels_are_emitted(self):
        emitted = self._emitted_without_ner(["Alpha"])
        self.assertEqual({e["label"] for e in emitted},
                         {"song_name", "playlist_name"})

    def test_import_error_from_the_ner_path_falls_back(self):
        ner = MagicMock(side_effect=ImportError("no ahocorasick_ner"))
        emitted = self._emitted_without_ner(["Alpha"], ner)
        self.assertEqual({e["label"] for e in emitted},
                         {"song_name", "playlist_name"})

    def test_song_name_is_emitted_even_with_no_liked_songs(self):
        emitted = self._emitted_without_ner([])
        song_name = [e for e in emitted if e["label"] == "song_name"]
        self.assertEqual(len(song_name), 1)
        self.assertEqual(song_name[0]["samples"], [])
        self.assertEqual(set(song_name[0].keys()),
                         {"skill_id", "label", "samples", "media_type"})

    def test_payload_carries_the_skill_id_and_media_type(self):
        emitted = self._emitted_without_ner(["Alpha"])
        for data in emitted:
            self.assertEqual(data["skill_id"], SKILL_ID)
            self.assertEqual(data["media_type"], MediaType.MUSIC)

    def test_one_emit_per_native_language(self):
        bus = FakeBus()
        emitted = []
        bus.on("ovos.common_play.register_keyword",
               lambda m: emitted.append(m.data))

        _registrar(bus, langs=("en-us", "pt-pt")).register_liked_songs(
            _likes(["Alpha"]))

        self.assertEqual(len(emitted), 4)


class TestLargeSampleSets(unittest.TestCase):
    """20+ samples go through a CSV file instead of an inline payload —
    the shape ovos-workshop's own registration uses."""

    def test_samples_are_written_to_a_csv(self):
        import tempfile
        bus = FakeBus()
        emitted = []
        bus.on("ovos.common_play.register_keyword",
               lambda m: emitted.append(m.data))
        titles = [f"Song {i}" for i in range(25)]

        with tempfile.TemporaryDirectory() as tmp:
            _registrar(bus, cache_dir=tmp).register_liked_songs(_likes(titles))
            song_name = [e for e in emitted if e["label"] == "song_name"][0]
            self.assertNotIn("samples", song_name)
            with open(song_name["csv"]) as f:
                lines = f.read().splitlines()

        self.assertEqual(lines[0], "label,sample")
        self.assertEqual(len(lines), 26)
        self.assertTrue(all(line.startswith("song_name,") for line in lines[1:]))


class TestHistoryPlaylistRegistration(unittest.TestCase):
    """register_history_playlists registers only the static playlist-name
    synonyms - never per-title keywords (history churns too much and
    registration is append-only, see the comment at the call site)."""

    def test_ner_backed_registers_both_synonym_sets(self):
        ner = MagicMock()
        _registrar(FakeBus(), ner).register_history_playlists()

        ner.assert_any_call(MediaType.MUSIC, "playlist_name",
                            RECENTLY_PLAYED_KEYWORDS)
        ner.assert_any_call(MediaType.MUSIC, "playlist_name",
                            MOST_PLAYED_KEYWORDS)
        # never a song_name/per-title registration
        labels = {c.args[1] for c in ner.call_args_list}
        self.assertEqual(labels, {"playlist_name"})

    def test_nothing_is_emitted_on_the_bus_when_ner_is_available(self):
        bus = FakeBus()
        emitted = []
        bus.on("ovos.common_play.register_keyword",
              lambda m: emitted.append(m.data))

        _registrar(bus, MagicMock()).register_history_playlists()

        self.assertEqual(emitted, [])

    def test_fallback_emits_both_synonym_sets_on_the_bus(self):
        bus = FakeBus()
        emitted = []
        bus.on("ovos.common_play.register_keyword",
              lambda m: emitted.append(m.data))

        _registrar(bus).register_history_playlists()

        self.assertEqual(len(emitted), 2)
        samples = [set(e["samples"]) for e in emitted]
        self.assertIn(set(RECENTLY_PLAYED_KEYWORDS), samples)
        self.assertIn(set(MOST_PLAYED_KEYWORDS), samples)
        self.assertTrue(all(e["label"] == "playlist_name" for e in emitted))


if __name__ == "__main__":
    unittest.main()

"""Tests for MediaCatalog: the OCP skill roster, the featured-media
filter, the search playlist, and the dialog-notification seam the player
speaks through.
"""
import unittest
from unittest.mock import MagicMock

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import MediaEntry, MediaType

from ovos_media.catalog import LikedSongsStore, MediaCatalog


class _FakeStore(dict):
    """The JsonStorageXDG surface LikedSongsStore writes through, without
    touching disk."""

    def store(self):
        pass


def _likes(entries=None):
    return LikedSongsStore(_FakeStore(entries or {}))


def _announce(catalog, **data):
    catalog.handle_skill_announce(Message("ovos.common_play.announce", data))


class TestSkillAnnounceMediaTypesNormalization(unittest.TestCase):
    """D2: a skill announcing with the singular "media_type" key can send
    a bare scalar (eg. an int), which used to be stored as-is and blow up
    get_featured_skills()'s "in media_types" membership checks."""

    def test_singular_int_media_type_is_featured_and_does_not_raise(self):
        catalog = MediaCatalog(FakeBus(), _likes())
        _announce(catalog, skill_id="skill.a", skill_name="A",
                  featured_tracks=["t1"], media_type=int(MediaType.MUSIC))

        skills = catalog.get_featured_skills()  # must not raise

        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0]["skill_id"], "skill.a")

    def test_plural_list_media_types_still_works(self):
        catalog = MediaCatalog(FakeBus(), _likes())
        _announce(catalog, skill_id="skill.b", skill_name="B",
                  featured_tracks=["t1"], media_types=[MediaType.MUSIC])

        skills = catalog.get_featured_skills()

        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0]["skill_id"], "skill.b")

    def test_adult_singular_media_type_is_filtered_out(self):
        catalog = MediaCatalog(FakeBus(), _likes())
        _announce(catalog, skill_id="skill.c", skill_name="C",
                  featured_tracks=["t1"], media_type=int(MediaType.ADULT))

        skills = catalog.get_featured_skills()  # must not raise

        self.assertEqual(skills, [])

    def test_adult_skills_are_listed_when_asked_for(self):
        catalog = MediaCatalog(FakeBus(), _likes())
        _announce(catalog, skill_id="skill.c", skill_name="C",
                  featured_tracks=["t1"], media_type=int(MediaType.ADULT))

        self.assertEqual(len(catalog.get_featured_skills(adult=True)), 1)


class TestSkillRoster(unittest.TestCase):
    def test_announce_registers_the_skill_even_without_featured_tracks(self):
        catalog = MediaCatalog(FakeBus(), _likes())
        _announce(catalog, skill_id="skill.d", skill_name="D")

        self.assertIn("skill.d", catalog.ocp_skills)
        self.assertNotIn("skill.d", catalog.featured_skills)

    def test_detach_drops_the_skill_from_both_registries(self):
        catalog = MediaCatalog(FakeBus(), _likes())
        _announce(catalog, skill_id="skill.e", skill_name="E",
                  featured_tracks=["t1"], media_types=[MediaType.MUSIC])

        catalog.handle_ocp_skill_detach(
            Message("ovos.common_play.skills.detach", {"skill_id": "skill.e"}))

        self.assertNotIn("skill.e", catalog.ocp_skills)
        self.assertNotIn("skill.e", catalog.featured_skills)

    def test_detach_of_an_unknown_skill_is_a_noop(self):
        catalog = MediaCatalog(FakeBus(), _likes())
        catalog.handle_ocp_skill_detach(
            Message("ovos.common_play.skills.detach", {"skill_id": "nope"}))
        self.assertEqual(catalog.ocp_skills, {})

    def test_get_featured_skills_broadcasts_the_skills_get_request(self):
        bus = FakeBus()
        catalog = MediaCatalog(bus, _likes())
        seen = []
        bus.on("ovos.common_play.skills.get", lambda m: seen.append(m))

        catalog.get_featured_skills()

        self.assertEqual(len(seen), 1)


class TestSearchPlaylist(unittest.TestCase):
    def test_clear_empties_the_search_results(self):
        catalog = MediaCatalog(FakeBus(), _likes())
        catalog.search_playlist.add_entry(MediaEntry(uri="file://a.mp3",
                                                     title="A"))
        catalog.clear()
        self.assertEqual(len(catalog.search_playlist), 0)

    def test_replace_swaps_the_search_results(self):
        catalog = MediaCatalog(FakeBus(), _likes())
        catalog.search_playlist.add_entry(MediaEntry(uri="file://a.mp3",
                                                     title="A"))
        catalog.replace([MediaEntry(uri="file://b.mp3", title="B")])
        self.assertEqual([e.uri for e in catalog.search_playlist.entries],
                         ["file://b.mp3"])


class TestDialogNotifications(unittest.TestCase):
    """The catalog carries dialog requests from the player to whatever
    voice front-end is attached; it never speaks itself."""

    def test_listener_receives_dialog_and_data(self):
        catalog = MediaCatalog(FakeBus(), _likes())
        listener = MagicMock()
        catalog.add_dialog_listener(listener)

        catalog.notify_dialog("queue.finished", {"title": "X"})

        listener.assert_called_once_with("queue.finished", {"title": "X"})

    def test_listener_is_registered_once(self):
        catalog = MediaCatalog(FakeBus(), _likes())
        listener = MagicMock()
        catalog.add_dialog_listener(listener)
        catalog.add_dialog_listener(listener)

        catalog.notify_dialog("track.failed")

        self.assertEqual(listener.call_count, 1)

    def test_removed_listener_is_not_called(self):
        catalog = MediaCatalog(FakeBus(), _likes())
        listener = MagicMock()
        catalog.add_dialog_listener(listener)
        catalog.remove_dialog_listener(listener)

        catalog.notify_dialog("track.failed")

        listener.assert_not_called()

    def test_a_raising_listener_does_not_reach_the_caller(self):
        """The notify sites are playback paths — a front-end that blows up
        while speaking must never abort playback."""
        catalog = MediaCatalog(FakeBus(), _likes())
        catalog.add_dialog_listener(MagicMock(side_effect=RuntimeError("boom")))
        survivor = MagicMock()
        catalog.add_dialog_listener(survivor)

        catalog.notify_dialog("track.failed")  # must not raise

        survivor.assert_called_once()

    def test_shutdown_drops_every_listener(self):
        catalog = MediaCatalog(FakeBus(), _likes())
        listener = MagicMock()
        catalog.add_dialog_listener(listener)

        catalog.shutdown()
        catalog.notify_dialog("track.failed")

        listener.assert_not_called()


if __name__ == "__main__":
    unittest.main()

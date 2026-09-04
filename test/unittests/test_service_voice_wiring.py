"""The production wiring between the player, its catalog and the voice
front-end.

Every other MediaService test patches OCPVoiceSkill out, so nothing there
notices if the skill stops being handed the catalog to listen on, or stops
sharing the player's liked-songs store. This builds the real thing on a
FakeBus — a real MediaService, a real OCPMediaPlayer and a real
OCPVoiceSkill — and pins the two wires MediaService is responsible for.
"""
import unittest
from unittest.mock import patch

from ovos_utils.fakebus import FakeBus


class TestVoiceSkillWiring(unittest.TestCase):

    def setUp(self):
        from ovos_media.service import MediaService
        self.bus = FakeBus()
        self.service = MediaService(bus=self.bus)
        self.addCleanup(self.service.shutdown)

    def test_player_dialog_notification_reaches_the_skill(self):
        """The player announces a failed track by notifying its catalog;
        the skill listening on that catalog is what turns it into speech.
        Fails if MediaService stops handing the catalog to the skill."""
        with patch.object(self.service.voice_skill, "speak_dialog") as speak:
            self.service.ocp.handle_invalid_media()

        speak.assert_called_once_with("track.failed", None)

    def test_skill_speaks_on_the_bus_end_to_end(self):
        """The same path with nothing stubbed: a real dialog reaches a real
        'speak' message."""
        spoken = []
        self.bus.on("speak", lambda m: spoken.append(m.data["utterance"]))

        self.service.ocp.handle_invalid_media()

        self.assertEqual(len(spoken), 1)
        self.assertTrue(spoken[0])

    def test_player_and_skill_share_one_liked_songs_store(self):
        """A like written through the player must be visible to the search
        the skill answers - they have to be the same object, not two stores
        over the same file."""
        self.assertIs(self.service.voice_skill.likes,
                      self.service.ocp.media.likes)

    def test_a_like_written_by_the_player_is_searchable_by_the_skill(self):
        """The identity above, exercised: nothing is persisted, the store's
        write-through is stubbed."""
        likes = self.service.voice_skill.likes
        with patch.object(likes._store, "store"):
            self.service.ocp.media.likes.like("http://x.mp3", title="Xylophone")
            try:
                with patch.object(self.service.voice_skill, "ocp_voc_match",
                                  side_effect=lambda phrase: {"song_name": "xylophone"}):
                    results = list(
                        self.service.voice_skill.search_db("xylophone", None))
                self.assertEqual([r["title"] for r in results], ["Xylophone"])
            finally:
                self.service.ocp.media.likes.unlike("http://x.mp3")

    def test_the_skill_keeps_the_catalog_skill_id(self):
        """The OCP pipeline keys search results and keyword registrations
        off this id."""
        from ovos_utils.ocp import OCP_ID
        self.assertEqual(self.service.voice_skill.skill_id,
                         OCP_ID + ".favorites")


if __name__ == "__main__":
    unittest.main()

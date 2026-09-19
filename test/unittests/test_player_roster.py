"""Selection tests for the player roster.

Two questions are asked of the roster: which player starts the current track,
and which players a transport verb reaches. The second is not derived from the
first — PlaybackType.UNDEFINED means "nothing is loaded", so a stop or a pause
on it must reach every player that could still be holding audio.
"""
import unittest
from unittest.mock import MagicMock

from ovos_utils.ocp import PlaybackType

from ovos_media.player.adapters import PlayerAdapter
from ovos_media.player.roster import PlayerRoster


class _FakeAdapter(PlayerAdapter):
    def __init__(self, player_id, playable=True):
        super().__init__(player_id)
        self.playable = playable
        self.calls = []

    def can_play(self, uri):
        self.calls.append(("can_play", uri))
        return self.playable

    def play(self, uri):
        self.calls.append(("play", uri))

    def pause(self):
        self.calls.append(("pause",))

    def resume(self):
        self.calls.append(("resume",))

    def stop(self):
        self.calls.append(("stop",))

    def seek(self, milliseconds):
        self.calls.append(("seek", milliseconds))

    def position(self):
        return None

    def length(self):
        return None

    def lower_volume(self):
        self.calls.append(("lower_volume",))

    def restore_volume(self):
        self.calls.append(("restore_volume",))

    def deactivate(self):
        self.calls.append(("deactivate",))


def _roster(**playable):
    adapters = {i: _FakeAdapter(i, playable.get(i.replace(":", "_"), True))
                for i in ("opm:audio", "opm:video", "opm:web", "skill")}
    roster = PlayerRoster(adapters.values())
    return roster, adapters


class TestRosterLookup(unittest.TestCase):
    def setUp(self):
        self.roster, self.adapters = _roster()

    def test_lookup_by_id(self):
        self.assertIs(self.roster.get("opm:audio"), self.adapters["opm:audio"])
        self.assertIsNone(self.roster.get("opm:nothing"))

    def test_owner_per_playback_type(self):
        for ptype, player_id in ((PlaybackType.AUDIO, "opm:audio"),
                                 (PlaybackType.VIDEO, "opm:video"),
                                 (PlaybackType.WEBVIEW, "opm:web"),
                                 (PlaybackType.SKILL, "skill")):
            with self.subTest(ptype=ptype):
                self.assertIs(self.roster.owner(ptype), self.adapters[player_id])

    def test_unowned_playback_types_have_no_owner(self):
        self.assertIsNone(self.roster.owner(PlaybackType.MPRIS))
        self.assertIsNone(self.roster.owner(PlaybackType.UNDEFINED))


class TestVerbRouting(unittest.TestCase):
    def setUp(self):
        self.roster, _ = _roster()

    def _ids(self, verb, ptype):
        return [a.id for a in self.roster.route(verb, ptype)]

    def test_pause_routing(self):
        self.assertEqual(self._ids("pause", PlaybackType.AUDIO), ["opm:audio"])
        self.assertEqual(self._ids("pause", PlaybackType.VIDEO), ["opm:video"])
        self.assertEqual(self._ids("pause", PlaybackType.SKILL), ["skill"])
        self.assertEqual(self._ids("pause", PlaybackType.WEBVIEW), [])
        self.assertEqual(self._ids("pause", PlaybackType.UNDEFINED),
                         ["opm:audio", "opm:video", "skill"])

    def test_resume_routing(self):
        self.assertEqual(self._ids("resume", PlaybackType.AUDIO), ["opm:audio"])
        self.assertEqual(self._ids("resume", PlaybackType.VIDEO), ["opm:video"])
        self.assertEqual(self._ids("resume", PlaybackType.SKILL), ["skill"])
        self.assertEqual(self._ids("resume", PlaybackType.WEBVIEW), [])
        self.assertEqual(self._ids("resume", PlaybackType.UNDEFINED),
                         ["opm:audio", "skill"])

    def test_stop_reaches_every_player_when_nothing_is_loaded(self):
        self.assertEqual(self._ids("stop", PlaybackType.UNDEFINED),
                         ["opm:audio", "skill", "opm:video", "opm:web"])

    def test_stop_routing_per_type(self):
        self.assertEqual(self._ids("stop", PlaybackType.AUDIO), ["opm:audio"])
        self.assertEqual(self._ids("stop", PlaybackType.VIDEO), ["opm:video"])
        self.assertEqual(self._ids("stop", PlaybackType.WEBVIEW), ["opm:web"])
        self.assertEqual(self._ids("stop", PlaybackType.SKILL), ["skill"])

    def test_seek_reaches_nobody_where_seeking_is_unsupported(self):
        self.assertEqual(self._ids("seek", PlaybackType.AUDIO), ["opm:audio"])
        self.assertEqual(self._ids("seek", PlaybackType.UNDEFINED), ["opm:audio"])
        self.assertEqual(self._ids("seek", PlaybackType.VIDEO), ["opm:video"])
        self.assertEqual(self._ids("seek", PlaybackType.SKILL), [])
        self.assertEqual(self._ids("seek", PlaybackType.WEBVIEW), [])
        self.assertEqual(self._ids("seek", PlaybackType.MPRIS), [])

    def test_ducking_only_reaches_players_with_a_volume(self):
        self.assertEqual(self._ids("volume", PlaybackType.AUDIO), ["opm:audio"])
        self.assertEqual(self._ids("volume", PlaybackType.VIDEO), ["opm:video"])
        self.assertEqual(self._ids("volume", PlaybackType.SKILL), [])
        self.assertEqual(self._ids("volume", PlaybackType.UNDEFINED), [])

    def test_position_queries_only_read_the_audio_backend(self):
        self.assertEqual(self._ids("position", PlaybackType.AUDIO), ["opm:audio"])
        self.assertEqual(self._ids("position", PlaybackType.VIDEO), [])
        self.assertEqual(self._ids("position_offset", PlaybackType.UNDEFINED),
                         ["opm:audio"])
        self.assertEqual(self._ids("position_offset", PlaybackType.VIDEO), [])

    def test_an_unknown_verb_routes_nowhere(self):
        self.assertEqual(self._ids("teleport", PlaybackType.AUDIO), [])

    def test_a_missing_adapter_is_skipped_rather_than_raising(self):
        roster = PlayerRoster([_FakeAdapter("opm:audio")])
        self.assertEqual([a.id for a in roster.route("stop", PlaybackType.UNDEFINED)],
                         ["opm:audio"])


class TestSelection(unittest.TestCase):
    def setUp(self):
        self.roster, self.adapters = _roster()

    def test_audio_is_selected_without_asking_can_play(self):
        adapter, ptype = self.roster.select(PlaybackType.AUDIO, "file://a.mp3")
        self.assertIs(adapter, self.adapters["opm:audio"])
        self.assertEqual(ptype, PlaybackType.AUDIO)
        self.assertEqual(self.adapters["opm:audio"].calls, [])

    def test_skill_is_selected_for_skill_playback(self):
        adapter, ptype = self.roster.select(PlaybackType.SKILL, "file://a.mp3")
        self.assertIs(adapter, self.adapters["skill"])
        self.assertEqual(ptype, PlaybackType.SKILL)

    def test_video_with_a_claiming_backend_stays_video(self):
        adapter, ptype = self.roster.select(PlaybackType.VIDEO, "file://a.mp4")
        self.assertIs(adapter, self.adapters["opm:video"])
        self.assertEqual(ptype, PlaybackType.VIDEO)
        self.assertNotIn(("deactivate",), self.adapters["opm:video"].calls)

    def test_unowned_playback_type_is_rejected(self):
        with self.assertRaises(ValueError):
            self.roster.select(PlaybackType.MPRIS, "file://a.mp3")
        with self.assertRaises(ValueError):
            self.roster.select(PlaybackType.UNDEFINED, "file://a.mp3")

    def test_video_without_a_backend_is_demoted_to_audio(self):
        roster, adapters = _roster(opm_video=False)
        adapter, ptype = roster.select(PlaybackType.VIDEO, "file://a.mp4")
        self.assertIs(adapter, adapters["opm:audio"])
        self.assertEqual(ptype, PlaybackType.AUDIO)

    def test_demotion_stops_the_abandoned_backend(self):
        roster, adapters = _roster(opm_video=False)
        roster.select(PlaybackType.VIDEO, "file://a.mp4")
        self.assertIn(("deactivate",), adapters["opm:video"].calls)

    def test_webview_without_a_backend_is_demoted_to_audio(self):
        roster, adapters = _roster(opm_web=False)
        adapter, ptype = roster.select(PlaybackType.WEBVIEW, "file://a.html")
        self.assertIs(adapter, adapters["opm:audio"])
        self.assertEqual(ptype, PlaybackType.AUDIO)
        self.assertIn(("deactivate",), adapters["opm:web"].calls)

    def test_no_player_at_all_reports_the_demotion_and_no_adapter(self):
        roster, adapters = _roster(opm_video=False, opm_audio=False)
        adapter, ptype = roster.select(PlaybackType.VIDEO, "file://a.mp4")
        self.assertIsNone(adapter)
        self.assertEqual(ptype, PlaybackType.AUDIO)
        self.assertIn(("deactivate",), adapters["opm:video"].calls)


class TestDeactivateOthers(unittest.TestCase):
    def test_every_other_player_gives_up_its_track(self):
        roster, adapters = _roster()
        roster.deactivate_others(PlaybackType.AUDIO)
        self.assertEqual(adapters["opm:audio"].calls, [])
        for player_id in ("opm:video", "opm:web", "skill"):
            self.assertIn(("deactivate",), adapters[player_id].calls, player_id)

    def test_an_unowned_playback_type_deactivates_everyone(self):
        roster, adapters = _roster()
        roster.deactivate_others(PlaybackType.MPRIS)
        for adapter in adapters.values():
            self.assertIn(("deactivate",), adapter.calls, adapter.id)

    def test_selection_does_not_deactivate_on_its_own(self):
        # the player deactivates before validating the stream, so a stream
        # that fails validation still leaves no backend holding a track
        roster, adapters = _roster()
        roster.select(PlaybackType.AUDIO, "file://a.mp3")
        self.assertEqual(adapters["opm:video"].calls, [])


class TestRosterOverRealAdapters(unittest.TestCase):
    """The roster works over the adapters the player actually builds."""

    def test_opm_and_skill_adapters_route_together(self):
        from ovos_media.player.adapters import (OPMBackendAdapter,
                                                SkillPlayerAdapter)
        player = MagicMock()
        player.audio_service.can_play.return_value = True
        roster = PlayerRoster([
            OPMBackendAdapter("opm:audio", player, "audio_service"),
            OPMBackendAdapter("opm:video", player, "video_service"),
            OPMBackendAdapter("opm:web", player, "web_service"),
            SkillPlayerAdapter(player),
        ])
        self.assertEqual([a.id for a in roster.route("stop", PlaybackType.UNDEFINED)],
                         ["opm:audio", "skill", "opm:video", "opm:web"])
        adapter, ptype = roster.select(PlaybackType.AUDIO, "file://a.mp3")
        self.assertEqual(adapter.id, "opm:audio")
        self.assertEqual(ptype, PlaybackType.AUDIO)


if __name__ == "__main__":
    unittest.main()

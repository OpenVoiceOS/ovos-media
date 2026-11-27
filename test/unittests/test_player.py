"""Tests for OCPMediaPlayer preferred service resolution and NowPlaying."""
import unittest
from unittest.mock import MagicMock

from ovos_utils.ocp import PlayerState, LoopState, PlaybackType

from player_fixture import make_player




class TestPlayerStateTransitions(unittest.TestCase):
    """set_player_state must reject wrong types and emit a bus message."""

    def test_set_player_state_rejects_invalid_type(self):
        p = make_player()
        p.bus.emit = MagicMock()
        with self.assertRaises(TypeError):
            p.set_player_state("playing")

    def test_set_player_state_emits_bus_message_on_change(self):
        p = make_player()
        emitted = []
        p.bus.emit = lambda m: emitted.append(m)
        # state starts at STOPPED; changing to PLAYING should emit
        p.set_player_state(PlayerState.PLAYING)
        msg_types = [m.msg_type for m in emitted]
        self.assertIn("ovos.common_play.player.state", msg_types)

    def test_set_player_state_noop_when_same_state(self):
        p = make_player()
        p.bus.emit = MagicMock()
        # state already STOPPED — should be a no-op
        p.set_player_state(PlayerState.STOPPED)
        p.bus.emit.assert_not_called()


class TestResolvePreferredService(unittest.TestCase):
    """_resolve_preferred_service must return the matching backend or None."""

    def _make_backend(self, name, aliases=None):
        b = MagicMock()
        b.name = name
        b.aliases = aliases or []
        return b

    def test_returns_matching_backend_by_name(self):
        p = make_player()
        vlc = self._make_backend("vlc")
        mpv = self._make_backend("mpv")
        p.audio_service.services = [vlc, mpv]
        p.audio_service.get_preferred_players.return_value = ["vlc"]
        result = p._resolve_preferred_service(p.audio_service)
        self.assertEqual(result, vlc)

    def test_returns_matching_backend_by_alias(self):
        p = make_player()
        vlc = self._make_backend("vlc-plugin", aliases=["vlc"])
        p.audio_service.services = [vlc]
        p.audio_service.get_preferred_players.return_value = ["vlc"]
        result = p._resolve_preferred_service(p.audio_service)
        self.assertEqual(result, vlc)

    def test_returns_none_when_no_preference(self):
        p = make_player()
        p.audio_service.services = [self._make_backend("vlc")]
        p.audio_service.get_preferred_players.return_value = []
        p.ocp_config = {}
        p._live_config = False
        result = p._resolve_preferred_service(p.audio_service)
        self.assertIsNone(result)

    def test_returns_none_when_preferred_not_loaded(self):
        p = make_player()
        p.audio_service.services = [self._make_backend("mpv")]
        p.audio_service.get_preferred_players.return_value = ["vlc"]
        result = p._resolve_preferred_service(p.audio_service)
        self.assertIsNone(result)

    def test_falls_back_to_ocp_config_preferred(self):
        p = make_player()
        vlc = self._make_backend("vlc")
        p.audio_service.services = [vlc]
        p.audio_service.get_preferred_players.return_value = None
        p.ocp_config = {"preferred_audio_services": ["vlc"]}
        result = p._resolve_preferred_service(p.audio_service)
        self.assertEqual(result, vlc)


class TestPlayerProperties(unittest.TestCase):
    """active_skill, playback_type, tracks, can_prev, can_next."""

    def test_active_skill_getter(self):
        p = make_player()
        p.now_playing.skill_id = "my.skill"
        self.assertEqual(p.active_skill, "my.skill")

    def test_active_skill_setter(self):
        p = make_player()
        p.active_skill = "new.skill"
        self.assertEqual(p.now_playing.skill_id, "new.skill")

    def test_playback_type_getter(self):
        p = make_player(PlaybackType.VIDEO)
        self.assertEqual(p.playback_type, PlaybackType.VIDEO)

    def test_playback_type_setter(self):
        p = make_player()
        p.playback_type = PlaybackType.VIDEO
        self.assertEqual(p.now_playing.playback, PlaybackType.VIDEO)

    def test_tracks_returns_list(self):
        p = make_player()
        p.playlist.entries = []
        result = p.tracks
        self.assertIsInstance(result, list)

    def test_can_prev_false_when_first_track(self):
        p = make_player()
        p.now_playing.playback = PlaybackType.AUDIO
        p.playlist.is_first_track = True
        self.assertFalse(p.can_prev)

    def test_can_prev_true_for_mpris(self):
        p = make_player(PlaybackType.MPRIS)
        p.playlist.is_first_track = True
        self.assertTrue(p.can_prev)

    def test_can_next_true_with_shuffle(self):
        p = make_player()
        p.shuffle = True
        self.assertTrue(p.can_next)

    def test_can_next_true_with_loop(self):
        p = make_player()
        p.loop_state = LoopState.REPEAT
        self.assertTrue(p.can_next)

    def test_can_next_false_when_last_track_no_loop(self):
        p = make_player()
        p.loop_state = LoopState.NONE
        p.shuffle = False
        p.playlist.is_last_track = True
        p.media.search_playlist.is_last_track = True
        p.ocp_config = {"merge_search": False}
        self.assertFalse(p.can_next)

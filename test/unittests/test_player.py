"""Tests for OCPMediaPlayer preferred service resolution and NowPlaying."""
import unittest
from unittest.mock import MagicMock, patch

from ovos_utils.ocp import PlayerState, LoopState, MediaState, PlaybackType
from ovos_utils.fakebus import FakeBus


def _make_player():
    """Return a minimal OCPMediaPlayer with all bus/service deps mocked.

    OCPMediaPlayer extends OVOSAbstractApplication which has strict type
    checks on the `bus` property.  We use FakeBus to satisfy them, and
    patch out the super().__init__ call so no real bus connection is made.
    """
    from ovos_media.player import OCPMediaPlayer
    with patch("ovos_media.player.AudioService"), \
         patch("ovos_media.player.VideoService"), \
         patch("ovos_media.player.WebService"), \
         patch("ovos_media.player.MprisPlayerCtl"), \
         patch("ovos_media.player.OCPGUIInterface"), \
         patch("ovos_media.player.Configuration", return_value={"media": {}}), \
         patch("ovos_media.player.OVOSAbstractApplication.__init__", return_value=None):
        p = OCPMediaPlayer.__new__(OCPMediaPlayer)
        p.ocp_config = {}
        p.state = PlayerState.STOPPED
        p.loop_state = LoopState.NONE
        p.media_state = MediaState.NO_MEDIA
        p.shuffle = False
        p.track_history = {}
        p._paused_on_duck = False
        p.now_playing = MagicMock()
        p.playlist = MagicMock()
        p.media = MagicMock()
        p.audio_service = MagicMock()
        p.video_service = MagicMock()
        p.web_service = MagicMock()
        p.current = None
        p.mpris = None
        p._bus = FakeBus()
        p.gui = MagicMock()
    return p


class TestPlayerStateTransitions(unittest.TestCase):
    """set_player_state must reject wrong types and emit a bus message."""

    def test_set_player_state_rejects_invalid_type(self):
        p = _make_player()
        p._bus.emit = MagicMock()
        with self.assertRaises(TypeError):
            p.set_player_state("playing")

    def test_set_player_state_emits_bus_message_on_change(self):
        p = _make_player()
        emitted = []
        p._bus.emit = lambda m: emitted.append(m)
        # state starts at STOPPED; changing to PLAYING should emit
        p.set_player_state(PlayerState.PLAYING)
        msg_types = [m.msg_type for m in emitted]
        self.assertIn("ovos.common_play.player.state", msg_types)

    def test_set_player_state_noop_when_same_state(self):
        p = _make_player()
        p._bus.emit = MagicMock()
        # state already STOPPED — should be a no-op
        p.set_player_state(PlayerState.STOPPED)
        p._bus.emit.assert_not_called()


class TestResolvePreferredService(unittest.TestCase):
    """_resolve_preferred_service must return the matching backend or None."""

    def _make_backend(self, name, aliases=None):
        b = MagicMock()
        b.name = name
        b.aliases = aliases or []
        return b

    def test_returns_matching_backend_by_name(self):
        p = _make_player()
        vlc = self._make_backend("vlc")
        mpv = self._make_backend("mpv")
        p.audio_service.services = [vlc, mpv]
        p.audio_service.get_preferred_players.return_value = ["vlc"]
        result = p._resolve_preferred_service(p.audio_service)
        self.assertEqual(result, vlc)

    def test_returns_matching_backend_by_alias(self):
        p = _make_player()
        vlc = self._make_backend("vlc-plugin", aliases=["vlc"])
        p.audio_service.services = [vlc]
        p.audio_service.get_preferred_players.return_value = ["vlc"]
        result = p._resolve_preferred_service(p.audio_service)
        self.assertEqual(result, vlc)

    def test_returns_none_when_no_preference(self):
        p = _make_player()
        p.audio_service.services = [self._make_backend("vlc")]
        p.audio_service.get_preferred_players.return_value = []
        p.ocp_config = {}
        result = p._resolve_preferred_service(p.audio_service)
        self.assertIsNone(result)

    def test_returns_none_when_preferred_not_loaded(self):
        p = _make_player()
        p.audio_service.services = [self._make_backend("mpv")]
        p.audio_service.get_preferred_players.return_value = ["vlc"]
        result = p._resolve_preferred_service(p.audio_service)
        self.assertIsNone(result)

    def test_falls_back_to_ocp_config_preferred(self):
        p = _make_player()
        vlc = self._make_backend("vlc")
        p.audio_service.services = [vlc]
        p.audio_service.get_preferred_players.return_value = None
        p.ocp_config = {"preferred_audio_services": ["vlc"]}
        result = p._resolve_preferred_service(p.audio_service)
        self.assertEqual(result, vlc)


if __name__ == "__main__":
    unittest.main()

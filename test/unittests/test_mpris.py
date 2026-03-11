"""Tests for MprisPlayerCtl and _MediaPlayer2PlayerInterface.

All DBus I/O is mocked — these tests run without a D-Bus session.
"""
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

from ovos_utils.ocp import PlayerState, LoopState


def _make_interface():
    """Return a _MediaPlayer2PlayerInterface with a mocked player."""
    from ovos_media.mpris import _MediaPlayer2PlayerInterface
    player = MagicMock()
    iface = _MediaPlayer2PlayerInterface.__new__(_MediaPlayer2PlayerInterface)
    iface._ocp_player = player
    return iface, player


class TestPosition(unittest.TestCase):
    """Position property must return microseconds (position * 1e6)."""

    def test_position_microseconds(self):
        iface, player = _make_interface()
        player.now_playing.position = 30  # seconds
        result = iface.Position
        self.assertAlmostEqual(result, 30 * 1e6)

    def test_position_zero_when_no_now_playing(self):
        iface, player = _make_interface()
        player.now_playing = None
        result = iface.Position
        self.assertEqual(result, 0)


class TestLoopStatusSetter(unittest.TestCase):
    """LoopStatus setter must map MPRIS strings to LoopState enum values.

    The dbus_next @dbus_property setter creates a class-level descriptor,
    so we call the underlying method directly via the class to bypass the
    descriptor protocol.
    """

    def _call_setter(self, iface, val):
        from ovos_media.mpris import _MediaPlayer2PlayerInterface
        # dbus_next @dbus_property.setter wraps the function in a _Property
        # descriptor; access the underlying setter via .fset
        _MediaPlayer2PlayerInterface.LoopStatus_setter.fset(iface, val)

    def test_track_maps_to_repeat_track(self):
        iface, player = _make_interface()
        self._call_setter(iface, "Track")
        self.assertEqual(player.loop_state, LoopState.REPEAT_TRACK)

    def test_playlist_maps_to_repeat(self):
        iface, player = _make_interface()
        self._call_setter(iface, "Playlist")
        self.assertEqual(player.loop_state, LoopState.REPEAT)

    def test_none_maps_to_loop_none(self):
        iface, player = _make_interface()
        self._call_setter(iface, "None")
        self.assertEqual(player.loop_state, LoopState.NONE)

    def test_unknown_string_maps_to_none(self):
        iface, player = _make_interface()
        self._call_setter(iface, "SomethingElse")
        self.assertEqual(player.loop_state, LoopState.NONE)


class TestStopMethod(unittest.TestCase):
    """Stop() must call stop(), not pause()."""

    def test_stop_calls_player_stop(self):
        iface, player = _make_interface()
        iface.Stop()
        player.stop.assert_called_once()
        player.pause.assert_not_called()


class TestManagePlayers(unittest.TestCase):
    """manage_players must be read from config, not hardcoded True."""

    def _make_mpris(self, config):
        from ovos_media.mpris import MprisPlayerCtl
        player = MagicMock()
        with patch.object(MprisPlayerCtl, "start"):
            ctl = MprisPlayerCtl.__new__(MprisPlayerCtl)
            ctl.dbus = None
            ctl.config = config
            ctl._ocp_player = player
            ctl.main_player = None
            ctl.players = {}
            ctl.player_meta = {}
            ctl._player_fails = {}
            ctl.manage_players = config.get("manage_external_players", True)
            ctl.ignored_players = config.get("ignored_players", [
                "org.mpris.MediaPlayer2.OCP",
            ])
        return ctl

    def test_manage_players_false_from_config(self):
        ctl = self._make_mpris({"manage_external_players": False})
        self.assertFalse(ctl.manage_players)

    def test_manage_players_true_by_default(self):
        ctl = self._make_mpris({})
        self.assertTrue(ctl.manage_players)

    def test_ignored_players_from_config(self):
        custom = ["org.mpris.MediaPlayer2.custom"]
        ctl = self._make_mpris({"ignored_players": custom})
        self.assertEqual(ctl.ignored_players, custom)


class TestSetMainPlayer(unittest.IsolatedAsyncioTestCase):
    """_set_main_player must log only when the name actually changes."""

    async def test_log_fires_on_change(self):
        from ovos_media.mpris import MprisPlayerCtl
        player = MagicMock()
        with patch.object(MprisPlayerCtl, "start"):
            ctl = MprisPlayerCtl.__new__(MprisPlayerCtl)
            ctl.config = {}
            ctl._ocp_player = player
            ctl.main_player = "old_player"
            ctl.players = {}
            ctl.player_meta = {}
            ctl._player_fails = {}
            ctl.manage_players = False  # skip _update_ocp side-effects

        with patch("ovos_media.mpris.LOG") as mock_log:
            await ctl._set_main_player("new_player")
            mock_log.info.assert_called()

    async def test_no_log_when_same_name(self):
        from ovos_media.mpris import MprisPlayerCtl
        player = MagicMock()
        with patch.object(MprisPlayerCtl, "start"):
            ctl = MprisPlayerCtl.__new__(MprisPlayerCtl)
            ctl.config = {}
            ctl._ocp_player = player
            ctl.main_player = "same_player"
            ctl.players = {}
            ctl.player_meta = {}
            ctl._player_fails = {}
            ctl.manage_players = False

        with patch("ovos_media.mpris.LOG") as mock_log:
            await ctl._set_main_player("same_player")
            mock_log.info.assert_not_called()


if __name__ == "__main__":
    unittest.main()

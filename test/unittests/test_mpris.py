"""Tests for OcpMprisExporter and _MediaPlayer2PlayerInterface.

All DBus I/O is mocked — these tests run without a D-Bus session.
"""
import unittest
from unittest.mock import MagicMock, patch, AsyncMock

from ovos_utils.ocp import PlayerState, LoopState


def _make_interface():
    """Return a _MediaPlayer2PlayerInterface with a mocked player."""
    from ovos_media.mpris import _MediaPlayer2PlayerInterface
    player = MagicMock()
    iface = _MediaPlayer2PlayerInterface.__new__(_MediaPlayer2PlayerInterface)
    iface._ocp_player = player
    return iface, player


def _make_exporter(config=None):
    """Return an OcpMprisExporter with a mocked player (no thread started)."""
    from ovos_media.mpris import OcpMprisExporter
    player = MagicMock()
    config = config or {}
    with patch.object(OcpMprisExporter, "start"):
        ctl = OcpMprisExporter.__new__(OcpMprisExporter)
        ctl.dbus = None
        ctl.config = config
        ctl._ocp_player = player
        ctl.main_player = None
        ctl.players = {}
        ctl.player_meta = {}
        ctl._player_fails = {}
        ctl.manage_players = config.get("manage_external_players", False)
        ctl.ignored_players = config.get("ignored_players", [
            "org.mpris.MediaPlayer2.OCP",
            "org.mpris.MediaPlayer2.plasma-browser-integration",
        ])
    return ctl, player


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
    """LoopStatus setter must map MPRIS strings to LoopState enum values."""

    def _call_setter(self, iface, val):
        from ovos_media.mpris import _MediaPlayer2PlayerInterface
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
    """manage_players defaults to False and is driven by config."""

    def test_manage_players_false_by_default(self):
        ctl, _ = _make_exporter({})
        self.assertFalse(ctl.manage_players)

    def test_manage_players_false_from_config(self):
        ctl, _ = _make_exporter({"manage_external_players": False})
        self.assertFalse(ctl.manage_players)

    def test_manage_players_true_from_config(self):
        ctl, _ = _make_exporter({"manage_external_players": True})
        self.assertTrue(ctl.manage_players)

    def test_ignored_players_from_config(self):
        custom = ["org.mpris.MediaPlayer2.custom"]
        ctl, _ = _make_exporter({"ignored_players": custom})
        self.assertEqual(ctl.ignored_players, custom)


class TestBackwardCompatAlias(unittest.TestCase):
    """MprisPlayerCtl must remain as an alias for OcpMprisExporter."""

    def test_alias_is_same_class(self):
        from ovos_media.mpris import MprisPlayerCtl, OcpMprisExporter
        self.assertIs(MprisPlayerCtl, OcpMprisExporter)


class TestSetMainPlayer(unittest.IsolatedAsyncioTestCase):
    """_set_main_player must log only when the name actually changes."""

    async def test_log_fires_on_change(self):
        ctl, player = _make_exporter()
        ctl.main_player = "old_player"

        with patch("ovos_media.mpris.LOG") as mock_log:
            await ctl._set_main_player("new_player")
            mock_log.info.assert_called()

    async def test_no_log_when_same_name(self):
        ctl, player = _make_exporter()
        ctl.main_player = "same_player"

        with patch("ovos_media.mpris.LOG") as mock_log:
            await ctl._set_main_player("same_player")
            mock_log.info.assert_not_called()


class TestDbusGracefulDegradation(unittest.IsolatedAsyncioTestCase):
    """event_loop must warn and return gracefully when D-Bus is unavailable."""

    async def test_dbus_connection_failure_logs_warning_and_returns(self):
        from ovos_media.mpris import OcpMprisExporter
        ctl, player = _make_exporter()
        ctl.shutdown_event = MagicMock()
        ctl.shutdown_event.is_set.side_effect = [False, True]  # one iteration
        ctl.stop_event = MagicMock()
        ctl.stop_event.is_set.return_value = False
        ctl.pause_event = MagicMock()
        ctl.pause_event.is_set.return_value = False
        ctl.prev_event = MagicMock()
        ctl.prev_event.is_set.return_value = False
        ctl.next_event = MagicMock()
        ctl.next_event.is_set.return_value = False
        ctl.resume_event = MagicMock()
        ctl.resume_event.is_set.return_value = False
        ctl.shuffle_event = MagicMock()
        ctl.shuffle_event.is_set.return_value = False
        ctl.repeat_event = MagicMock()
        ctl.repeat_event.is_set.return_value = False

        with patch("ovos_media.mpris.DbusMessageBus") as mock_bus_cls, \
                patch("ovos_media.mpris.LOG") as mock_log:
            mock_bus_cls.return_value.connect = AsyncMock(
                side_effect=ConnectionError("no D-Bus"))
            await ctl.event_loop()
            mock_log.warning.assert_called_once()
            warning_msg = mock_log.warning.call_args[0][0]
            self.assertIn("MPRIS unavailable", warning_msg)


class TestScanPlayersGatedByManagePlayers(unittest.IsolatedAsyncioTestCase):
    """scan_players must not be called when manage_players is False."""

    async def test_no_scan_when_manage_players_false(self):
        ctl, player = _make_exporter({"manage_external_players": False})
        ctl.shutdown_event = MagicMock()
        # two iterations so we can verify scan_players never fires
        ctl.shutdown_event.is_set.side_effect = [False, False, True]
        ctl.stop_event = MagicMock()
        ctl.stop_event.is_set.return_value = False
        ctl.pause_event = MagicMock()
        ctl.pause_event.is_set.return_value = False
        ctl.prev_event = MagicMock()
        ctl.prev_event.is_set.return_value = False
        ctl.next_event = MagicMock()
        ctl.next_event.is_set.return_value = False
        ctl.resume_event = MagicMock()
        ctl.resume_event.is_set.return_value = False
        ctl.shuffle_event = MagicMock()
        ctl.shuffle_event.is_set.return_value = False
        ctl.repeat_event = MagicMock()
        ctl.repeat_event.is_set.return_value = False

        mock_dbus = AsyncMock()
        ctl.dbus = mock_dbus  # pre-connected, skip the connect branch

        with patch.object(ctl, "export_ocp", new=AsyncMock()), \
                patch.object(ctl, "scan_players", new=AsyncMock()) as mock_scan, \
                patch("ovos_media.mpris.sleep"):
            await ctl.event_loop()
            mock_scan.assert_not_called()


if __name__ == "__main__":
    unittest.main()

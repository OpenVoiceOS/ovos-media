# Copyright 2024, Mycroft AI Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Additional coverage tests for mpris.py to push coverage to 65%+.

Targets uncovered regions:
- OcpMprisExporter.__init__ (lines 74-105): covered via _make_exporter_real_init
- export_ocp (lines 113-116): async method
- _update_ocp (lines 121-181): state sync from player_meta
- handle_player_shuffle (187-191): manage_players=True path
- handle_player_loop_state (193-202): manage_players=True path
- handle_player_state (204-215): manage_players=True path
- handle_lost_player / handle_new_player: sync paths
- query_player (536-573): async, fails gracefully
- _player_interface property
- dbus_type property
"""
import asyncio
import unittest
from unittest.mock import MagicMock, patch, AsyncMock

from ovos_utils.ocp import PlayerState, LoopState, MediaState, PlaybackType


# ---------------------------------------------------------------------------
# Shared factory
# ---------------------------------------------------------------------------

def _make_exporter(config=None):
    """Return an OcpMprisExporter with a mocked player (no thread started)."""
    from ovos_media.mpris import OcpMprisExporter
    from threading import Event
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
        # Ensure all events are set as real Event objects
        for attr in ("shutdown_event", "stop_event", "pause_event",
                     "resume_event", "next_event", "prev_event",
                     "shuffle_event", "repeat_event"):
            setattr(ctl, attr, Event())
    return ctl, player


# ---------------------------------------------------------------------------
# OcpMprisExporter: dbus_type property
# ---------------------------------------------------------------------------

class TestDbusType(unittest.TestCase):
    """dbus_type returns SESSION by default and SYSTEM when configured."""

    def test_dbus_type_session_by_default(self):
        from dbus_next.constants import BusType
        ctl, _ = _make_exporter({})
        self.assertEqual(ctl.dbus_type, BusType.SESSION)

    def test_dbus_type_system_when_configured(self):
        from dbus_next.constants import BusType
        ctl, _ = _make_exporter({"dbus_type": "system"})
        self.assertEqual(ctl.dbus_type, BusType.SYSTEM)

    def test_dbus_type_session_case_insensitive(self):
        from dbus_next.constants import BusType
        ctl, _ = _make_exporter({"dbus_type": "SESSION"})
        self.assertEqual(ctl.dbus_type, BusType.SESSION)


# ---------------------------------------------------------------------------
# OcpMprisExporter: export_ocp (async)
# ---------------------------------------------------------------------------

class TestExportOcp(unittest.IsolatedAsyncioTestCase):
    """export_ocp exports the interfaces and requests the bus name."""

    async def test_export_ocp_requests_name(self):
        ctl, _ = _make_exporter()
        mock_dbus = MagicMock()
        mock_dbus.export = MagicMock()
        mock_dbus.request_name = AsyncMock()
        ctl.dbus = mock_dbus
        # Attach interface mocks
        ctl.mediaPlayer2Interface = MagicMock()
        ctl.mediaPlayer2PlayerInterface = MagicMock()
        await ctl.export_ocp()
        mock_dbus.request_name.assert_awaited_once_with(
            "org.mpris.MediaPlayer2.OCP"
        )

    async def test_export_ocp_exports_both_interfaces(self):
        ctl, _ = _make_exporter()
        mock_dbus = MagicMock()
        mock_dbus.export = MagicMock()
        mock_dbus.request_name = AsyncMock()
        ctl.dbus = mock_dbus
        ctl.mediaPlayer2Interface = MagicMock()
        ctl.mediaPlayer2PlayerInterface = MagicMock()
        await ctl.export_ocp()
        self.assertEqual(mock_dbus.export.call_count, 2)


# ---------------------------------------------------------------------------
# OcpMprisExporter: _update_ocp
# ---------------------------------------------------------------------------

class TestUpdateOcp(unittest.TestCase):
    """_update_ocp syncs player_meta state into the ocp player."""

    def _make_ctl_with_player_meta(self, state="Playing"):
        ctl, player = _make_exporter({"manage_external_players": True})
        ctl.main_player = "org.mpris.MediaPlayer2.vlc"
        ctl.player_meta = {
            "org.mpris.MediaPlayer2.vlc": {
                "state": state,
                "shuffle": False,
                "loop_state": 0,
                "title": "Song",
                "artist": "Artist",
                "image": "",
                "length": 180000000,
                "external_player": "org.mpris.MediaPlayer2.vlc",
            }
        }
        player.active_skill = "other.skill"
        return ctl, player

    def test_playing_state_sets_player_state_playing(self):
        ctl, player = self._make_ctl_with_player_meta("Playing")
        ctl._update_ocp()
        player.set_player_state.assert_called_with(PlayerState.PLAYING)

    def test_paused_state_sets_player_state_paused(self):
        ctl, player = self._make_ctl_with_player_meta("Paused")
        ctl._update_ocp()
        player.set_player_state.assert_called_with(PlayerState.PAUSED)

    def test_stopped_state_sets_player_state_stopped(self):
        ctl, player = self._make_ctl_with_player_meta("Stopped")
        ctl._update_ocp()
        player.set_player_state.assert_called_with(PlayerState.STOPPED)

    def test_vlc_gets_vlc_skill_icon(self):
        ctl, player = self._make_ctl_with_player_meta("Playing")
        ctl._update_ocp()
        call_kwargs = player.set_now_playing.call_args[0][0]
        self.assertIn("vlc.png", call_kwargs["skill_icon"])

    def test_spotify_gets_spotify_skill_icon(self):
        ctl, player = _make_exporter({"manage_external_players": True})
        ctl.main_player = "org.mpris.MediaPlayer2.spotify"
        ctl.player_meta = {
            "org.mpris.MediaPlayer2.spotify": {
                "state": "Playing",
                "shuffle": False,
                "loop_state": 0,
                "length": 0,
                "external_player": "org.mpris.MediaPlayer2.spotify",
            }
        }
        player.active_skill = "other"
        ctl._update_ocp()
        call_kwargs = player.set_now_playing.call_args[0][0]
        self.assertIn("spotify.png", call_kwargs["skill_icon"])

    def test_noop_when_stop_event_set(self):
        ctl, player = self._make_ctl_with_player_meta("Playing")
        ctl.stop_event = MagicMock()
        ctl.stop_event.is_set.return_value = True
        ctl._update_ocp()
        player.set_player_state.assert_not_called()

    def test_noop_when_manage_players_false(self):
        ctl, player = _make_exporter({"manage_external_players": False})
        ctl.main_player = "some.player"
        ctl.player_meta = {"some.player": {"state": "Playing"}}
        ctl.stop_event = MagicMock()
        ctl.stop_event.is_set.return_value = False
        ctl._update_ocp()
        player.set_player_state.assert_not_called()

    def test_loop_state_1_sets_repeat(self):
        ctl, player = self._make_ctl_with_player_meta("Playing")
        ctl.player_meta["org.mpris.MediaPlayer2.vlc"]["loop_state"] = 1
        ctl._update_ocp()
        self.assertEqual(
            ctl.player_meta["org.mpris.MediaPlayer2.vlc"]["loop_state"],
            LoopState.REPEAT
        )

    def test_loop_state_2_sets_repeat_track(self):
        ctl, player = self._make_ctl_with_player_meta("Playing")
        ctl.player_meta["org.mpris.MediaPlayer2.vlc"]["loop_state"] = 2
        ctl._update_ocp()
        self.assertEqual(
            ctl.player_meta["org.mpris.MediaPlayer2.vlc"]["loop_state"],
            LoopState.REPEAT_TRACK
        )


# ---------------------------------------------------------------------------
# OcpMprisExporter: handle_player_shuffle
# ---------------------------------------------------------------------------

class TestHandlePlayerShuffle(unittest.IsolatedAsyncioTestCase):
    """handle_player_shuffle updates ocp player when manage_players is True."""

    async def test_updates_shuffle_when_manage_players_true(self):
        ctl, player = _make_exporter({"manage_external_players": True})
        await ctl.handle_player_shuffle(True)
        self.assertTrue(player.shuffle)
        player._update_gui.assert_called_once()

    async def test_noop_shuffle_when_manage_players_false(self):
        ctl, player = _make_exporter({"manage_external_players": False})
        await ctl.handle_player_shuffle(True)
        # shuffle should not have been set on the player
        player._update_gui.assert_not_called()


# ---------------------------------------------------------------------------
# OcpMprisExporter: handle_player_loop_state
# ---------------------------------------------------------------------------

class TestHandlePlayerLoopState(unittest.IsolatedAsyncioTestCase):
    """handle_player_loop_state sets loop_state on ocp player."""

    async def test_state_1_sets_repeat(self):
        ctl, player = _make_exporter({"manage_external_players": True})
        await ctl.handle_player_loop_state(1)
        self.assertEqual(player.loop_state, LoopState.REPEAT)

    async def test_state_2_sets_repeat_track(self):
        ctl, player = _make_exporter({"manage_external_players": True})
        await ctl.handle_player_loop_state(2)
        self.assertEqual(player.loop_state, LoopState.REPEAT_TRACK)

    async def test_state_0_sets_none(self):
        ctl, player = _make_exporter({"manage_external_players": True})
        await ctl.handle_player_loop_state(0)
        self.assertEqual(player.loop_state, LoopState.NONE)

    async def test_noop_when_manage_players_false(self):
        ctl, player = _make_exporter({"manage_external_players": False})
        original = player.loop_state
        await ctl.handle_player_loop_state(1)
        # loop_state on the player mock was not touched
        player._update_gui.assert_not_called()


# ---------------------------------------------------------------------------
# OcpMprisExporter: handle_player_state
# ---------------------------------------------------------------------------

class TestHandlePlayerState(unittest.IsolatedAsyncioTestCase):
    """handle_player_state updates ocp player state when manage_players is True."""

    async def test_paused_state(self):
        ctl, player = _make_exporter({"manage_external_players": True})
        await ctl.handle_player_state("Paused")
        player.set_player_state.assert_called_once_with(PlayerState.PAUSED)

    async def test_playing_state_calls_takeover(self):
        ctl, player = _make_exporter({"manage_external_players": True})
        await ctl.handle_player_state("Playing")
        player.handle_MPRIS_takeover.assert_called_once()
        player.set_player_state.assert_called_once_with(PlayerState.PLAYING)

    async def test_stopped_state(self):
        ctl, player = _make_exporter({"manage_external_players": True})
        await ctl.handle_player_state("Stopped")
        player.set_player_state.assert_called_once_with(PlayerState.STOPPED)

    async def test_noop_when_manage_players_false(self):
        ctl, player = _make_exporter({"manage_external_players": False})
        await ctl.handle_player_state("Playing")
        player.set_player_state.assert_not_called()


# ---------------------------------------------------------------------------
# OcpMprisExporter: query_player (async)
# ---------------------------------------------------------------------------

class TestQueryPlayer(unittest.IsolatedAsyncioTestCase):
    """query_player fetches metadata and increments fail counter on error."""

    async def test_skips_when_fail_count_gte_3(self):
        ctl, player = _make_exporter()
        ctl._player_fails["vlc"] = 3
        ctl.players["vlc"] = MagicMock()
        # Should return early without calling get_interface
        await ctl.query_player("vlc")
        ctl.players["vlc"].get_interface.assert_not_called()

    async def test_returns_early_for_unknown_player(self):
        ctl, player = _make_exporter()
        # Should not raise; player not in self.players
        await ctl.query_player("nonexistent")

    async def test_increments_fail_counter_on_exception(self):
        ctl, player = _make_exporter()
        mock_player_proxy = MagicMock()
        mock_iface = AsyncMock()
        mock_iface.get_metadata = AsyncMock(side_effect=Exception("dbus error"))
        mock_player_proxy.get_interface = MagicMock(return_value=mock_iface)
        ctl.players["broken"] = mock_player_proxy
        await ctl.query_player("broken")
        self.assertGreater(ctl._player_fails.get("broken", 0), 0)

    async def test_successful_query_resets_fail_counter(self):
        ctl, player = _make_exporter()

        mock_meta = {"xesam:title": MagicMock(value="Song")}
        mock_iface = AsyncMock()
        mock_iface.get_metadata = AsyncMock(return_value=mock_meta)
        mock_iface.get_playback_status = AsyncMock(return_value="Playing")
        mock_iface.get_loop_status = AsyncMock(return_value="None")

        mock_player_proxy = MagicMock()
        mock_player_proxy.get_interface = MagicMock(return_value=mock_iface)

        ctl.players["vlc"] = mock_player_proxy
        ctl._player_fails["vlc"] = 2

        # update_player_meta is complex; stub it out
        ctl.update_player_meta = AsyncMock()

        await ctl.query_player("vlc")
        self.assertEqual(ctl._player_fails["vlc"], 0)


# ---------------------------------------------------------------------------
# OcpMprisExporter: handle_sync_player
# ---------------------------------------------------------------------------

class TestHandleSyncPlayer(unittest.IsolatedAsyncioTestCase):
    """handle_sync_player calls _set_main_player when state==Playing."""

    async def test_sets_main_player_when_playing(self):
        ctl, player = _make_exporter()
        ctl._update_ocp = MagicMock()
        with patch.object(ctl, "_set_main_player", new=AsyncMock()) as mock_set:
            await ctl.handle_sync_player({
                "state": "Playing",
                "external_player": "org.mpris.MediaPlayer2.vlc"
            })
            mock_set.assert_awaited_once_with("org.mpris.MediaPlayer2.vlc")

    async def test_calls_update_ocp_when_not_playing(self):
        ctl, player = _make_exporter()
        ctl.main_player = "org.mpris.MediaPlayer2.vlc"
        ctl._update_ocp = MagicMock()
        await ctl.handle_sync_player({
            "state": "Paused",
            "external_player": "org.mpris.MediaPlayer2.vlc"
        })
        ctl._update_ocp.assert_called_once()


# ---------------------------------------------------------------------------
# OcpMprisExporter: update_props
# ---------------------------------------------------------------------------

class TestUpdateProps(unittest.TestCase):
    """update_props delegates to mediaPlayer2PlayerInterface."""

    def test_delegates_to_interface(self):
        ctl, _ = _make_exporter()
        mock_iface = MagicMock()
        ctl.mediaPlayer2PlayerInterface = mock_iface
        ctl.update_props({"PlaybackStatus": "Paused"})
        mock_iface.emit_properties_changed.assert_called_once_with(
            {"PlaybackStatus": "Paused"}
        )


# ---------------------------------------------------------------------------
# OcpMprisExporter: OcpMprisExporter.__init__ via real init
# ---------------------------------------------------------------------------

class TestOcpMprisExporterRealInit(unittest.TestCase):
    """Test that __init__ sets up expected attributes."""

    def _make_real_ctl(self, config=None):
        """Create a real OcpMprisExporter instance with start() and event loop patched."""
        import asyncio
        from ovos_media.mpris import OcpMprisExporter
        player = MagicMock()
        mock_loop = MagicMock()
        with patch.object(OcpMprisExporter, "start"), \
             patch("ovos_media.mpris._MediaPlayer2Interface"), \
             patch("ovos_media.mpris._MediaPlayer2PlayerInterface"), \
             patch("asyncio.get_event_loop", return_value=mock_loop):
            ctl = OcpMprisExporter(player, config=config or {}, daemonic=False)
        return ctl, player

    def test_init_creates_events(self):
        """All threading Events are created in __init__."""
        ctl, _ = self._make_real_ctl()
        from threading import Event
        for attr in ("shutdown_event", "stop_event", "pause_event",
                     "resume_event", "next_event", "prev_event",
                     "shuffle_event", "repeat_event"):
            self.assertIsInstance(getattr(ctl, attr), Event,
                                  f"{attr} should be an Event")

    def test_init_stores_player_reference(self):
        ctl, player = self._make_real_ctl()
        self.assertIs(ctl._ocp_player, player)

    def test_init_default_manage_players_false(self):
        ctl, _ = self._make_real_ctl()
        self.assertFalse(ctl.manage_players)

    def test_init_manage_players_true_from_config(self):
        ctl, _ = self._make_real_ctl(config={"manage_external_players": True})
        self.assertTrue(ctl.manage_players)


# ---------------------------------------------------------------------------
# OcpMprisExporter: handle_lost_player / handle_new_player edge cases
# ---------------------------------------------------------------------------

class TestHandleLostPlayerEdgeCases(unittest.IsolatedAsyncioTestCase):
    """handle_lost_player is idempotent."""

    async def test_removes_player_meta_if_present(self):
        ctl, _ = _make_exporter()
        ctl.player_meta["vlc"] = {"state": "Playing"}
        ctl.players["vlc"] = MagicMock()
        await ctl.handle_lost_player("vlc")
        self.assertNotIn("vlc", ctl.player_meta)
        self.assertNotIn("vlc", ctl.players)

    async def test_handles_missing_player_gracefully(self):
        ctl, _ = _make_exporter()
        # Should not raise
        await ctl.handle_lost_player("does_not_exist")


class TestHandleNewPlayerEdgeCases(unittest.IsolatedAsyncioTestCase):
    """handle_new_player logs for unknown players only."""

    async def test_logs_for_unknown_player(self):
        ctl, _ = _make_exporter()
        with patch("ovos_media.mpris.LOG") as mock_log:
            await ctl.handle_new_player({"name": "org.mpris.MediaPlayer2.mpv"})
            mock_log.info.assert_called_once()

    async def test_suppresses_log_for_known_failed(self):
        ctl, _ = _make_exporter()
        ctl._player_fails["org.mpris.MediaPlayer2.broken"] = 3
        with patch("ovos_media.mpris.LOG") as mock_log:
            await ctl.handle_new_player(
                {"name": "org.mpris.MediaPlayer2.broken"})
            mock_log.info.assert_not_called()


# ---------------------------------------------------------------------------
# OcpMprisExporter: event_loop — manage_players=True path triggers scan_players
# ---------------------------------------------------------------------------

class TestEventLoopManagePlayersPath(unittest.IsolatedAsyncioTestCase):
    """event_loop calls scan_players when manage_players is True."""

    async def test_scan_players_called_when_manage_players_true(self):
        ctl, player = _make_exporter({"manage_external_players": True})
        ctl.shutdown_event = MagicMock()
        # two iterations then shutdown
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
        ctl.dbus = mock_dbus  # pre-connected

        with patch.object(ctl, "export_ocp", new=AsyncMock()), \
             patch.object(ctl, "scan_players", new=AsyncMock()) as mock_scan, \
             patch.object(ctl, "query_player", new=AsyncMock()), \
             patch("ovos_media.mpris.sleep"):
            await ctl.event_loop()
            mock_scan.assert_called()


# ---------------------------------------------------------------------------
# OcpMprisExporter: internal player control methods (_play_prev, _play_next,
# _pause_player, _resume_player, _stop_player, _shuffle_enable/disable,
# _repeat_enable/disable/track_enable, _stop_all, _pause_all)
# ---------------------------------------------------------------------------

class TestInternalPlayerControl(unittest.IsolatedAsyncioTestCase):
    """Direct tests for the async player control helpers."""

    def _make_player_proxy(self, state="Playing"):
        """Return a fake player proxy with an async interface."""
        proxy = MagicMock()
        iface = AsyncMock()
        iface.call_previous = AsyncMock()
        iface.call_next = AsyncMock()
        iface.call_pause = AsyncMock()
        iface.call_play = AsyncMock()
        iface.call_stop = AsyncMock()
        iface.set_shuffle = AsyncMock()
        iface.set_loop_status = AsyncMock()
        proxy.get_interface = MagicMock(return_value=iface)
        return proxy, iface

    def _make_ctl_with_player(self, name="vlc", state="Playing"):
        ctl, ocp_player = _make_exporter()
        proxy, iface = self._make_player_proxy(state)
        ctl.players[name] = proxy
        ctl.player_meta[name] = {"state": state}
        return ctl, iface, name

    async def test_play_prev_calls_call_previous_when_playing(self):
        ctl, iface, name = self._make_ctl_with_player(state="Playing")
        await ctl._play_prev(name)
        iface.call_previous.assert_awaited_once()

    async def test_play_prev_noop_when_not_playing(self):
        ctl, iface, name = self._make_ctl_with_player(state="Paused")
        await ctl._play_prev(name)
        iface.call_previous.assert_not_awaited()

    async def test_play_prev_returns_early_for_unknown_player(self):
        ctl, _ = _make_exporter()
        # Should not raise
        await ctl._play_prev("nonexistent")

    async def test_play_next_calls_call_next_when_playing(self):
        ctl, iface, name = self._make_ctl_with_player(state="Playing")
        await ctl._play_next(name)
        iface.call_next.assert_awaited_once()

    async def test_play_next_noop_when_not_playing(self):
        ctl, iface, name = self._make_ctl_with_player(state="Stopped")
        await ctl._play_next(name)
        iface.call_next.assert_not_awaited()

    async def test_play_next_returns_early_for_unknown_player(self):
        ctl, _ = _make_exporter()
        await ctl._play_next("nonexistent")

    async def test_pause_player_pauses_when_playing(self):
        ctl, iface, name = self._make_ctl_with_player(state="Playing")
        await ctl._pause_player(name)
        iface.call_pause.assert_awaited_once()

    async def test_pause_player_noop_when_not_playing(self):
        ctl, iface, name = self._make_ctl_with_player(state="Paused")
        await ctl._pause_player(name)
        iface.call_pause.assert_not_awaited()

    async def test_pause_player_returns_early_for_unknown(self):
        ctl, _ = _make_exporter()
        await ctl._pause_player("nonexistent")

    async def test_resume_player_plays_when_not_playing(self):
        ctl, iface, name = self._make_ctl_with_player(state="Paused")
        await ctl._resume_player(name)
        iface.call_play.assert_awaited_once()

    async def test_resume_player_noop_when_already_playing(self):
        ctl, iface, name = self._make_ctl_with_player(state="Playing")
        await ctl._resume_player(name)
        iface.call_play.assert_not_awaited()

    async def test_resume_player_returns_early_for_unknown(self):
        ctl, _ = _make_exporter()
        await ctl._resume_player("nonexistent")

    async def test_stop_player_stops_when_playing(self):
        ctl, iface, name = self._make_ctl_with_player(state="Playing")
        await ctl._stop_player(name)
        iface.call_stop.assert_awaited_once()

    async def test_stop_player_sets_meta_to_stopped(self):
        ctl, iface, name = self._make_ctl_with_player(state="Playing")
        await ctl._stop_player(name)
        self.assertEqual(ctl.player_meta[name]["state"], "Stopped")

    async def test_stop_player_clears_main_player_when_it_is_the_main(self):
        ctl, iface, name = self._make_ctl_with_player(state="Playing")
        ctl.main_player = name
        await ctl._stop_player(name)
        self.assertIsNone(ctl.main_player)

    async def test_stop_player_returns_early_for_unknown(self):
        ctl, _ = _make_exporter()
        # Should not raise
        await ctl._stop_player("nonexistent")

    async def test_shuffle_enable_calls_set_shuffle_true(self):
        ctl, iface, name = self._make_ctl_with_player()
        await ctl._shuffle_enable(name)
        iface.set_shuffle.assert_awaited_once_with(True)

    async def test_shuffle_disable_calls_set_shuffle_false(self):
        ctl, iface, name = self._make_ctl_with_player()
        await ctl._shuffle_disable(name)
        iface.set_shuffle.assert_awaited_once_with(False)

    async def test_repeat_enable_calls_set_loop_playlist(self):
        ctl, iface, name = self._make_ctl_with_player()
        await ctl._repeat_enable(name)
        iface.set_loop_status.assert_awaited_once_with("Playlist")

    async def test_repeat_disable_calls_set_loop_none(self):
        ctl, iface, name = self._make_ctl_with_player()
        await ctl._repeat_disable(name)
        iface.set_loop_status.assert_awaited_once_with("None")

    async def test_repeat_track_enable_calls_set_loop_track(self):
        ctl, iface, name = self._make_ctl_with_player()
        await ctl._repeat_track_enable(name)
        iface.set_loop_status.assert_awaited_once_with("Track")

    async def test_stop_all_stops_all_players(self):
        ctl, ocp_player = _make_exporter()
        for name in ("vlc", "spotify"):
            proxy, iface = MagicMock(), AsyncMock()
            proxy.get_interface = MagicMock(return_value=iface)
            iface.call_stop = AsyncMock()
            ctl.players[name] = proxy
            ctl.player_meta[name] = {"state": "Playing"}
        with patch.object(ctl, "_stop_player", new=AsyncMock()) as mock_stop:
            await ctl._stop_all()
            self.assertEqual(mock_stop.await_count, 2)

    async def test_pause_all_pauses_all_players(self):
        ctl, ocp_player = _make_exporter()
        for name in ("vlc", "spotify"):
            proxy, iface = MagicMock(), AsyncMock()
            proxy.get_interface = MagicMock(return_value=iface)
            ctl.players[name] = proxy
            ctl.player_meta[name] = {"state": "Playing"}
        with patch.object(ctl, "_pause_player", new=AsyncMock()) as mock_pause:
            await ctl._pause_all()
            self.assertEqual(mock_pause.await_count, 2)


# ---------------------------------------------------------------------------
# OcpMprisExporter: error-handling retry paths
# ---------------------------------------------------------------------------

class TestInternalPlayerControlErrors(unittest.IsolatedAsyncioTestCase):
    """When interface calls raise, the helpers log a warning and give up."""

    def _make_failing_proxy(self):
        proxy = MagicMock()
        iface = AsyncMock()
        iface.call_previous = AsyncMock(side_effect=Exception("dbus error"))
        iface.call_next = AsyncMock(side_effect=Exception("dbus error"))
        iface.call_pause = AsyncMock(side_effect=Exception("dbus error"))
        iface.call_play = AsyncMock(side_effect=Exception("dbus error"))
        iface.set_shuffle = AsyncMock(side_effect=Exception("dbus error"))
        iface.set_loop_status = AsyncMock(side_effect=Exception("dbus error"))
        proxy.get_interface = MagicMock(return_value=iface)
        return proxy

    async def test_play_prev_logs_warning_after_failure(self):
        ctl, _ = _make_exporter()
        proxy = self._make_failing_proxy()
        ctl.players["vlc"] = proxy
        ctl.player_meta["vlc"] = {"state": "Playing"}
        with patch("ovos_media.mpris.LOG") as mock_log:
            await ctl._play_prev("vlc", max_tries=1)
            mock_log.warning.assert_called()

    async def test_play_next_logs_warning_after_failure(self):
        ctl, _ = _make_exporter()
        proxy = self._make_failing_proxy()
        ctl.players["vlc"] = proxy
        ctl.player_meta["vlc"] = {"state": "Playing"}
        with patch("ovos_media.mpris.LOG") as mock_log:
            await ctl._play_next("vlc", max_tries=1)
            mock_log.warning.assert_called()

    async def test_shuffle_enable_logs_warning_after_failure(self):
        ctl, _ = _make_exporter()
        proxy = self._make_failing_proxy()
        ctl.players["vlc"] = proxy
        ctl.player_meta["vlc"] = {"state": "Playing"}
        with patch("ovos_media.mpris.LOG") as mock_log:
            await ctl._shuffle_enable("vlc", max_tries=1)
            mock_log.warning.assert_called()

    async def test_repeat_enable_logs_warning_after_failure(self):
        ctl, _ = _make_exporter()
        proxy = self._make_failing_proxy()
        ctl.players["vlc"] = proxy
        ctl.player_meta["vlc"] = {"state": "Playing"}
        with patch("ovos_media.mpris.LOG") as mock_log:
            await ctl._repeat_enable("vlc", max_tries=1)
            mock_log.warning.assert_called()


if __name__ == "__main__":
    unittest.main()

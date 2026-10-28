"""Tests for Role B — awareness of the other MPRIS players on the machine.

Covers the external-player manager (discovery, metadata, takeover policy, the
per-player control coroutines and their retry paths), the roster membership the
external players get, and the facade the virtual player holds.

All DBus I/O is mocked; these run without a D-Bus session.
"""
import asyncio
import unittest
from unittest.mock import MagicMock, patch, AsyncMock

from ovos_utils.ocp import PlayerState, LoopState, PlaybackType


def _make_manager(config=None):
    """Return an ExternalPlayerManager with a mocked player and loop."""
    from ovos_media.mpris.manager import ExternalPlayerManager
    player = MagicMock()
    player.dispatcher = None  # commands run inline, as for any stub player
    mgr = ExternalPlayerManager(player, MagicMock(), config=config or {})
    return mgr, player


def _make_facade(config=None):
    """Return an OcpMprisExporter whose loop and manager are mocked."""
    from ovos_media.mpris import OcpMprisExporter
    from ovos_media.mpris.manager import ExternalPlayerManager
    ctl = OcpMprisExporter.__new__(OcpMprisExporter)
    ctl.config = config or {}
    ctl.loop = MagicMock()
    ctl.exporter = MagicMock()
    ctl.manager = ExternalPlayerManager(MagicMock(), ctl.loop,
                                        config=ctl.config)
    return ctl


def _posted(ctl):
    """The coroutine the facade posted onto the loop, closed after reading."""
    ctl.loop.call_async.assert_called_once()
    coro = ctl.loop.call_async.call_args[0][0]
    name = coro.__qualname__
    coro.close()
    return name


class TestManagePlayers(unittest.TestCase):
    """manage_players defaults to False and is driven by config."""

    def test_manage_players_false_by_default(self):
        mgr, _ = _make_manager({})
        self.assertFalse(mgr.manage_players)

    def test_manage_players_false_from_config(self):
        mgr, _ = _make_manager({"manage_external_players": False})
        self.assertFalse(mgr.manage_players)

    def test_manage_players_true_from_config(self):
        mgr, _ = _make_manager({"manage_external_players": True})
        self.assertTrue(mgr.manage_players)

    def test_ignored_players_from_config(self):
        custom = ["org.mpris.MediaPlayer2.custom"]
        mgr, _ = _make_manager({"ignored_players": custom})
        self.assertEqual(mgr.ignored_players, custom)

    def test_config_none_does_not_crash(self):
        from ovos_media.mpris.manager import ExternalPlayerManager
        mgr = ExternalPlayerManager(MagicMock(), MagicMock(), config=None,
                                    manage_players=True)
        self.assertTrue(mgr.manage_players)


class TestUpdateOcp(unittest.TestCase):
    """_update_ocp syncs player_meta state into the ocp player."""

    def _make_mgr_with_player_meta(self, state="Playing"):
        mgr, player = _make_manager({"manage_external_players": True})
        mgr.main_player = "org.mpris.MediaPlayer2.vlc"
        mgr.player_meta = {
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
        return mgr, player

    def test_playing_state_sets_player_state_playing(self):
        mgr, player = self._make_mgr_with_player_meta("Playing")
        mgr._update_ocp()
        player.set_player_state.assert_called_with(PlayerState.PLAYING)

    def test_paused_state_sets_player_state_paused(self):
        mgr, player = self._make_mgr_with_player_meta("Paused")
        mgr._update_ocp()
        player.set_player_state.assert_called_with(PlayerState.PAUSED)

    def test_stopped_state_sets_player_state_stopped(self):
        mgr, player = self._make_mgr_with_player_meta("Stopped")
        mgr._update_ocp()
        player.set_player_state.assert_called_with(PlayerState.STOPPED)

    def test_vlc_gets_vlc_skill_icon(self):
        mgr, player = self._make_mgr_with_player_meta("Playing")
        mgr._update_ocp()
        call_kwargs = player.set_now_playing.call_args[0][0]
        self.assertIn("vlc.png", call_kwargs["skill_icon"])

    def test_spotify_gets_spotify_skill_icon(self):
        mgr, player = _make_manager({"manage_external_players": True})
        mgr.main_player = "org.mpris.MediaPlayer2.spotify"
        mgr.player_meta = {
            "org.mpris.MediaPlayer2.spotify": {
                "state": "Playing",
                "shuffle": False,
                "loop_state": 0,
                "length": 0,
                "external_player": "org.mpris.MediaPlayer2.spotify",
            }
        }
        player.active_skill = "other"
        mgr._update_ocp()
        call_kwargs = player.set_now_playing.call_args[0][0]
        self.assertIn("spotify.png", call_kwargs["skill_icon"])

    def test_icons_resolve_to_files_that_exist(self):
        import os
        from ovos_media.mpris.manager import ExternalPlayerManager
        for name in ("org.mpris.MediaPlayer2.vlc",
                     "org.mpris.MediaPlayer2.spotify",
                     "org.mpris.MediaPlayer2.firefox.instance1",
                     "org.mpris.MediaPlayer2.chromium.instance2",
                     "org.mpris.MediaPlayer2.mpv",
                     "org.mpris.MediaPlayer2.audacious",
                     "org.mpris.MediaPlayer2.whatever"):
            icon = ExternalPlayerManager._icon_for(name)
            self.assertTrue(os.path.isfile(icon), icon)

    def test_noop_when_stop_event_set(self):
        mgr, player = self._make_mgr_with_player_meta("Playing")
        mgr.stop_event.set()
        mgr._update_ocp()
        player.set_player_state.assert_not_called()

    def test_noop_when_manage_players_false(self):
        mgr, player = _make_manager({"manage_external_players": False})
        mgr.main_player = "some.player"
        mgr.player_meta = {"some.player": {"state": "Playing"}}
        mgr._update_ocp()
        player.set_player_state.assert_not_called()

    def test_loop_state_1_sets_repeat(self):
        mgr, player = self._make_mgr_with_player_meta("Playing")
        mgr.player_meta["org.mpris.MediaPlayer2.vlc"]["loop_state"] = 1
        mgr._update_ocp()
        self.assertEqual(
            mgr.player_meta["org.mpris.MediaPlayer2.vlc"]["loop_state"],
            LoopState.REPEAT)

    def test_loop_state_2_sets_repeat_track(self):
        mgr, player = self._make_mgr_with_player_meta("Playing")
        mgr.player_meta["org.mpris.MediaPlayer2.vlc"]["loop_state"] = 2
        mgr._update_ocp()
        self.assertEqual(
            mgr.player_meta["org.mpris.MediaPlayer2.vlc"]["loop_state"],
            LoopState.REPEAT_TRACK)


class TestHandlePlayerShuffle(unittest.IsolatedAsyncioTestCase):
    """handle_player_shuffle updates ocp player when manage_players is True."""

    async def test_updates_shuffle_when_manage_players_true(self):
        mgr, player = _make_manager({"manage_external_players": True})
        await mgr.handle_player_shuffle(True)
        self.assertTrue(player.shuffle)

    async def test_noop_shuffle_when_manage_players_false(self):
        mgr, player = _make_manager({"manage_external_players": False})
        original = player.shuffle
        await mgr.handle_player_shuffle(True)
        self.assertEqual(player.shuffle, original)


class TestHandlePlayerLoopState(unittest.IsolatedAsyncioTestCase):
    """handle_player_loop_state sets loop_state on ocp player."""

    async def test_state_1_sets_repeat(self):
        mgr, player = _make_manager({"manage_external_players": True})
        await mgr.handle_player_loop_state(1)
        self.assertEqual(player.loop_state, LoopState.REPEAT)

    async def test_state_2_sets_repeat_track(self):
        mgr, player = _make_manager({"manage_external_players": True})
        await mgr.handle_player_loop_state(2)
        self.assertEqual(player.loop_state, LoopState.REPEAT_TRACK)

    async def test_state_0_sets_none(self):
        mgr, player = _make_manager({"manage_external_players": True})
        await mgr.handle_player_loop_state(0)
        self.assertEqual(player.loop_state, LoopState.NONE)

    async def test_noop_when_manage_players_false(self):
        mgr, player = _make_manager({"manage_external_players": False})
        original = player.loop_state
        await mgr.handle_player_loop_state(1)
        self.assertEqual(player.loop_state, original)


class TestHandlePlayerState(unittest.IsolatedAsyncioTestCase):
    """handle_player_state updates ocp player state when manage_players is True."""

    async def test_paused_state(self):
        mgr, player = _make_manager({"manage_external_players": True})
        await mgr.handle_player_state("Paused")
        player.set_player_state.assert_called_once_with(PlayerState.PAUSED)

    async def test_playing_state_calls_takeover(self):
        mgr, player = _make_manager({"manage_external_players": True})
        await mgr.handle_player_state("Playing")
        player.handle_MPRIS_takeover.assert_called_once()
        player.set_player_state.assert_called_once_with(PlayerState.PLAYING)

    async def test_playing_state_sets_playback_type_mpris(self):
        mgr, player = _make_manager({"manage_external_players": True})
        await mgr.handle_player_state("Playing")
        self.assertEqual(player.playback_type, PlaybackType.MPRIS)

    async def test_stopped_state(self):
        mgr, player = _make_manager({"manage_external_players": True})
        await mgr.handle_player_state("Stopped")
        player.set_player_state.assert_called_once_with(PlayerState.STOPPED)

    async def test_noop_when_manage_players_false(self):
        mgr, player = _make_manager({"manage_external_players": False})
        await mgr.handle_player_state("Playing")
        player.set_player_state.assert_not_called()


class TestTakeoverSpareseExternalPlayers(unittest.TestCase):
    """A takeover must not stop the external player it yields to."""

    def _roster_with_external(self):
        from ovos_media.player.roster import PlayerRoster
        owned, external = MagicMock(), MagicMock()
        owned.id, owned.external = "opm:audio", False
        external.id, external.external = "mpris:vlc", True
        return PlayerRoster([owned, external]), owned, external

    def test_owned_excludes_external_adapters(self):
        roster, owned, external = self._roster_with_external()
        self.assertEqual(roster.owned, [owned])

    def test_takeover_stops_only_the_owned_players(self):
        from ovos_media.player import OCPMediaPlayer
        roster, owned, external = self._roster_with_external()
        player = MagicMock()
        player.roster = roster
        OCPMediaPlayer.handle_MPRIS_takeover(player)
        owned.stop.assert_called_once()
        external.stop.assert_not_called()


class TestRosterMembership(unittest.TestCase):
    """External players join and leave the roster as they appear and vanish."""

    def _manager_with_roster(self):
        from ovos_media.player.roster import PlayerRoster
        mgr, player = _make_manager({"manage_external_players": True})
        player.roster = PlayerRoster([])
        return mgr, player

    def test_register_adds_an_external_adapter(self):
        mgr, player = self._manager_with_roster()
        mgr._register_adapter("org.mpris.MediaPlayer2.vlc")
        adapter = player.roster.get("mpris:org.mpris.MediaPlayer2.vlc")
        self.assertIsNotNone(adapter)
        self.assertTrue(adapter.external)

    def test_register_is_idempotent(self):
        mgr, player = self._manager_with_roster()
        mgr._register_adapter("org.mpris.MediaPlayer2.vlc")
        mgr._register_adapter("org.mpris.MediaPlayer2.vlc")
        self.assertEqual(len(player.roster.adapters), 1)

    def test_unregister_removes_the_adapter(self):
        mgr, player = self._manager_with_roster()
        mgr._register_adapter("org.mpris.MediaPlayer2.vlc")
        mgr._unregister_adapter("org.mpris.MediaPlayer2.vlc")
        self.assertEqual(player.roster.adapters, [])

    def test_unregister_of_an_unknown_player_is_a_noop(self):
        mgr, player = self._manager_with_roster()
        mgr._unregister_adapter("org.mpris.MediaPlayer2.nope")
        self.assertEqual(player.roster.adapters, [])

    def test_a_player_without_a_roster_is_tolerated(self):
        mgr, player = _make_manager({"manage_external_players": True})
        player.roster = None
        mgr._register_adapter("org.mpris.MediaPlayer2.vlc")
        self.assertEqual(mgr.adapters, {})

    def test_external_adapter_never_claims_a_track(self):
        mgr, player = self._manager_with_roster()
        mgr._register_adapter("org.mpris.MediaPlayer2.vlc")
        adapter = player.roster.get("mpris:org.mpris.MediaPlayer2.vlc")
        self.assertFalse(adapter.can_play("file:///x.mp3"))

    def test_external_adapter_is_a_presence_not_a_remote_control(self):
        # nothing routes a transport verb to an external player; the manager
        # drives them on the D-Bus thread instead
        mgr, player = self._manager_with_roster()
        mgr._register_adapter("org.mpris.MediaPlayer2.vlc")
        adapter = player.roster.get("mpris:org.mpris.MediaPlayer2.vlc")
        adapter.stop()
        adapter.pause()
        adapter.seek(1000)
        mgr.loop.call_async.assert_not_called()

    def test_losing_a_player_unregisters_its_adapter(self):
        mgr, player = self._manager_with_roster()
        mgr.players["org.mpris.MediaPlayer2.vlc"] = MagicMock()
        mgr._register_adapter("org.mpris.MediaPlayer2.vlc")
        asyncio.run(mgr.handle_lost_player("org.mpris.MediaPlayer2.vlc"))
        self.assertEqual(player.roster.adapters, [])


class TestQueryPlayer(unittest.IsolatedAsyncioTestCase):
    """query_player fetches metadata and increments fail counter on error."""

    async def test_skips_when_fail_count_gte_3(self):
        mgr, _ = _make_manager()
        mgr._player_fails["vlc"] = 3
        mgr.players["vlc"] = MagicMock()
        await mgr.query_player("vlc")
        mgr.players["vlc"].get_interface.assert_not_called()

    async def test_returns_early_for_unknown_player(self):
        mgr, _ = _make_manager()
        await mgr.query_player("nonexistent")

    async def test_increments_fail_counter_on_exception(self):
        mgr, _ = _make_manager()
        mock_player_proxy = MagicMock()
        mock_iface = AsyncMock()
        mock_iface.get_metadata = AsyncMock(side_effect=Exception("dbus error"))
        mock_player_proxy.get_interface = MagicMock(return_value=mock_iface)
        mgr.players["broken"] = mock_player_proxy
        await mgr.query_player("broken")
        self.assertGreater(mgr._player_fails.get("broken", 0), 0)

    async def test_successful_query_resets_fail_counter(self):
        mgr, _ = _make_manager()

        mock_meta = {"xesam:title": MagicMock(value="Song")}
        mock_iface = AsyncMock()
        mock_iface.get_metadata = AsyncMock(return_value=mock_meta)
        mock_iface.get_playback_status = AsyncMock(return_value="Playing")
        mock_iface.get_loop_status = AsyncMock(return_value="None")

        mock_player_proxy = MagicMock()
        mock_player_proxy.get_interface = MagicMock(return_value=mock_iface)

        mgr.players["vlc"] = mock_player_proxy
        mgr._player_fails["vlc"] = 2
        mgr.update_player_meta = AsyncMock()

        await mgr.query_player("vlc")
        self.assertEqual(mgr._player_fails["vlc"], 0)


class TestHandleSyncPlayer(unittest.IsolatedAsyncioTestCase):
    """handle_sync_player calls _set_main_player when state==Playing."""

    async def test_sets_main_player_when_playing(self):
        mgr, _ = _make_manager()
        mgr._update_ocp = MagicMock()
        with patch.object(mgr, "_set_main_player", new=AsyncMock()) as mock_set:
            await mgr.handle_sync_player({
                "state": "Playing",
                "external_player": "org.mpris.MediaPlayer2.vlc"})
            mock_set.assert_awaited_once_with("org.mpris.MediaPlayer2.vlc")

    async def test_calls_update_ocp_when_not_playing(self):
        mgr, _ = _make_manager()
        mgr.main_player = "org.mpris.MediaPlayer2.vlc"
        mgr._update_ocp = MagicMock()
        await mgr.handle_sync_player({
            "state": "Paused",
            "external_player": "org.mpris.MediaPlayer2.vlc"})
        mgr._update_ocp.assert_called_once()


class TestSetMainPlayer(unittest.IsolatedAsyncioTestCase):
    """_set_main_player must log only when the name actually changes."""

    async def test_log_fires_on_change(self):
        mgr, _ = _make_manager()
        mgr.main_player = "old_player"
        with patch("ovos_media.mpris.manager.LOG") as mock_log:
            await mgr._set_main_player("new_player")
            mock_log.info.assert_called()

    async def test_no_log_when_same_name(self):
        mgr, _ = _make_manager()
        mgr.main_player = "same_player"
        with patch("ovos_media.mpris.manager.LOG") as mock_log:
            await mgr._set_main_player("same_player")
            mock_log.info.assert_not_called()


class TestStopPlayer(unittest.IsolatedAsyncioTestCase):
    """_stop_player must only mark a player Stopped on a successful call_stop.

    A failed stop must leave player_meta state untouched (not "Stopped") so
    _stop_all retries it later instead of silently skipping it forever.
    """

    def _setup(self):
        mgr, _ = _make_manager()
        mock_iface = MagicMock()
        mock_iface.call_stop = AsyncMock()
        mock_player_proxy = MagicMock()
        mock_player_proxy.get_interface.return_value = mock_iface
        mgr.players = {"p1": mock_player_proxy}
        mgr.player_meta = {"p1": {"state": "Playing"}}
        mgr.main_player = "p1"
        return mgr, mock_iface

    async def test_successful_stop_marks_stopped_and_clears_main_player(self):
        mgr, mock_iface = self._setup()
        await mgr._stop_player("p1")
        self.assertEqual(mgr.player_meta["p1"]["state"], "Stopped")
        self.assertIsNone(mgr.main_player)
        mock_iface.call_stop.assert_awaited_once()

    async def test_failed_stop_leaves_state_untouched(self):
        mgr, mock_iface = self._setup()
        mock_iface.call_stop.side_effect = Exception("dbus call failed")
        await mgr._stop_player("p1", max_tries=1)
        self.assertEqual(mgr.player_meta["p1"]["state"], "Playing")
        self.assertEqual(mgr.main_player, "p1")

    async def test_failed_stop_is_retried_on_next_call(self):
        mgr, mock_iface = self._setup()
        mock_iface.call_stop.side_effect = Exception("dbus call failed")
        await mgr._stop_player("p1", max_tries=1)
        self.assertEqual(mock_iface.call_stop.await_count, 1)
        # state stayed "Playing" so a subsequent _stop_all pass retries it
        self.assertEqual(mgr.player_meta["p1"]["state"], "Playing")
        mock_iface.call_stop.side_effect = None  # next attempt succeeds
        await mgr._stop_player("p1", max_tries=1)
        self.assertEqual(mgr.player_meta["p1"]["state"], "Stopped")


class TestTickGatedByManagePlayers(unittest.IsolatedAsyncioTestCase):
    """The watch pass only runs when external management is enabled."""

    async def test_no_scan_when_manage_players_false(self):
        mgr, _ = _make_manager({"manage_external_players": False})
        with patch.object(mgr, "scan_players", new=AsyncMock()) as mock_scan, \
                patch("ovos_media.mpris.manager.asyncio.sleep", new=AsyncMock()):
            await mgr.tick()
            mock_scan.assert_not_called()

    async def test_scan_players_called_when_manage_players_true(self):
        mgr, _ = _make_manager({"manage_external_players": True})
        mgr.players["vlc"] = MagicMock()
        with patch.object(mgr, "scan_players", new=AsyncMock()) as mock_scan, \
                patch.object(mgr, "query_player", new=AsyncMock()) as mock_query, \
                patch("ovos_media.mpris.manager.asyncio.sleep", new=AsyncMock()):
            await mgr.tick()
            mock_scan.assert_awaited_once()
            mock_query.assert_awaited_once_with("vlc")


class TestScanPlayers(unittest.IsolatedAsyncioTestCase):
    """scan_players skips the players it is meant to skip."""

    def _reply(self, names):
        from dbus_next.message import MessageType as DbusMessageType
        reply = MagicMock()
        reply.message_type = DbusMessageType.METHOD_RETURN
        reply.body = [names]
        return reply

    def _manager(self, names):
        mgr, _ = _make_manager({"manage_external_players": True})
        mgr.loop.dbus = AsyncMock()
        mgr.loop.dbus.call = AsyncMock(return_value=self._reply(names))
        mgr.loop.dbus.introspect = AsyncMock()
        mgr.loop.dbus.get_proxy_object = MagicMock()
        mgr._create_player_handler = MagicMock()
        return mgr

    async def test_ignored_and_kdeconnect_players_are_skipped(self):
        mgr = self._manager(["org.mpris.MediaPlayer2.OCP",
                             "org.mpris.MediaPlayer2.kdeconnect.phone",
                             "org.freedesktop.DBus",
                             "org.mpris.MediaPlayer2.vlc"])
        with patch.object(mgr, "query_player", new=AsyncMock()):
            await mgr.scan_players()
        self.assertEqual(list(mgr.players), ["org.mpris.MediaPlayer2.vlc"])

    async def test_a_discovered_player_joins_the_roster(self):
        mgr = self._manager(["org.mpris.MediaPlayer2.vlc"])
        with patch.object(mgr, "query_player", new=AsyncMock()):
            await mgr.scan_players()
        self.assertIn("org.mpris.MediaPlayer2.vlc", mgr.adapters)

    async def test_a_dbus_error_reply_raises(self):
        from dbus_next.message import MessageType as DbusMessageType
        mgr = self._manager([])
        reply = MagicMock()
        reply.message_type = DbusMessageType.ERROR
        reply.body = ["boom"]
        mgr.loop.dbus.call = AsyncMock(return_value=reply)
        with self.assertRaises(Exception):
            await mgr.scan_players()


class TestHandleLostPlayer(unittest.IsolatedAsyncioTestCase):
    """handle_lost_player must remove player from players and player_meta."""

    async def test_removes_from_player_meta_and_players(self):
        mgr, _ = _make_manager()
        mgr.players["some_player"] = MagicMock()
        mgr.player_meta["some_player"] = {"state": "Playing"}
        await mgr.handle_lost_player("some_player")
        self.assertNotIn("some_player", mgr.players)
        self.assertNotIn("some_player", mgr.player_meta)

    async def test_unknown_player_does_not_raise(self):
        mgr, _ = _make_manager()
        await mgr.handle_lost_player("nonexistent_player")


class TestHandleNewPlayer(unittest.IsolatedAsyncioTestCase):
    """handle_new_player must log info for unknown players."""

    async def test_logs_info_for_new_player(self):
        mgr, _ = _make_manager()
        with patch("ovos_media.mpris.manager.LOG") as mock_log:
            await mgr.handle_new_player({"name": "org.mpris.MediaPlayer2.vlc"})
            mock_log.info.assert_called_once()

    async def test_does_not_log_for_known_failed_player(self):
        mgr, _ = _make_manager()
        mgr._player_fails["org.mpris.MediaPlayer2.broken"] = 3
        with patch("ovos_media.mpris.manager.LOG") as mock_log:
            await mgr.handle_new_player({"name": "org.mpris.MediaPlayer2.broken"})
            mock_log.info.assert_not_called()


class TestMeta2Dict(unittest.TestCase):
    """_meta2dict maps xesam/mpris keys onto the OCP track fields."""

    class _V:  # dbus variant stand-in
        def __init__(self, value):
            self.value = value

    def _convert(self, meta, name="org.mpris.MediaPlayer2.vlc"):
        mgr, _ = _make_manager()
        return mgr._meta2dict(name, meta)

    def test_title_extracted(self):
        out = self._convert({"state": None, "xesam:title": self._V("Song")})
        self.assertEqual(out["title"], "Song")

    def test_artist_extracted(self):
        out = self._convert({"state": None, "xesam:artist": self._V(["Band"])})
        self.assertEqual(out["artist"], "Band")

    def test_album_extracted(self):
        out = self._convert({"state": None, "xesam:album": self._V("LP")})
        self.assertEqual(out["album"], "LP")

    def test_image_extracted(self):
        out = self._convert({"state": None, "mpris:artUrl": self._V("http://x/a.png")})
        self.assertEqual(out["image"], "http://x/a.png")

    def test_length_extracted(self):
        out = self._convert({"state": None, "mpris:length": self._V(1000)})
        self.assertEqual(out["length"], 1000)

    def test_external_player_set(self):
        out = self._convert({"state": None})
        self.assertEqual(out["external_player"], "org.mpris.MediaPlayer2.vlc")

    def test_state_defaults_to_playing_when_title_present_and_no_state(self):
        out = self._convert({"state": None, "xesam:title": self._V("Song")})
        self.assertEqual(out["state"], "Playing")

    def test_state_none_when_no_title_and_no_state(self):
        out = self._convert({"state": None})
        self.assertIsNone(out["state"])

    def test_artist_empty_list_does_not_raise_and_is_skipped(self):
        out = self._convert({"state": None, "xesam:artist": self._V([])})
        self.assertNotIn("artist", out)

    def test_artist_plain_string_kept_whole(self):
        out = self._convert({"state": None, "xesam:artist": self._V("Band")})
        self.assertEqual(out["artist"], "Band")

    def test_artist_normal_list_takes_first_element(self):
        out = self._convert({"state": None, "xesam:artist": self._V(["A", "B"])})
        self.assertEqual(out["artist"], "A")

    def test_artist_none_is_skipped(self):
        out = self._convert({"state": None, "xesam:artist": self._V(None)})
        self.assertNotIn("artist", out)

    def test_meta_without_url_gets_synthetic_uri(self):
        out = self._convert({"state": "Playing", "loop_state": None,
                             "xesam:title": self._V("Song")},
                            name="org.mpris.MediaPlayer2.spotify")
        self.assertEqual(out["uri"], "mpris://org.mpris.MediaPlayer2.spotify")

    def test_meta_with_url_maps_to_uri(self):
        out = self._convert({"state": "Playing", "loop_state": None,
                             "xesam:url": self._V("https://x/song.mp3")})
        self.assertEqual(out["uri"], "https://x/song.mp3")


class TestInternalPlayerControl(unittest.IsolatedAsyncioTestCase):
    """Direct tests for the async player control helpers."""

    def _make_player_proxy(self):
        proxy = MagicMock()
        iface = AsyncMock()
        for call in ("call_previous", "call_next", "call_pause", "call_play",
                     "call_stop", "set_shuffle", "set_loop_status"):
            setattr(iface, call, AsyncMock())
        proxy.get_interface = MagicMock(return_value=iface)
        return proxy, iface

    def _make_mgr_with_player(self, name="vlc", state="Playing"):
        mgr, _ = _make_manager()
        proxy, iface = self._make_player_proxy()
        mgr.players[name] = proxy
        mgr.player_meta[name] = {"state": state}
        return mgr, iface, name

    async def test_play_prev_calls_call_previous_when_playing(self):
        mgr, iface, name = self._make_mgr_with_player(state="Playing")
        await mgr._play_prev(name)
        iface.call_previous.assert_awaited_once()

    async def test_play_prev_noop_when_not_playing(self):
        mgr, iface, name = self._make_mgr_with_player(state="Paused")
        await mgr._play_prev(name)
        iface.call_previous.assert_not_awaited()

    async def test_play_prev_returns_early_for_unknown_player(self):
        mgr, _ = _make_manager()
        await mgr._play_prev("nonexistent")

    async def test_play_next_calls_call_next_when_playing(self):
        mgr, iface, name = self._make_mgr_with_player(state="Playing")
        await mgr._play_next(name)
        iface.call_next.assert_awaited_once()

    async def test_play_next_noop_when_not_playing(self):
        mgr, iface, name = self._make_mgr_with_player(state="Stopped")
        await mgr._play_next(name)
        iface.call_next.assert_not_awaited()

    async def test_play_next_returns_early_for_unknown_player(self):
        mgr, _ = _make_manager()
        await mgr._play_next("nonexistent")

    async def test_pause_player_pauses_when_playing(self):
        mgr, iface, name = self._make_mgr_with_player(state="Playing")
        await mgr._pause_player(name)
        iface.call_pause.assert_awaited_once()

    async def test_pause_player_noop_when_not_playing(self):
        mgr, iface, name = self._make_mgr_with_player(state="Paused")
        await mgr._pause_player(name)
        iface.call_pause.assert_not_awaited()

    async def test_pause_player_returns_early_for_unknown(self):
        mgr, _ = _make_manager()
        await mgr._pause_player("nonexistent")

    async def test_resume_player_plays_when_not_playing(self):
        mgr, iface, name = self._make_mgr_with_player(state="Paused")
        await mgr._resume_player(name)
        iface.call_play.assert_awaited_once()

    async def test_resume_player_noop_when_already_playing(self):
        mgr, iface, name = self._make_mgr_with_player(state="Playing")
        await mgr._resume_player(name)
        iface.call_play.assert_not_awaited()

    async def test_resume_player_returns_early_for_unknown(self):
        mgr, _ = _make_manager()
        await mgr._resume_player("nonexistent")

    async def test_stop_player_stops_when_playing(self):
        mgr, iface, name = self._make_mgr_with_player(state="Playing")
        await mgr._stop_player(name)
        iface.call_stop.assert_awaited_once()

    async def test_stop_player_sets_meta_to_stopped(self):
        mgr, iface, name = self._make_mgr_with_player(state="Playing")
        await mgr._stop_player(name)
        self.assertEqual(mgr.player_meta[name]["state"], "Stopped")

    async def test_stop_player_clears_main_player_when_it_is_the_main(self):
        mgr, iface, name = self._make_mgr_with_player(state="Playing")
        mgr.main_player = name
        await mgr._stop_player(name)
        self.assertIsNone(mgr.main_player)

    async def test_stop_player_returns_early_for_unknown(self):
        mgr, _ = _make_manager()
        await mgr._stop_player("nonexistent")

    async def test_stop_player_skips_when_in_players_but_not_yet_in_player_meta(self):
        # race window: scan_players() adds to self.players before query_player()
        # has populated player_meta for that name
        mgr, _ = _make_manager()
        proxy, iface = self._make_player_proxy()
        mgr.players["new_player"] = proxy
        # deliberately NOT setting mgr.player_meta["new_player"]
        await mgr._stop_player("new_player")  # must not raise KeyError
        iface.call_stop.assert_not_awaited()

    async def test_shuffle_enable_calls_set_shuffle_true(self):
        mgr, iface, name = self._make_mgr_with_player()
        await mgr._shuffle_enable(name)
        iface.set_shuffle.assert_awaited_once_with(True)

    async def test_shuffle_disable_calls_set_shuffle_false(self):
        mgr, iface, name = self._make_mgr_with_player()
        await mgr._shuffle_disable(name)
        iface.set_shuffle.assert_awaited_once_with(False)

    async def test_repeat_enable_calls_set_loop_playlist(self):
        mgr, iface, name = self._make_mgr_with_player()
        await mgr._repeat_enable(name)
        iface.set_loop_status.assert_awaited_once_with("Playlist")

    async def test_repeat_disable_calls_set_loop_none(self):
        mgr, iface, name = self._make_mgr_with_player()
        await mgr._repeat_disable(name)
        iface.set_loop_status.assert_awaited_once_with("None")

    async def test_repeat_track_enable_calls_set_loop_track(self):
        mgr, iface, name = self._make_mgr_with_player()
        await mgr._repeat_track_enable(name)
        iface.set_loop_status.assert_awaited_once_with("Track")

    async def test_stop_all_stops_all_players(self):
        mgr, _ = _make_manager()
        for name in ("vlc", "spotify"):
            mgr.players[name] = MagicMock()
            mgr.player_meta[name] = {"state": "Playing"}
        with patch.object(mgr, "_stop_player", new=AsyncMock()) as mock_stop:
            await mgr._stop_all()
            self.assertEqual(mock_stop.await_count, 2)

    async def test_pause_all_pauses_all_players(self):
        mgr, _ = _make_manager()
        for name in ("vlc", "spotify"):
            mgr.players[name] = MagicMock()
            mgr.player_meta[name] = {"state": "Playing"}
        with patch.object(mgr, "_pause_player", new=AsyncMock()) as mock_pause:
            await mgr._pause_all()
            self.assertEqual(mock_pause.await_count, 2)

    async def test_stop_all_survives_concurrent_player_loss(self):
        # Reproduces a live crash: _stop_player awaits a real D-Bus call,
        # yielding to the event loop. If a concurrent handle_lost_player()
        # (dispatched by on_properties_changed on the same loop) pops from
        # self.players mid-iteration, a plain `for p in self.players:` loop
        # raises "RuntimeError: dictionary changed size during iteration".
        # _stop_all must snapshot the players before iterating.
        mgr, _ = _make_manager()
        for name in ("vlc", "spotify"):
            mgr.players[name] = MagicMock()
            mgr.player_meta[name] = {"state": "Playing"}

        attempted = []

        async def fake_stop_player(name, max_tries=2):
            attempted.append(name)
            if name == "vlc":
                await mgr.handle_lost_player("spotify")
            await asyncio.sleep(0)

        with patch.object(mgr, "_stop_player", new=fake_stop_player):
            await mgr._stop_all()  # must not raise RuntimeError

        self.assertEqual(set(attempted), {"vlc", "spotify"})
        self.assertNotIn("spotify", mgr.players)

    async def test_pause_all_survives_concurrent_player_loss(self):
        mgr, _ = _make_manager()
        for name in ("vlc", "spotify"):
            mgr.players[name] = MagicMock()
            mgr.player_meta[name] = {"state": "Playing"}

        attempted = []

        async def fake_pause_player(name, max_tries=1):
            attempted.append(name)
            if name == "vlc":
                await mgr.handle_lost_player("spotify")
            await asyncio.sleep(0)

        with patch.object(mgr, "_pause_player", new=fake_pause_player):
            await mgr._pause_all()  # must not raise RuntimeError

        self.assertEqual(set(attempted), {"vlc", "spotify"})
        self.assertNotIn("spotify", mgr.players)


class TestInternalPlayerControlErrors(unittest.IsolatedAsyncioTestCase):
    """When interface calls raise, the helpers log a warning and give up."""

    def _make_failing_proxy(self):
        proxy = MagicMock()
        iface = AsyncMock()
        for call in ("call_previous", "call_next", "call_pause", "call_play",
                     "set_shuffle", "set_loop_status"):
            setattr(iface, call, AsyncMock(side_effect=Exception("dbus error")))
        proxy.get_interface = MagicMock(return_value=iface)
        return proxy

    def _mgr(self):
        mgr, _ = _make_manager()
        mgr.players["vlc"] = self._make_failing_proxy()
        mgr.player_meta["vlc"] = {"state": "Playing"}
        return mgr

    async def test_play_prev_logs_warning_after_failure(self):
        mgr = self._mgr()
        with patch("ovos_media.mpris.manager.LOG") as mock_log:
            await mgr._play_prev("vlc", max_tries=1)
            mock_log.warning.assert_called()

    async def test_play_next_logs_warning_after_failure(self):
        mgr = self._mgr()
        with patch("ovos_media.mpris.manager.LOG") as mock_log:
            await mgr._play_next("vlc", max_tries=1)
            mock_log.warning.assert_called()

    async def test_shuffle_enable_logs_warning_after_failure(self):
        mgr = self._mgr()
        with patch("ovos_media.mpris.manager.LOG") as mock_log:
            await mgr._shuffle_enable("vlc", max_tries=1)
            mock_log.warning.assert_called()

    async def test_repeat_enable_logs_warning_after_failure(self):
        mgr = self._mgr()
        with patch("ovos_media.mpris.manager.LOG") as mock_log:
            await mgr._repeat_enable("vlc", max_tries=1)
            mock_log.warning.assert_called()


class TestPropertiesChangedBeforeMetaExists(unittest.IsolatedAsyncioTestCase):
    """Signal arriving before player_meta[name] exists must not raise.

    The window between subscribing to a player's property signals and the
    first query_player call that fills its metadata in.
    """

    def _register_handler(self, mgr, name):
        iface = MagicMock()
        iface.bus_name = name
        proxy = MagicMock()
        proxy.get_interface = MagicMock(return_value=iface)
        mgr.players[name] = proxy
        mgr._create_player_handler(name)
        return iface.on_properties_changed.call_args[0][0]

    class _Variant:
        def __init__(self, value):
            self.value = value

    async def test_playback_status_creates_meta_without_keyerror(self):
        mgr, _ = _make_manager()
        name = "org.mpris.MediaPlayer2.mpv"
        callback = self._register_handler(mgr, name)
        self.assertNotIn(name, mgr.player_meta)

        await callback("org.mpris.MediaPlayer2.Player",
                       {"PlaybackStatus": self._Variant("Playing")}, [])

        self.assertIn(name, mgr.player_meta)
        self.assertEqual(mgr.player_meta[name]["state"], "Playing")
        # the seed carries the player's identity, not just an empty dict:
        # _apply_external_player_state reads external_player for skill_id,
        # and a bare {} reflects a track with no player behind it
        self.assertEqual(mgr.player_meta[name]["external_player"], name)

    async def test_shuffle_creates_meta_without_keyerror(self):
        mgr, _ = _make_manager()
        name = "org.mpris.MediaPlayer2.mpv"
        callback = self._register_handler(mgr, name)
        self.assertNotIn(name, mgr.player_meta)

        await callback("org.mpris.MediaPlayer2.Player",
                       {"Shuffle": self._Variant(True)}, [])

        self.assertIn(name, mgr.player_meta)
        self.assertEqual(mgr.player_meta[name]["shuffle"], True)
        self.assertEqual(mgr.player_meta[name]["external_player"], name)

    async def test_loop_status_creates_meta_without_keyerror(self):
        mgr, _ = _make_manager()
        name = "org.mpris.MediaPlayer2.mpv"
        callback = self._register_handler(mgr, name)
        self.assertNotIn(name, mgr.player_meta)

        await callback("org.mpris.MediaPlayer2.Player",
                       {"LoopStatus": self._Variant("Track")}, [])

        self.assertIn(name, mgr.player_meta)
        self.assertEqual(mgr.player_meta[name]["loop_state"],
                         LoopState.REPEAT_TRACK)
        self.assertEqual(mgr.player_meta[name]["external_player"], name)

    async def test_playback_status_updates_existing_meta_in_place(self):
        mgr, _ = _make_manager()
        name = "org.mpris.MediaPlayer2.mpv"
        mgr.player_meta[name] = {"state": "Stopped"}
        callback = self._register_handler(mgr, name)

        await callback("org.mpris.MediaPlayer2.Player",
                       {"PlaybackStatus": self._Variant("Playing")}, [])

        self.assertEqual(mgr.player_meta[name]["state"], "Playing")

    async def test_ignored_player_signals_are_dropped(self):
        mgr, _ = _make_manager()
        name = "org.mpris.MediaPlayer2.OCP"
        callback = self._register_handler(mgr, name)

        await callback("org.mpris.MediaPlayer2.Player",
                       {"PlaybackStatus": self._Variant("Playing")}, [])

        self.assertNotIn(name, mgr.player_meta)

    async def test_unreadable_properties_interface_registers_nothing(self):
        mgr, _ = _make_manager()
        proxy = MagicMock()
        proxy.get_interface = MagicMock(side_effect=Exception("chromium"))
        mgr.players["org.mpris.MediaPlayer2.chromium"] = proxy
        with patch("ovos_media.mpris.manager.LOG") as mock_log:
            mgr._create_player_handler("org.mpris.MediaPlayer2.chromium")
            mock_log.warning.assert_called()


class TestOcpUpdateWithManagedPlayersAndMissingMeta(unittest.IsolatedAsyncioTestCase):
    """With manage_external_players enabled, a PlaybackStatus signal for an
    unknown player must reach _update_ocp without raising, and the reflected
    metadata must carry a usable skill_id.
    """

    def _register_handler(self, mgr, name):
        iface = MagicMock()
        iface.bus_name = name
        proxy = MagicMock()
        proxy.get_interface = MagicMock(return_value=iface)
        mgr.players[name] = proxy
        mgr._create_player_handler(name)
        return iface.on_properties_changed.call_args[0][0]

    class _Variant:
        def __init__(self, value):
            self.value = value

    async def test_playback_status_before_metadata_completes_ocp_update(self):
        mgr, player = _make_manager({"manage_external_players": True})
        name = "org.mpris.MediaPlayer2.mpv"
        callback = self._register_handler(mgr, name)
        self.assertNotIn(name, mgr.player_meta)

        # unknown player -> no crash reaching _update_ocp, half-mutated OCP
        # state must still finish with a real skill_id
        await callback("org.mpris.MediaPlayer2.Player",
                       {"PlaybackStatus": self._Variant("Playing")}, [])

        self.assertEqual(mgr.main_player, name)
        self.assertIn(name, mgr.player_meta)
        self.assertEqual(mgr.player_meta[name]["skill_id"], name)
        player.set_now_playing.assert_called()

    async def test_playback_status_with_full_metadata_flows_unchanged(self):
        mgr, player = _make_manager({"manage_external_players": True})
        name = "org.mpris.MediaPlayer2.mpv"
        mgr.player_meta[name] = {
            "external_player": name,
            "state": "Stopped",
            "title": "Song",
            "artist": "Band",
            "uri": "mpris://mpv",
        }
        callback = self._register_handler(mgr, name)

        await callback("org.mpris.MediaPlayer2.Player",
                       {"PlaybackStatus": self._Variant("Playing")}, [])

        self.assertEqual(mgr.player_meta[name]["skill_id"], name)
        self.assertEqual(mgr.player_meta[name]["title"], "Song")
        player.set_now_playing.assert_called()


class TestFacadeTransportVerbs(unittest.TestCase):
    """Each verb the player asks for crosses to the D-Bus thread as a coroutine."""

    def test_play_prev_posts_do_play_prev(self):
        ctl = _make_facade()
        ctl.play_prev()
        self.assertIn("do_play_prev", _posted(ctl))

    def test_play_next_posts_do_play_next(self):
        ctl = _make_facade()
        ctl.play_next()
        self.assertIn("do_play_next", _posted(ctl))

    def test_resume_posts_do_resume(self):
        ctl = _make_facade()
        ctl.resume()
        self.assertIn("do_resume", _posted(ctl))

    def test_pause_posts_do_pause_all(self):
        ctl = _make_facade()
        ctl.pause()
        self.assertIn("do_pause_all", _posted(ctl))

    def test_stop_posts_do_stop_all(self):
        ctl = _make_facade()
        ctl.stop()
        self.assertIn("do_stop_all", _posted(ctl))

    def test_stop_raises_the_flag_before_returning(self):
        # the player reads stop_event to decide whether a stop is already
        # outstanding, so it cannot wait for the loop thread to run
        ctl = _make_facade()
        ctl.stop()
        self.assertTrue(ctl.stop_event.is_set())
        ctl.loop.call_async.call_args[0][0].close()

    def test_toggle_shuffle_posts_do_toggle_shuffle(self):
        ctl = _make_facade()
        ctl.toggle_shuffle()
        self.assertIn("do_toggle_shuffle", _posted(ctl))

    def test_toggle_repeat_posts_do_toggle_repeat(self):
        ctl = _make_facade()
        ctl.toggle_repeat()
        self.assertIn("do_toggle_repeat", _posted(ctl))

    def test_shutdown_stops_then_tears_the_loop_down(self):
        ctl = _make_facade()
        ctl.shutdown()
        self.assertTrue(ctl.stop_event.is_set())
        ctl.loop.shutdown.assert_called_once()
        ctl.loop.call_async.call_args[0][0].close()


class TestStopEventLifecycle(unittest.IsolatedAsyncioTestCase):
    """The stop flag stays raised until the stop has actually been applied."""

    async def test_do_stop_all_clears_the_flag(self):
        mgr, _ = _make_manager()
        mgr.stop_event.set()
        await mgr.do_stop_all()
        self.assertFalse(mgr.stop_event.is_set())

    async def test_flag_is_cleared_even_when_a_stop_fails(self):
        mgr, _ = _make_manager()
        mgr.stop_event.set()
        with patch.object(mgr, "_stop_all",
                          new=AsyncMock(side_effect=Exception("boom"))):
            with self.assertRaises(Exception):
                await mgr.do_stop_all()
        self.assertFalse(mgr.stop_event.is_set())


class TestToggleCommands(unittest.IsolatedAsyncioTestCase):
    """The toggles read the external player's own state, falling back to OCP's."""

    async def test_shuffle_toggle_follows_the_external_player(self):
        mgr, _ = _make_manager()
        mgr.main_player = "vlc"
        mgr.player_meta["vlc"] = {"shuffle": True}
        with patch.object(mgr, "_shuffle_enable", new=AsyncMock()) as enable:
            await mgr.do_toggle_shuffle()
            enable.assert_awaited_once_with("vlc")

    async def test_shuffle_toggle_without_meta_falls_back_to_ocp(self):
        mgr, player = _make_manager()
        mgr.main_player = "vlc"
        player.shuffle = False
        with patch.object(mgr, "_shuffle_disable", new=AsyncMock()) as disable:
            await mgr.do_toggle_shuffle()
            disable.assert_awaited_once_with("vlc")

    async def test_repeat_toggle_cycles_none_to_playlist(self):
        mgr, player = _make_manager()
        mgr.main_player = "vlc"
        mgr.player_meta["vlc"] = {"loop_state": LoopState.NONE}
        player.loop_state = LoopState.NONE
        with patch.object(mgr, "_repeat_enable", new=AsyncMock()) as enable:
            await mgr.do_toggle_repeat()
            enable.assert_awaited_once_with("vlc")

    async def test_repeat_toggle_cycles_playlist_to_track(self):
        mgr, _ = _make_manager()
        mgr.main_player = "vlc"
        mgr.player_meta["vlc"] = {"loop_state": LoopState.REPEAT}
        with patch.object(mgr, "_repeat_track_enable", new=AsyncMock()) as enable:
            await mgr.do_toggle_repeat()
            enable.assert_awaited_once_with("vlc")

    async def test_repeat_toggle_cycles_track_to_none(self):
        mgr, _ = _make_manager()
        mgr.main_player = "vlc"
        mgr.player_meta["vlc"] = {"loop_state": LoopState.REPEAT_TRACK}
        with patch.object(mgr, "_repeat_disable", new=AsyncMock()) as disable:
            await mgr.do_toggle_repeat()
            disable.assert_awaited_once_with("vlc")

    async def test_toggle_without_a_main_player_does_not_raise(self):
        # a KeyError here used to crash the whole MPRIS daemon into its
        # restart loop
        mgr, player = _make_manager()
        player.shuffle = False
        player.loop_state = LoopState.NONE
        await mgr.do_toggle_shuffle()
        await mgr.do_toggle_repeat()


class TestFacadeWiring(unittest.TestCase):
    """The facade owns the three units and joins them to each other."""

    def _make_real_facade(self, config=None):
        from ovos_media.mpris import OcpMprisExporter
        player = MagicMock()
        with patch("ovos_media.mpris.DbusLoop.start"), \
                patch("ovos_media.mpris.exporter._MediaPlayer2Interface"), \
                patch("ovos_media.mpris.exporter._MediaPlayer2PlayerInterface"):
            return OcpMprisExporter(player, config=config or {}, daemonic=False)

    def test_loop_publishes_the_exporter_on_connect(self):
        ctl = self._make_real_facade()
        self.assertEqual(ctl.loop.on_connect, ctl.exporter.export)

    def test_loop_ticks_the_manager(self):
        ctl = self._make_real_facade()
        self.assertEqual(ctl.loop.tick, ctl.manager.tick)

    def test_stop_event_is_the_managers(self):
        from threading import Event
        ctl = self._make_real_facade()
        self.assertIsInstance(ctl.stop_event, Event)
        self.assertIs(ctl.stop_event, ctl.manager.stop_event)

    def test_default_manage_players_false(self):
        ctl = self._make_real_facade()
        self.assertFalse(ctl.manage_players)

    def test_manage_players_true_from_config(self):
        ctl = self._make_real_facade({"manage_external_players": True})
        self.assertTrue(ctl.manage_players)

    def test_loop_is_daemonic_when_asked(self):
        from ovos_media.mpris import OcpMprisExporter
        player = MagicMock()
        with patch("ovos_media.mpris.DbusLoop.start"), \
                patch("ovos_media.mpris.exporter._MediaPlayer2Interface"), \
                patch("ovos_media.mpris.exporter._MediaPlayer2PlayerInterface"):
            ctl = OcpMprisExporter(player, daemonic=True)
        self.assertTrue(ctl.loop.daemon)


if __name__ == "__main__":
    unittest.main()

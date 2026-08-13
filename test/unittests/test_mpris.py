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
    """Position property must return microseconds (position * 1000).

    now_playing.position is milliseconds (repo-wide ms contract, produced by
    ovos-plugin-manager templates' playback_time), NOT seconds. MPRIS wants
    microseconds, so the conversion factor is *1000, not *1e6.
    """

    def test_position_microseconds(self):
        iface, player = _make_interface()
        player.now_playing.position = 30_000  # milliseconds
        result = iface.Position
        self.assertAlmostEqual(result, 30_000 * 1000)

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


class TestStopPlayer(unittest.IsolatedAsyncioTestCase):
    """_stop_player must only mark a player Stopped on a successful call_stop.

    A failed stop must leave player_meta state untouched (not "Stopped") so
    _stop_all retries it later instead of silently skipping it forever.
    """

    def _setup(self):
        ctl, player = _make_exporter()
        mock_iface = MagicMock()
        mock_iface.call_stop = AsyncMock()
        mock_player_proxy = MagicMock()
        mock_player_proxy.get_interface.return_value = mock_iface
        ctl.players = {"p1": mock_player_proxy}
        ctl.player_meta = {"p1": {"state": "Playing"}}
        ctl.main_player = "p1"
        return ctl, mock_iface

    async def test_successful_stop_marks_stopped_and_clears_main_player(self):
        ctl, mock_iface = self._setup()
        await ctl._stop_player("p1")
        self.assertEqual(ctl.player_meta["p1"]["state"], "Stopped")
        self.assertIsNone(ctl.main_player)
        mock_iface.call_stop.assert_awaited_once()

    async def test_failed_stop_leaves_state_untouched(self):
        ctl, mock_iface = self._setup()
        mock_iface.call_stop.side_effect = Exception("dbus call failed")
        await ctl._stop_player("p1", max_tries=1)
        self.assertEqual(ctl.player_meta["p1"]["state"], "Playing")
        self.assertEqual(ctl.main_player, "p1")

    async def test_failed_stop_is_retried_on_next_call(self):
        ctl, mock_iface = self._setup()
        mock_iface.call_stop.side_effect = Exception("dbus call failed")
        await ctl._stop_player("p1", max_tries=1)
        self.assertEqual(mock_iface.call_stop.await_count, 1)
        # state stayed "Playing" so a subsequent _stop_all pass retries it
        self.assertEqual(ctl.player_meta["p1"]["state"], "Playing")
        mock_iface.call_stop.side_effect = None  # next attempt succeeds
        await ctl._stop_player("p1", max_tries=1)
        self.assertEqual(ctl.player_meta["p1"]["state"], "Stopped")


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


# ---------------------------------------------------------------------------
# Additional tests added to improve coverage
# ---------------------------------------------------------------------------

def _make_mp2_interface():
    """Return a _MediaPlayer2Interface with a mocked player (no DBus init)."""
    from ovos_media.mpris import _MediaPlayer2Interface
    player = MagicMock()
    player.playlist = []  # HasTrackList depends on len(playlist)
    iface = _MediaPlayer2Interface.__new__(_MediaPlayer2Interface)
    iface._identity = "OCP"
    iface._desktopEntry = "OCP"
    iface._supportedMimeTypes = ["audio/mpeg"]
    iface._supportedUriSchemes = ["file", "http"]
    iface._canQuit = False
    iface._hasTrackList = False
    iface._ocp_player = player
    return iface, player


class TestMediaPlayer2InterfaceProperties(unittest.TestCase):
    """Read-only properties of _MediaPlayer2Interface."""

    def test_identity(self):
        iface, _ = _make_mp2_interface()
        self.assertEqual(iface.Identity, "OCP")

    def test_desktop_entry(self):
        iface, _ = _make_mp2_interface()
        self.assertEqual(iface.DesktopEntry, "OCP")

    def test_supported_mime_types(self):
        iface, _ = _make_mp2_interface()
        self.assertIn("audio/mpeg", iface.SupportedMimeTypes)

    def test_supported_uri_schemes(self):
        iface, _ = _make_mp2_interface()
        self.assertIn("file", iface.SupportedUriSchemes)

    def test_has_track_list_always_true(self):
        # The property body unconditionally returns True regardless of _hasTrackList
        iface, _ = _make_mp2_interface()
        self.assertTrue(iface.HasTrackList)

    def test_can_quit_false_by_default(self):
        iface, _ = _make_mp2_interface()
        self.assertFalse(iface.CanQuit)

    def test_can_set_fullscreen_false(self):
        iface, _ = _make_mp2_interface()
        self.assertFalse(iface.CanSetFullscreen)

    def test_fullscreen_false(self):
        iface, _ = _make_mp2_interface()
        self.assertFalse(iface.Fullscreen)

    def test_can_raise_false(self):
        iface, _ = _make_mp2_interface()
        self.assertFalse(iface.CanRaise)


class TestMediaPlayer2InterfaceQuit(unittest.TestCase):
    """Quit() behaviour depends on _canQuit flag."""

    def test_quit_calls_shutdown_when_can_quit_true(self):
        iface, player = _make_mp2_interface()
        iface._canQuit = True
        iface.Quit()
        player.shutdown.assert_called_once()

    def test_quit_does_nothing_when_can_quit_false(self):
        iface, player = _make_mp2_interface()
        iface._canQuit = False
        iface.Quit()
        player.shutdown.assert_not_called()


class TestPlaybackStatus(unittest.TestCase):
    """PlaybackStatus must return the right MPRIS string for each PlayerState."""

    def test_playing_state(self):
        iface, player = _make_interface()
        player.state = PlayerState.PLAYING
        self.assertEqual(iface.PlaybackStatus, "Playing")

    def test_paused_state(self):
        iface, player = _make_interface()
        player.state = PlayerState.PAUSED
        self.assertEqual(iface.PlaybackStatus, "Paused")

    def test_stopped_state(self):
        iface, player = _make_interface()
        player.state = PlayerState.STOPPED
        self.assertEqual(iface.PlaybackStatus, "Stopped")


class TestLoopStatusGetter(unittest.TestCase):
    """LoopStatus getter must return correct MPRIS strings."""

    def test_repeat_track(self):
        """MPRIS 2.2 spec requires 'Track' (not 'RepeatTrack')."""
        iface, player = _make_interface()
        player.loop_state = LoopState.REPEAT_TRACK
        self.assertEqual(iface.LoopStatus, "Track")

    def test_repeat(self):
        """MPRIS 2.2 spec requires 'Playlist' for full-playlist repeat."""
        iface, player = _make_interface()
        player.loop_state = LoopState.REPEAT
        self.assertEqual(iface.LoopStatus, "Playlist")

    def test_none(self):
        iface, player = _make_interface()
        player.loop_state = LoopState.NONE
        self.assertEqual(iface.LoopStatus, "None")


class TestShuffleGetterSetter(unittest.TestCase):
    """Shuffle getter/setter delegates to the underlying player."""

    def test_getter_returns_player_shuffle(self):
        iface, player = _make_interface()
        player.shuffle = True
        self.assertTrue(iface.Shuffle)

    def test_getter_returns_false_when_player_shuffle_false(self):
        iface, player = _make_interface()
        player.shuffle = False
        self.assertFalse(iface.Shuffle)

    def test_setter_sets_player_shuffle_true(self):
        from ovos_media.mpris import _MediaPlayer2PlayerInterface
        iface, player = _make_interface()
        _MediaPlayer2PlayerInterface.Shuffle_setter.fset(iface, True)
        self.assertTrue(player.shuffle)

    def test_setter_sets_player_shuffle_false(self):
        from ovos_media.mpris import _MediaPlayer2PlayerInterface
        iface, player = _make_interface()
        _MediaPlayer2PlayerInterface.Shuffle_setter.fset(iface, False)
        self.assertFalse(player.shuffle)


class TestCanProperties(unittest.TestCase):
    """CanPlay, CanPause, CanGoNext, CanGoPrevious, CanControl."""

    def test_can_play_true_when_paused(self):
        iface, player = _make_interface()
        player.state = PlayerState.PAUSED
        self.assertTrue(iface.CanPlay)

    def test_can_play_false_when_playing(self):
        iface, player = _make_interface()
        player.state = PlayerState.PLAYING
        self.assertFalse(iface.CanPlay)

    def test_can_pause_true_when_playing(self):
        iface, player = _make_interface()
        player.state = PlayerState.PLAYING
        self.assertTrue(iface.CanPause)

    def test_can_pause_false_when_paused(self):
        iface, player = _make_interface()
        player.state = PlayerState.PAUSED
        self.assertFalse(iface.CanPause)

    def test_can_go_next_delegates(self):
        iface, player = _make_interface()
        player.can_next = True
        self.assertTrue(iface.CanGoNext)

    def test_can_go_previous_delegates(self):
        iface, player = _make_interface()
        player.can_prev = True
        self.assertTrue(iface.CanGoPrevious)

    def test_can_control_always_true(self):
        iface, _ = _make_interface()
        self.assertTrue(iface.CanControl)


class TestPlayerInterfaceMethods(unittest.TestCase):
    """Play, Pause, Previous, Next, PlayPause dispatch correctly."""

    def test_play_calls_resume(self):
        iface, player = _make_interface()
        iface.Play()
        player.resume.assert_called_once()

    def test_pause_calls_pause(self):
        iface, player = _make_interface()
        iface.Pause()
        player.pause.assert_called_once()

    def test_previous_calls_play_prev(self):
        iface, player = _make_interface()
        iface.Previous()
        player.play_prev.assert_called_once()

    def test_next_calls_play_next(self):
        iface, player = _make_interface()
        iface.Next()
        player.play_next.assert_called_once()

    def test_play_pause_resumes_when_paused(self):
        iface, player = _make_interface()
        player.state = PlayerState.PAUSED
        iface.PlayPause()
        player.resume.assert_called_once()
        player.pause.assert_not_called()

    def test_play_pause_pauses_when_playing(self):
        iface, player = _make_interface()
        player.state = PlayerState.PLAYING
        iface.PlayPause()
        player.pause.assert_called_once()
        player.resume.assert_not_called()


class TestOcpMprisExporterUpdateProps(unittest.TestCase):
    """update_props must call emit_properties_changed on the player interface."""

    def test_update_props_forwards_to_player_interface(self):
        ctl, _ = _make_exporter()
        mock_iface = MagicMock()
        ctl.mediaPlayer2PlayerInterface = mock_iface
        ctl.update_props({"PlaybackStatus": "Playing"})
        mock_iface.emit_properties_changed.assert_called_once_with(
            {"PlaybackStatus": "Playing"}
        )


class TestMeta2Dict(unittest.TestCase):
    """_meta2dict must parse dbus_next Variant objects into an OCP dict."""

    def _make_variant(self, value):
        v = MagicMock()
        v.value = value
        return v

    def _call(self, meta):
        ctl, _ = _make_exporter()
        return ctl._meta2dict("test_player", meta)

    def test_title_extracted(self):
        result = self._call({"xesam:title": self._make_variant("My Song")})
        self.assertEqual(result["title"], "My Song")

    def test_artist_extracted(self):
        result = self._call({"xesam:artist": self._make_variant(["Artist A"])})
        self.assertEqual(result["artist"], "Artist A")

    def test_album_extracted(self):
        result = self._call({"xesam:album": self._make_variant("Great Album")})
        self.assertEqual(result["album"], "Great Album")

    def test_image_extracted(self):
        result = self._call({"mpris:artUrl": self._make_variant("http://img.png")})
        self.assertEqual(result["image"], "http://img.png")

    def test_length_extracted(self):
        result = self._call({"mpris:length": self._make_variant(123456)})
        self.assertEqual(result["length"], 123456)

    def test_external_player_set(self):
        result = self._call({})
        self.assertEqual(result["external_player"], "test_player")

    def test_state_defaults_to_playing_when_title_present_and_no_state(self):
        result = self._call({"xesam:title": self._make_variant("Song"),
                             "state": None})
        self.assertEqual(result["state"], "Playing")

    def test_state_none_when_no_title_and_no_state(self):
        result = self._call({})
        # no title and no state → state remains None (falsy) and not overridden
        self.assertIsNone(result["state"])

    def test_artist_empty_list_does_not_raise_and_is_skipped(self):
        # browsers/podcast apps send an empty xesam:artist list
        result = self._call({"xesam:artist": self._make_variant([])})
        self.assertNotIn("artist", result)

    def test_artist_plain_string_kept_whole(self):
        # a plain string must not be truncated to its first character
        result = self._call({"xesam:artist": self._make_variant("Solo")})
        self.assertEqual(result["artist"], "Solo")

    def test_artist_normal_list_takes_first_element(self):
        result = self._call({"xesam:artist": self._make_variant(["A", "B"])})
        self.assertEqual(result["artist"], "A")

    def test_artist_none_is_skipped(self):
        result = self._call({"xesam:artist": self._make_variant(None)})
        self.assertNotIn("artist", result)


class TestRunRetryBound(unittest.TestCase):
    """run() must bound its retry loop and never recurse unboundedly."""

    def test_run_retries_bounded_and_does_not_recurse(self):
        ctl, _ = _make_exporter()
        ctl.shutdown_event = MagicMock()
        ctl.shutdown_event.is_set.return_value = False

        loop = MagicMock()
        loop.run_until_complete = MagicMock(side_effect=RuntimeError("boom"))
        ctl.loop = loop

        with patch("ovos_media.mpris.LOG") as mock_log:
            ctl.run()  # must return, not raise RecursionError
            mock_log.error.assert_called_with("MPRIS exited")

        # initial attempt + 5 retries = 6 calls
        self.assertEqual(loop.run_until_complete.call_count, 6)

    def test_run_stops_immediately_once_shutdown_event_is_set(self):
        ctl, _ = _make_exporter()
        ctl.shutdown_event = MagicMock()
        ctl.shutdown_event.is_set.return_value = True

        loop = MagicMock()
        loop.run_until_complete = MagicMock(side_effect=RuntimeError("boom"))
        ctl.loop = loop

        ctl.run()
        loop.run_until_complete.assert_called_once()

    def test_run_returns_cleanly_on_success(self):
        ctl, _ = _make_exporter()
        ctl.shutdown_event = MagicMock()
        ctl.shutdown_event.is_set.return_value = False

        loop = MagicMock()
        loop.run_until_complete = MagicMock(return_value=None)
        ctl.loop = loop

        ctl.run()
        loop.run_until_complete.assert_called_once()


class TestHandleLostPlayer(unittest.IsolatedAsyncioTestCase):
    """handle_lost_player must remove player from players and player_meta."""

    async def test_removes_from_player_meta_and_players(self):
        ctl, _ = _make_exporter()
        ctl.players["some_player"] = MagicMock()
        ctl.player_meta["some_player"] = {"state": "Playing"}
        await ctl.handle_lost_player("some_player")
        self.assertNotIn("some_player", ctl.players)
        self.assertNotIn("some_player", ctl.player_meta)

    async def test_unknown_player_does_not_raise(self):
        ctl, _ = _make_exporter()
        # Should not raise even if player is unknown
        await ctl.handle_lost_player("nonexistent_player")


class TestHandleNewPlayer(unittest.IsolatedAsyncioTestCase):
    """handle_new_player must log info for unknown players."""

    async def test_logs_info_for_new_player(self):
        ctl, _ = _make_exporter()
        with patch("ovos_media.mpris.LOG") as mock_log:
            await ctl.handle_new_player({"name": "org.mpris.MediaPlayer2.vlc"})
            mock_log.info.assert_called_once()

    async def test_does_not_log_for_known_failed_player(self):
        ctl, _ = _make_exporter()
        ctl._player_fails["org.mpris.MediaPlayer2.broken"] = 3
        with patch("ovos_media.mpris.LOG") as mock_log:
            await ctl.handle_new_player({"name": "org.mpris.MediaPlayer2.broken"})
            mock_log.info.assert_not_called()


class TestEventControlMethods(unittest.TestCase):
    """play_prev/play_next/resume/pause/stop/toggle_shuffle/toggle_repeat set events."""

    def _make_ctl_with_events(self):
        ctl, _ = _make_exporter()
        for attr in ("prev_event", "next_event", "resume_event",
                     "pause_event", "stop_event", "shuffle_event",
                     "repeat_event", "shutdown_event"):
            setattr(ctl, attr, MagicMock())
        return ctl

    def test_play_prev_sets_prev_event(self):
        ctl = self._make_ctl_with_events()
        ctl.play_prev()
        ctl.prev_event.set.assert_called_once()

    def test_play_next_sets_next_event(self):
        ctl = self._make_ctl_with_events()
        ctl.play_next()
        ctl.next_event.set.assert_called_once()

    def test_resume_sets_resume_event(self):
        ctl = self._make_ctl_with_events()
        ctl.resume()
        ctl.resume_event.set.assert_called_once()

    def test_pause_sets_pause_event(self):
        ctl = self._make_ctl_with_events()
        ctl.pause()
        ctl.pause_event.set.assert_called_once()

    def test_stop_sets_stop_event(self):
        ctl = self._make_ctl_with_events()
        ctl.stop()
        ctl.stop_event.set.assert_called_once()

    def test_toggle_shuffle_sets_shuffle_event(self):
        ctl = self._make_ctl_with_events()
        ctl.toggle_shuffle()
        ctl.shuffle_event.set.assert_called_once()

    def test_toggle_repeat_sets_repeat_event(self):
        ctl = self._make_ctl_with_events()
        ctl.toggle_repeat()
        ctl.repeat_event.set.assert_called_once()


class TestShutdown(unittest.TestCase):
    """shutdown() must set the shutdown_event and stop the loop."""

    def test_shutdown_sets_events_and_stops_loop(self):
        ctl, _ = _make_exporter()
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = False
        ctl.loop = mock_loop
        for attr in ("prev_event", "next_event", "resume_event",
                     "pause_event", "stop_event", "shuffle_event",
                     "repeat_event", "shutdown_event"):
            setattr(ctl, attr, MagicMock())

        with patch.object(ctl, "join"):  # Thread not started in unit test; avoid RuntimeError
            ctl.shutdown()

        ctl.stop_event.set.assert_called()   # stop() is called first
        ctl.shutdown_event.set.assert_called_once()
        mock_loop.call_soon_threadsafe.assert_called_once_with(mock_loop.stop)


if __name__ == "__main__":
    unittest.main()

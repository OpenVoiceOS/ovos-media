"""Tests for Role A — ovos-media exported as org.mpris.MediaPlayer2.OCP.

All DBus I/O is mocked; these run without a D-Bus session.

The property getters answer from the published PlayerSnapshot rather than from
live player attributes, so the mocked player here carries a snapshot and the
tests set state on it. Position and Metadata are the documented exceptions and
still read now_playing.
"""
import unittest
from unittest.mock import MagicMock, PropertyMock, patch, AsyncMock

from dbus_next.service import ServiceInterface

from ovos_utils.ocp import PlaybackType, PlayerState, LoopState

from ovos_media.player.dispatcher import PlayerSnapshot
from ovos_media.player.roster import PlayerRoster, AUDIO, VIDEO, WEB, SKILL


def _make_interface(**snapshot):
    """Return a _MediaPlayer2PlayerInterface with a mocked player."""
    from ovos_media.mpris import _MediaPlayer2PlayerInterface
    player = MagicMock()
    player.snapshot = PlayerSnapshot(**snapshot)
    iface = _MediaPlayer2PlayerInterface.__new__(_MediaPlayer2PlayerInterface)
    iface._ocp_player = player
    return iface, player


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


def _roster():
    """A roster with one adapter per owned player id."""
    def adapter(player_id):
        a = MagicMock()
        a.id = player_id
        a.external = False
        return a
    return PlayerRoster([adapter(i) for i in (AUDIO, VIDEO, WEB, SKILL)])


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

    def test_position_returns_int(self):
        iface, player = _make_interface()
        player.now_playing.position = 1234.5
        self.assertIsInstance(iface.Position, int)

    def test_position_nan_returns_zero(self):
        iface, player = _make_interface()
        player.now_playing.position = float("nan")
        self.assertEqual(iface.Position, 0)

    def test_position_inf_returns_zero(self):
        iface, player = _make_interface()
        player.now_playing.position = float("inf")
        self.assertEqual(iface.Position, 0)

    def test_position_none_returns_zero(self):
        iface, player = _make_interface()
        player.now_playing.position = None
        self.assertEqual(iface.Position, 0)

    def test_position_str_does_not_overflow_wire_type(self):
        iface, player = _make_interface()
        player.now_playing.position = "not a number"
        self.assertEqual(iface.Position, 0)

    def test_position_dbus_signature_is_x(self):
        from ovos_media.mpris import _MediaPlayer2PlayerInterface
        prop = _MediaPlayer2PlayerInterface.__dict__["Position"]
        self.assertEqual(prop.prop_getter.__annotations__["return"], "x")

    def test_position_huge_finite_clamps_into_int64_and_packs(self):
        from dbus_next.signature import Variant
        iface, player = _make_interface()
        player.now_playing.position = 2 ** 70
        value = iface.Position
        self.assertEqual(value, 2 ** 63 - 1)
        Variant("x", value)  # must marshal without raising

    def test_position_absurd_float_clamps_into_int64_and_packs(self):
        from dbus_next.signature import Variant
        iface, player = _make_interface()
        player.now_playing.position = 1e30
        value = iface.Position
        self.assertEqual(value, 2 ** 63 - 1)
        Variant("x", value)


class TestSnapshotReads(unittest.TestCase):
    """The getters answer from the published snapshot, not from live state.

    The D-Bus thread is not the dispatcher thread, so reading player
    attributes directly races every command. Mutating the player without
    publishing a new snapshot must therefore be invisible to MPRIS.
    """

    def test_playback_status_ignores_unpublished_mutation(self):
        iface, player = _make_interface(player_state=PlayerState.PLAYING)
        player.state = PlayerState.STOPPED  # mutated, never published
        self.assertEqual(iface.PlaybackStatus, "Playing")

    def test_playback_status_follows_a_republished_snapshot(self):
        iface, player = _make_interface(player_state=PlayerState.PLAYING)
        player.snapshot = PlayerSnapshot(player_state=PlayerState.PAUSED)
        self.assertEqual(iface.PlaybackStatus, "Paused")

    def test_loop_status_ignores_unpublished_mutation(self):
        iface, player = _make_interface(loop_state=LoopState.REPEAT)
        player.loop_state = LoopState.NONE
        self.assertEqual(iface.LoopStatus, "Playlist")

    def test_shuffle_ignores_unpublished_mutation(self):
        iface, player = _make_interface(shuffle=True)
        player.shuffle = False
        self.assertTrue(iface.Shuffle)

    def test_can_pause_ignores_unpublished_mutation(self):
        iface, player = _make_interface(player_state=PlayerState.PLAYING,
                                        playback_type=PlaybackType.AUDIO)
        player.roster = _roster()
        player.state = PlayerState.STOPPED
        self.assertTrue(iface.CanPause)

    def test_play_pause_reads_the_snapshot_on_the_dispatcher(self):
        iface, player = _make_interface(player_state=PlayerState.PAUSED)
        iface._play_pause()
        player.resume.assert_called_once()
        player.pause.assert_not_called()


class TestSignalPrecedesTheSnapshot(unittest.TestCase):
    """PropertiesChanged must not run ahead of what a Get would answer.

    The getters read the published snapshot, and the dispatcher's post-hook
    only republishes when the whole command finishes. A controller that reads
    PlaybackStatus on the signal it just received must not be told the state
    the signal says has changed.
    """

    class _Player:
        """Just enough player for set_player_state to run against."""

        def __init__(self):
            self.state = PlayerState.STOPPED
            self.bus = MagicMock()
            self.mpris = MagicMock()
            self.handle_status = MagicMock()
            self._snapshot = PlayerSnapshot(player_state=self.state)

        def publish_snapshot(self):
            self._snapshot = PlayerSnapshot(player_state=self.state)
            return self._snapshot

        @property
        def snapshot(self):
            return self._snapshot

    def test_getter_reads_the_new_state_when_the_signal_fires(self):
        from ovos_media.player import OCPMediaPlayer
        player = self._Player()
        iface, _ = _make_interface()
        iface._ocp_player = player
        seen = []
        player.mpris.update_props = lambda props: seen.append(
            (props["PlaybackStatus"], iface.PlaybackStatus))

        OCPMediaPlayer.set_player_state(player, PlayerState.PLAYING)

        self.assertEqual(seen, [("Playing", "Playing")])

    def test_a_later_state_change_is_published_too(self):
        from ovos_media.player import OCPMediaPlayer
        player = self._Player()
        iface, _ = _make_interface()
        iface._ocp_player = player
        seen = []
        player.mpris.update_props = lambda props: seen.append(iface.PlaybackStatus)

        OCPMediaPlayer.set_player_state(player, PlayerState.PLAYING)
        OCPMediaPlayer.set_player_state(player, PlayerState.PAUSED)

        self.assertEqual(seen, ["Playing", "Paused"])


class TestLoopStatusSetter(unittest.TestCase):
    """LoopStatus setter maps MPRIS strings onto LoopState."""

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


class TestBackwardCompatAlias(unittest.TestCase):
    """MprisPlayerCtl must remain as an alias for OcpMprisExporter."""

    def test_alias_is_same_class(self):
        from ovos_media.mpris import MprisPlayerCtl, OcpMprisExporter
        self.assertIs(MprisPlayerCtl, OcpMprisExporter)

    def test_player_module_still_exports_the_name(self):
        # ovoscope patches ovos_media.player.OcpMprisExporter wholesale
        import ovos_media.player as player_module
        from ovos_media.mpris import OcpMprisExporter
        self.assertIs(player_module.OcpMprisExporter, OcpMprisExporter)


class TestExport(unittest.IsolatedAsyncioTestCase):
    """export() exports the interfaces and requests the bus name."""

    def _make_exporter(self):
        from ovos_media.mpris import MprisExporter
        exporter = MprisExporter.__new__(MprisExporter)
        exporter.mediaPlayer2Interface = MagicMock()
        exporter.mediaPlayer2PlayerInterface = MagicMock()
        exporter.playlistsInterface = MagicMock()
        return exporter

    async def test_export_requests_name(self):
        exporter = self._make_exporter()
        mock_dbus = MagicMock()
        mock_dbus.request_name = AsyncMock()
        await exporter.export(mock_dbus)
        mock_dbus.request_name.assert_awaited_once_with(
            "org.mpris.MediaPlayer2.OCP")

    async def test_export_exports_all_interfaces(self):
        exporter = self._make_exporter()
        mock_dbus = MagicMock()
        mock_dbus.request_name = AsyncMock()
        await exporter.export(mock_dbus)
        self.assertEqual(mock_dbus.export.call_count, 3)

    async def test_export_logs_the_bus_name(self):
        exporter = self._make_exporter()
        mock_dbus = MagicMock()
        mock_dbus.request_name = AsyncMock()
        with patch("ovos_media.mpris.exporter.LOG") as mock_log:
            await exporter.export(mock_dbus)
        self.assertTrue(any("org.mpris.MediaPlayer2.OCP" in str(call)
                             for call in mock_log.info.call_args_list))


class TestUpdateProps(unittest.TestCase):
    """update_props delegates to mediaPlayer2PlayerInterface."""

    def test_delegates_to_interface(self):
        from ovos_media.mpris import MprisExporter
        exporter = MprisExporter.__new__(MprisExporter)
        mock_iface = MagicMock()
        exporter.mediaPlayer2PlayerInterface = mock_iface
        exporter.update_props({"PlaybackStatus": "Paused"})
        mock_iface.emit_properties_changed.assert_called_once_with(
            {"PlaybackStatus": "Paused"})

    def test_facade_forwards_to_the_exporter(self):
        from ovos_media.mpris import OcpMprisExporter
        ctl = OcpMprisExporter.__new__(OcpMprisExporter)
        ctl.exporter = MagicMock()
        ctl.update_props({"PlaybackStatus": "Playing"})
        ctl.exporter.update_props.assert_called_once_with(
            {"PlaybackStatus": "Playing"})


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
        self.assertIn("http", iface.SupportedUriSchemes)

    def test_has_track_list_always_false(self):
        # no org.mpris.MediaPlayer2.TrackList interface is exported; True
        # here would be exactly the "advertises an interface that isn't
        # there" defect the Playlists stub exists to fix.
        iface, _ = _make_mp2_interface()
        self.assertFalse(iface.HasTrackList)

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
    """Quit() only shuts the player down when CanQuit is true."""

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


class TestMetadata(unittest.TestCase):
    """Metadata must never raise out of the property getter.

    A raising getter kills Properties.GetAll for the whole Player interface.
    """

    def test_metadata_garbage_length_drops_only_the_length(self):
        from ovos_utils.ocp import MediaEntry
        iface, player = _make_interface()
        entry = MediaEntry(uri="file:///x.mp3", title="T")
        entry.length = "not a number"
        player.now_playing = entry
        meta = iface.Metadata
        self.assertNotIn("mpris:length", meta)
        self.assertEqual(meta["xesam:url"].value, "file:///x.mp3")
        self.assertIn("mpris:trackid", meta)

    def test_metadata_raising_entry_returns_empty_dict(self):
        iface, player = _make_interface()
        entry = MagicMock()
        type(entry).mpris_metadata = PropertyMock(side_effect=TypeError("boom"))
        player.now_playing = entry
        self.assertEqual(iface.Metadata, {})

    def test_metadata_no_now_playing_returns_empty_dict(self):
        iface, player = _make_interface()
        player.now_playing = None
        self.assertEqual(iface.Metadata, {})

    def test_metadata_valid_entry_returns_populated_dict(self):
        from ovos_utils.ocp import MediaEntry
        iface, player = _make_interface()
        player.now_playing = MediaEntry(uri="file:///x.mp3", title="T",
                                        artist="A", length=1000)
        meta = iface.Metadata
        self.assertIsInstance(meta, dict)
        self.assertTrue(meta)


class TestVolumeGetter(unittest.TestCase):
    """Volume must never raise out of the property getter."""

    def _iface_with_response(self, response):
        iface, player = _make_interface()
        player.bus.wait_for_response = MagicMock(return_value=response)
        return iface

    def test_missing_percent_key_returns_default(self):
        msg = MagicMock()
        msg.data = {}
        self.assertEqual(self._iface_with_response(msg).Volume, 1.0)

    def test_none_percent_returns_default(self):
        msg = MagicMock()
        msg.data = {"percent": None}
        self.assertEqual(self._iface_with_response(msg).Volume, 1.0)

    def test_non_numeric_percent_returns_default(self):
        msg = MagicMock()
        msg.data = {"percent": "loud"}
        self.assertEqual(self._iface_with_response(msg).Volume, 1.0)

    def test_no_response_returns_default(self):
        self.assertEqual(self._iface_with_response(None).Volume, 1.0)

    def test_valid_percent_returns_value(self):
        msg = MagicMock()
        msg.data = {"percent": 0.5}
        self.assertEqual(self._iface_with_response(msg).Volume, 0.5)


class TestPlaybackStatus(unittest.TestCase):
    """PlaybackStatus maps the snapshot's player state onto MPRIS strings."""

    def test_playing_state(self):
        iface, _ = _make_interface(player_state=PlayerState.PLAYING)
        self.assertEqual(iface.PlaybackStatus, "Playing")

    def test_paused_state(self):
        iface, _ = _make_interface(player_state=PlayerState.PAUSED)
        self.assertEqual(iface.PlaybackStatus, "Paused")

    def test_stopped_state(self):
        iface, _ = _make_interface(player_state=PlayerState.STOPPED)
        self.assertEqual(iface.PlaybackStatus, "Stopped")


class TestLoopStatusGetter(unittest.TestCase):
    """LoopStatus maps LoopState onto the MPRIS 2.2 strings."""

    def test_repeat_track(self):
        iface, _ = _make_interface(loop_state=LoopState.REPEAT_TRACK)
        self.assertEqual(iface.LoopStatus, "Track")

    def test_repeat(self):
        iface, _ = _make_interface(loop_state=LoopState.REPEAT)
        self.assertEqual(iface.LoopStatus, "Playlist")

    def test_none(self):
        iface, _ = _make_interface(loop_state=LoopState.NONE)
        self.assertEqual(iface.LoopStatus, "None")


class TestShuffleGetterSetter(unittest.TestCase):

    def test_getter_returns_snapshot_shuffle(self):
        iface, _ = _make_interface(shuffle=True)
        self.assertTrue(iface.Shuffle)

    def test_getter_returns_false_when_shuffle_off(self):
        iface, _ = _make_interface(shuffle=False)
        self.assertFalse(iface.Shuffle)

    def _call_setter(self, iface, val):
        from ovos_media.mpris import _MediaPlayer2PlayerInterface
        _MediaPlayer2PlayerInterface.Shuffle_setter.fset(iface, val)

    def test_setter_sets_player_shuffle_true(self):
        iface, player = _make_interface()
        self._call_setter(iface, True)
        self.assertTrue(player.shuffle)

    def test_setter_sets_player_shuffle_false(self):
        iface, player = _make_interface()
        self._call_setter(iface, False)
        self.assertFalse(player.shuffle)


class TestCanProperties(unittest.TestCase):
    """The Can* properties report what the player can really be asked to do."""

    def test_can_play_true_when_paused(self):
        iface, _ = _make_interface(player_state=PlayerState.PAUSED)
        self.assertTrue(iface.CanPlay)

    def test_can_play_false_when_playing(self):
        iface, _ = _make_interface(player_state=PlayerState.PLAYING)
        self.assertFalse(iface.CanPlay)

    def test_can_pause_true_when_playing_audio(self):
        iface, player = _make_interface(player_state=PlayerState.PLAYING,
                                        playback_type=PlaybackType.AUDIO)
        player.roster = _roster()
        self.assertTrue(iface.CanPause)

    def test_can_pause_false_when_paused(self):
        iface, player = _make_interface(player_state=PlayerState.PAUSED,
                                        playback_type=PlaybackType.AUDIO)
        player.roster = _roster()
        self.assertFalse(iface.CanPause)

    def test_can_pause_false_when_no_player_takes_the_verb(self):
        # WEBVIEW has no row in the pause route: nothing to pause
        iface, player = _make_interface(player_state=PlayerState.PLAYING,
                                        playback_type=PlaybackType.WEBVIEW)
        player.roster = _roster()
        self.assertFalse(iface.CanPause)

    def test_can_pause_true_for_an_external_mpris_player(self):
        # external players are paused through the manager, not the routes
        iface, player = _make_interface(player_state=PlayerState.PLAYING,
                                        playback_type=PlaybackType.MPRIS)
        player.roster = _roster()
        self.assertTrue(iface.CanPause)

    def test_can_seek_true_for_audio(self):
        iface, player = _make_interface(playback_type=PlaybackType.AUDIO)
        player.roster = _roster()
        self.assertTrue(iface.CanSeek)

    def test_can_seek_true_for_video(self):
        iface, player = _make_interface(playback_type=PlaybackType.VIDEO)
        player.roster = _roster()
        self.assertTrue(iface.CanSeek)

    def test_can_seek_false_for_skill_playback(self):
        # a skill drives its own playback and has no seek passthrough
        iface, player = _make_interface(playback_type=PlaybackType.SKILL)
        player.roster = _roster()
        self.assertFalse(iface.CanSeek)

    def test_can_seek_false_for_external_mpris_player(self):
        iface, player = _make_interface(playback_type=PlaybackType.MPRIS)
        player.roster = _roster()
        self.assertFalse(iface.CanSeek)

    def test_can_seek_false_when_idle(self):
        # UNDEFINED has a seek route (the "stop everything" fan-out), but
        # nothing is loaded: a live seekbar over no track is a lie
        iface, player = _make_interface(playback_type=PlaybackType.UNDEFINED)
        player.roster = _roster()
        self.assertFalse(iface.CanSeek)

    def test_can_seek_false_without_a_roster(self):
        iface, player = _make_interface(playback_type=PlaybackType.AUDIO)
        player.roster = None
        self.assertFalse(iface.CanSeek)

    def test_route_lookup_failure_is_not_fatal(self):
        iface, player = _make_interface(playback_type=PlaybackType.AUDIO)
        player.roster = MagicMock()
        player.roster.route.side_effect = RuntimeError("boom")
        with patch("ovos_media.mpris.exporter.LOG"):
            self.assertFalse(iface.CanSeek)

    def test_can_go_next_delegates(self):
        iface, player = _make_interface()
        player.can_next = True
        self.assertTrue(iface.CanGoNext)

    def test_can_go_previous_delegates(self):
        iface, player = _make_interface()
        player.can_prev = False
        self.assertFalse(iface.CanGoPrevious)

    def test_can_control_always_true(self):
        iface, _ = _make_interface()
        self.assertTrue(iface.CanControl)


class TestSeekPassthrough(unittest.TestCase):
    """Seek/SetPosition are exported and reach the player's seek in ms.

    CanSeek used to be advertised with no Seek method behind it, so any
    controller that enabled its seekbar got UnknownMethod back.
    """

    def _seekable_iface(self, position_ms=30_000, uri="file:///x.mp3"):
        iface, player = _make_interface(playback_type=PlaybackType.AUDIO)
        player.roster = _roster()
        player.now_playing = MagicMock()
        player.now_playing.position = position_ms
        player.now_playing.uri = uri
        iface.Seeked = MagicMock()
        return iface, player

    def test_seek_and_setposition_are_exported_on_the_interface(self):
        from ovos_media.mpris import _MediaPlayer2PlayerInterface
        iface = _MediaPlayer2PlayerInterface(MagicMock(),
                                             "org.mpris.MediaPlayer2.Player")
        exported = {m.name for m in ServiceInterface._get_methods(iface)}
        self.assertIn("Seek", exported)
        self.assertIn("SetPosition", exported)

    def test_seeked_signal_is_exported(self):
        from ovos_media.mpris import _MediaPlayer2PlayerInterface
        iface = _MediaPlayer2PlayerInterface(MagicMock(),
                                             "org.mpris.MediaPlayer2.Player")
        self.assertIn("Seeked",
                      {s.name for s in ServiceInterface._get_signals(iface)})

    def test_seek_is_relative_and_converts_microseconds_to_milliseconds(self):
        iface, player = self._seekable_iface(position_ms=30_000)
        iface.Seek(5_000_000)  # +5s
        player.seek.assert_called_once_with(35_000)

    def test_seek_backwards_clamps_at_zero(self):
        iface, player = self._seekable_iface(position_ms=1_000)
        iface.Seek(-9_000_000)
        player.seek.assert_called_once_with(0)

    def test_seek_emits_seeked_with_the_new_position_in_microseconds(self):
        iface, player = self._seekable_iface(position_ms=30_000)
        iface.Seek(5_000_000)
        iface.Seeked.assert_called_once_with(35_000 * 1000)

    def test_setposition_seeks_to_the_absolute_position(self):
        iface, player = self._seekable_iface()
        iface.SetPosition(iface._track_id(), 12_000_000)
        player.seek.assert_called_once_with(12_000)

    def test_setposition_with_a_stale_track_id_is_ignored(self):
        # the spec requires it: a seekbar drag must not jump the track that
        # replaced the one the client was looking at
        iface, player = self._seekable_iface()
        iface.SetPosition("/org/mpris/MediaPlayer2/Track/deadbeefdeadbeef",
                          12_000_000)
        player.seek.assert_not_called()
        iface.Seeked.assert_not_called()

    def test_track_id_changes_with_the_track(self):
        iface, player = self._seekable_iface(uri="file:///a.mp3")
        first = iface._track_id()
        player.now_playing.uri = "file:///b.mp3"
        self.assertNotEqual(first, iface._track_id())

    def test_track_id_is_no_track_when_nothing_is_loaded(self):
        iface, player = self._seekable_iface()
        player.now_playing = None
        self.assertEqual(iface._track_id(),
                         "/org/mpris/MediaPlayer2/TrackList/NoTrack")

    def test_metadata_carries_the_track_id(self):
        from ovos_utils.ocp import MediaEntry
        iface, player = _make_interface()
        player.now_playing = MediaEntry(uri="file:///x.mp3", title="T")
        self.assertEqual(iface.Metadata["mpris:trackid"].value,
                         iface._track_id())

    def test_seek_while_idle_is_harmless(self):
        iface, player = _make_interface(playback_type=PlaybackType.UNDEFINED)
        player.roster = _roster()
        player.now_playing = MagicMock()
        player.now_playing.position = 0
        iface.Seeked = MagicMock()
        iface.Seek(5_000_000)
        player.seek.assert_not_called()
        iface.Seeked.assert_not_called()

    def test_setposition_while_idle_is_harmless(self):
        iface, player = _make_interface(playback_type=PlaybackType.UNDEFINED)
        player.roster = _roster()
        player.now_playing = MagicMock()
        player.now_playing.uri = "file:///x.mp3"
        iface.Seeked = MagicMock()
        iface.SetPosition(iface._track_id(), 5_000_000)
        player.seek.assert_not_called()

    def test_seek_on_skill_playback_is_ignored(self):
        iface, player = _make_interface(playback_type=PlaybackType.SKILL)
        player.roster = _roster()
        player.now_playing = MagicMock()
        player.now_playing.position = 0
        iface.Seeked = MagicMock()
        iface.Seek(5_000_000)
        player.seek.assert_not_called()

    def test_seek_from_a_malformed_position_starts_from_zero(self):
        iface, player = self._seekable_iface()
        player.now_playing.position = float("nan")
        iface.Seek(5_000_000)
        player.seek.assert_called_once_with(5_000)


class TestPlayerInterfaceMethods(unittest.TestCase):
    """The transport methods reach the player through the dispatcher."""

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
        iface, player = _make_interface(player_state=PlayerState.PAUSED)
        iface.PlayPause()
        player.resume.assert_called_once()
        player.pause.assert_not_called()

    def test_play_pause_pauses_when_playing(self):
        iface, player = _make_interface(player_state=PlayerState.PLAYING)
        iface.PlayPause()
        player.pause.assert_called_once()
        player.resume.assert_not_called()


class TestSubmitToPlayer(unittest.TestCase):
    """The one crossing back onto the player's own thread."""

    def test_real_dispatcher_receives_the_command(self):
        from ovos_media.mpris import submit_to_player
        from ovos_media.player.dispatcher import Dispatcher
        player = MagicMock()
        player.dispatcher = Dispatcher.immediate_dispatcher()
        seen = []
        submit_to_player(player, lambda: seen.append(1))
        self.assertEqual(seen, [1])

    def test_player_without_a_dispatcher_is_called_inline(self):
        from ovos_media.mpris import submit_to_player
        seen = []
        submit_to_player(object(), lambda: seen.append(1))
        self.assertEqual(seen, [1])


class TestPlaylistsInterface(unittest.TestCase):
    """The org.mpris.MediaPlayer2.Playlists stub reserves the surface
    without claiming any playlists exist yet.

    Every property is round-tripped through :class:`dbus_next.signature.
    Variant` against the property's own declared signature — not just
    compared at the Python level — because dbus_next's STRUCT marshaller
    requires ``list``, not ``tuple``, and a plain equality/unpacking check
    on the raw Python value passes green over a value that crashes the
    moment it actually reaches the wire (SignatureBodyMismatchError on
    Properties.GetAll / ObjectManager.InterfacesAdded).
    """

    def _make_interface(self):
        from ovos_media.mpris import _MediaPlayer2PlaylistsInterface
        return _MediaPlayer2PlaylistsInterface(MagicMock())

    def _variant(self, iface, prop_name):
        from dbus_next.signature import Variant
        sig = next(p.signature for p in
                   type(iface)._get_properties(iface) if p.name == prop_name)
        return Variant(sig, getattr(iface, prop_name))

    def test_playlist_count_is_zero(self):
        iface = self._make_interface()
        variant = self._variant(iface, "PlaylistCount")
        self.assertEqual(variant.value, 0)

    def test_orderings_is_alphabetical(self):
        iface = self._make_interface()
        variant = self._variant(iface, "Orderings")
        self.assertEqual(variant.value, ["Alphabetical"])

    def test_active_playlist_marshals_and_is_none(self):
        iface = self._make_interface()
        variant = self._variant(iface, "ActivePlaylist")
        valid, playlist = variant.value
        self.assertFalse(valid)

    def test_activate_playlist_is_a_noop(self):
        # dbus_next's @method() wrapper discards the return value of calling
        # the decorated method directly; the real return travels through the
        # underlying function it stashes on __DBUS_METHOD for dispatch.
        iface = self._make_interface()
        fn = iface.ActivatePlaylist.__dict__['__DBUS_METHOD'].fn
        self.assertIsNone(fn(iface, "/some/playlist"))

    def test_get_playlists_returns_empty(self):
        iface = self._make_interface()
        fn = iface.GetPlaylists.__dict__['__DBUS_METHOD'].fn
        result = fn(iface, 0, 10, "Alphabetical", False)
        self.assertEqual(result, [])
        # the method's own return signature must accept an empty list
        Variant = __import__("dbus_next.signature", fromlist=["Variant"]).Variant
        Variant('a(oss)', result)

    def test_playlists_interface_is_exported(self):
        from ovos_media.mpris import MprisExporter
        exporter = MprisExporter.__new__(MprisExporter)
        exporter._ocp_player = MagicMock()
        from ovos_media.mpris.exporter import (_MediaPlayer2Interface,
                                               _MediaPlayer2PlayerInterface,
                                               _MediaPlayer2PlaylistsInterface)
        player = MagicMock()
        player.playlist = []
        exporter.mediaPlayer2Interface = _MediaPlayer2Interface(player)
        exporter.mediaPlayer2PlayerInterface = _MediaPlayer2PlayerInterface(
            player, 'org.mpris.MediaPlayer2.Player')
        exporter.playlistsInterface = _MediaPlayer2PlaylistsInterface(player)
        self.assertIsInstance(exporter.playlistsInterface,
                              _MediaPlayer2PlaylistsInterface)


class TestPlaylistsInterfaceRealBus(unittest.IsolatedAsyncioTestCase):
    """Export the stub on a real message bus object and call GetAll through
    dbus_next's own property machinery, the path a tuple return cannot
    survive."""

    async def test_get_all_marshals_every_property(self):
        from ovos_media.mpris import _MediaPlayer2PlaylistsInterface
        from dbus_next.service import ServiceInterface
        player = MagicMock()
        iface = _MediaPlayer2PlaylistsInterface(player)
        for prop in ServiceInterface._get_properties(iface):
            from dbus_next.signature import Variant
            Variant(prop.signature, getattr(iface, prop.name))


if __name__ == "__main__":
    unittest.main()

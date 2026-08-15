"""Tests for OcpMprisExporter._create_player_handler's on_properties_changed
closure when a signal arrives before player_meta has been populated for that
player (the window between subscribing and the first query_player call).
"""
import unittest
from unittest.mock import MagicMock, AsyncMock
from threading import Event

from ovos_utils.ocp import LoopState


def _make_exporter(config=None):
    """Return an OcpMprisExporter with a mocked player (no thread started)."""
    from ovos_media.mpris import OcpMprisExporter
    from unittest.mock import patch
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
        for attr in ("shutdown_event", "stop_event", "pause_event",
                     "resume_event", "next_event", "prev_event",
                     "shuffle_event", "repeat_event"):
            setattr(ctl, attr, Event())
    return ctl, player


def _register_handler(ctl, name):
    """Build the player_handler closure and return the registered callback."""
    iface = MagicMock()
    iface.bus_name = name
    proxy = MagicMock()
    proxy.get_interface = MagicMock(return_value=iface)
    ctl.players[name] = proxy
    ctl._create_player_handler(name)
    callback = iface.on_properties_changed.call_args[0][0]
    return callback


class _Variant:
    def __init__(self, value):
        self.value = value


class TestPropertiesChangedBeforeMetaExists(unittest.IsolatedAsyncioTestCase):
    """Signal arriving before player_meta[name] exists must not raise."""

    async def test_playback_status_creates_meta_without_keyerror(self):
        ctl, _ = _make_exporter()
        name = "org.mpris.MediaPlayer2.mpv"
        callback = _register_handler(ctl, name)
        self.assertNotIn(name, ctl.player_meta)

        await callback("org.mpris.MediaPlayer2.Player",
                        {"PlaybackStatus": _Variant("Playing")}, [])

        self.assertIn(name, ctl.player_meta)
        self.assertEqual(ctl.player_meta[name]["state"], "Playing")

    async def test_shuffle_creates_meta_without_keyerror(self):
        ctl, _ = _make_exporter()
        name = "org.mpris.MediaPlayer2.mpv"
        callback = _register_handler(ctl, name)
        self.assertNotIn(name, ctl.player_meta)

        await callback("org.mpris.MediaPlayer2.Player",
                        {"Shuffle": _Variant(True)}, [])

        self.assertIn(name, ctl.player_meta)
        self.assertEqual(ctl.player_meta[name]["shuffle"], True)

    async def test_loop_status_creates_meta_without_keyerror(self):
        ctl, _ = _make_exporter()
        name = "org.mpris.MediaPlayer2.mpv"
        callback = _register_handler(ctl, name)
        self.assertNotIn(name, ctl.player_meta)

        await callback("org.mpris.MediaPlayer2.Player",
                        {"LoopStatus": _Variant("Track")}, [])

        self.assertIn(name, ctl.player_meta)
        self.assertEqual(ctl.player_meta[name]["loop_state"],
                          LoopState.REPEAT_TRACK)

    async def test_playback_status_updates_existing_meta_in_place(self):
        ctl, _ = _make_exporter()
        name = "org.mpris.MediaPlayer2.mpv"
        ctl.player_meta[name] = {"state": "Stopped"}
        callback = _register_handler(ctl, name)

        await callback("org.mpris.MediaPlayer2.Player",
                        {"PlaybackStatus": _Variant("Playing")}, [])

        self.assertEqual(ctl.player_meta[name]["state"], "Playing")


class TestOcpUpdateWithManagedPlayersAndMissingMeta(unittest.IsolatedAsyncioTestCase):
    """With manage_external_players enabled, a PlaybackStatus signal for an
    unknown player must reach _update_ocp without raising, and the reflected
    metadata must carry a usable skill_id.
    """

    async def test_playback_status_before_metadata_completes_ocp_update(self):
        ctl, player = _make_exporter({"manage_external_players": True})
        name = "org.mpris.MediaPlayer2.mpv"
        callback = _register_handler(ctl, name)
        self.assertNotIn(name, ctl.player_meta)

        # unknown player -> no crash reaching _update_ocp, half-mutated OCP
        # state must still finish with a real skill_id
        await callback("org.mpris.MediaPlayer2.Player",
                        {"PlaybackStatus": _Variant("Playing")}, [])

        self.assertEqual(ctl.main_player, name)
        self.assertIn(name, ctl.player_meta)
        self.assertEqual(ctl.player_meta[name]["skill_id"], name)
        player.set_now_playing.assert_called()

    async def test_playback_status_with_full_metadata_flows_unchanged(self):
        ctl, player = _make_exporter({"manage_external_players": True})
        name = "org.mpris.MediaPlayer2.mpv"
        ctl.player_meta[name] = {
            "external_player": name,
            "state": "Stopped",
            "title": "Song",
            "artist": "Band",
            "uri": "mpris://mpv",
        }
        callback = _register_handler(ctl, name)

        await callback("org.mpris.MediaPlayer2.Player",
                        {"PlaybackStatus": _Variant("Playing")}, [])

        self.assertEqual(ctl.player_meta[name]["skill_id"], name)
        self.assertEqual(ctl.player_meta[name]["title"], "Song")
        player.set_now_playing.assert_called()

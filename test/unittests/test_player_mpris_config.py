"""Regression test: OCPMediaPlayer must forward its media config block to
OcpMprisExporter, so config keys like mpris_poll_interval, ignored_players
and dbus_type actually reach the exporter instead of being dead config.
"""
import unittest
from unittest.mock import MagicMock, patch

from ovos_utils.fakebus import FakeBus


class TestMprisConfigForwarded(unittest.TestCase):
    """__init__ must pass config=self.ocp_config to OcpMprisExporter."""

    def _make_player(self, media_config):
        from ovos_media.player import OCPMediaPlayer
        with patch("ovos_media.player.AudioService"), \
             patch("ovos_media.player.VideoService"), \
             patch("ovos_media.player.WebService"), \
             patch("ovos_media.player.OcpMprisExporter") as mock_exporter, \
             patch("ovos_media.player.GUIInterface"), \
             patch("ovos_media.player.NowPlaying"), \
             patch("ovos_media.player.Playlist"), \
             patch("ovos_media.player.OCPMediaCatalog"), \
             patch.object(OCPMediaPlayer, "register_bus_handlers"):
            p = OCPMediaPlayer(bus=FakeBus(), config=media_config)
        return p, mock_exporter

    def test_custom_poll_interval_reaches_exporter_config(self):
        media_config = {"enable_mpris": True,
                        "manage_external_players": True,
                        "mpris_poll_interval": 42}
        p, mock_exporter = self._make_player(media_config)

        mock_exporter.assert_called_once()
        _, kwargs = mock_exporter.call_args
        self.assertIn("config", kwargs)
        self.assertEqual(kwargs["config"].get("mpris_poll_interval"), 42)
        self.assertIs(kwargs["config"], p.ocp_config)

    def test_ignored_players_and_dbus_type_reach_exporter_config(self):
        media_config = {"enable_mpris": True,
                        "ignored_players": ["org.mpris.MediaPlayer2.foo"],
                        "dbus_type": "system"}
        p, mock_exporter = self._make_player(media_config)

        _, kwargs = mock_exporter.call_args
        self.assertEqual(kwargs["config"].get("ignored_players"),
                         ["org.mpris.MediaPlayer2.foo"])
        self.assertEqual(kwargs["config"].get("dbus_type"), "system")

    def test_mpris_disabled_does_not_construct_exporter(self):
        media_config = {"enable_mpris": False}
        p, mock_exporter = self._make_player(media_config)
        mock_exporter.assert_not_called()


if __name__ == "__main__":
    unittest.main()

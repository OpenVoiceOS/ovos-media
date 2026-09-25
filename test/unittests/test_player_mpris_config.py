"""Regression test: OCPMediaPlayer must forward its media config block to
OcpMprisExporter, so config keys like mpris_poll_interval, ignored_players
and dbus_type actually reach the exporter instead of being dead config.
"""
import unittest
from unittest.mock import MagicMock, patch

from ovos_utils.fakebus import FakeBus


class TestMprisConfigForwarded(unittest.TestCase):
    """__init__ must pass config=self.ocp_config to OcpMprisExporter."""

    def _make_player(self, media_config, bypass_test_default=False):
        from ovos_media.player import OCPMediaPlayer
        init = (OCPMediaPlayer._unpatched_init if bypass_test_default
                else OCPMediaPlayer.__init__)
        with patch("ovos_media.player.AudioService"), \
             patch("ovos_media.player.VideoService"), \
             patch("ovos_media.player.WebService"), \
             patch("ovos_media.player.OcpMprisExporter") as mock_exporter, \
                 patch("ovos_media.player.NowPlaying"), \
             patch("ovos_media.player.Playlist"), \
             patch("ovos_media.player.OCPMediaCatalog"), \
             patch("ovos_media.player.OCPBusApi"), \
             patch.object(OCPMediaPlayer, "_report_to_core"), \
             patch.object(OCPMediaPlayer, "__init__", init):
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

    def test_mpris_defaults_to_enabled(self):
        # ovos-media is a desktop MPRIS player unless configured off; a
        # headless install with no session bus still degrades to a single
        # warning, so defaulting the exporter on is safe. This bypasses the
        # suite-wide test default (see test/unittests/conftest.py) to check
        # the production default the source itself resolves, with the
        # exporter still mocked out so nothing touches a real bus.
        # a non-empty dict without the key: an empty dict is falsy and
        # would fall through to Configuration(), which the suite's own
        # test/conftest.py pins to enable_mpris=False for exactly the
        # reason this test bypasses the per-test default above.
        media_config = {"manage_external_players": False}
        p, mock_exporter = self._make_player(media_config, bypass_test_default=True)
        mock_exporter.assert_called_once()

    def test_the_suite_default_turns_mpris_off_when_the_key_is_absent(self):
        # The guard for the autouse fixture in test/unittests/conftest.py.
        # Every other test here names enable_mpris, so none of them reaches
        # the fixture's setdefault. Delete that line and they all still
        # pass, which leaves the fixture's whole reason for being autouse
        # unmeasured: a config without the key must come up with MPRIS off,
        # or a unit test claims org.mpris.MediaPlayer2.OCP on whatever
        # session bus the runner has. The line above asserts the opposite
        # production default through _unpatched_init, so the pair reads
        # together.
        media_config = {"manage_external_players": False}
        p, mock_exporter = self._make_player(media_config)
        mock_exporter.assert_not_called()
        # assertIs rather than assertFalse with .get(): a missing key is also
        # falsy, so the weaker form passed whether the fixture set the key to
        # False or never set it at all. The exporter line above is what
        # catches the fixture going away, so this one is free to assert the
        # value itself.
        self.assertIs(p.ocp_config["enable_mpris"], False)


if __name__ == "__main__":
    unittest.main()

"""Tests for MediaService lifecycle and bus message handlers."""
import unittest
from unittest.mock import MagicMock, patch, call


class TestMediaServiceLifecycle(unittest.TestCase):
    """MediaService init, ready/stopping hooks, and thread lifecycle."""

    def _make_service(self):
        from ovos_media.service import MediaService
        with patch("ovos_media.service.MessageBusClient"), \
             patch("ovos_media.service.OCPMediaPlayer") as MockPlayer, \
             patch("ovos_media.service.ProcessStatus") as MockStatus, \
             patch("ovos_media.service.Configuration", return_value={"media": {}}):
            MockStatus.return_value = MagicMock()
            bus = MagicMock()
            svc = MediaService(bus=bus)
        return svc

    def test_status_set_started_on_init(self):
        svc = self._make_service()
        svc.status.set_started.assert_called_once()

    def test_status_set_alive_on_init(self):
        svc = self._make_service()
        svc.status.set_alive.assert_called_once()

    def test_run_sets_ready(self):
        svc = self._make_service()
        svc.run()
        svc.status.set_ready.assert_called_once()

    def test_shutdown_calls_reset_and_stopping(self):
        svc = self._make_service()
        svc.ocp = MagicMock()
        svc.shutdown()
        svc.ocp.reset.assert_called_once()
        svc.status.set_stopping.assert_called_once()
        svc.ocp.shutdown.assert_called_once()


class TestMediaServiceHandlers(unittest.TestCase):
    """Bus message handler methods."""

    def _make_service(self):
        from ovos_media.service import MediaService
        with patch("ovos_media.service.MessageBusClient"), \
             patch("ovos_media.service.OCPMediaPlayer") as MockPlayer, \
             patch("ovos_media.service.ProcessStatus") as MockStatus, \
             patch("ovos_media.service.Configuration", return_value={"media": {}}):
            MockStatus.return_value = MagicMock()
            bus = MagicMock()
            svc = MediaService(bus=bus)
        return svc

    def test_handle_ping_emits_pong(self):
        svc = self._make_service()
        msg = MagicMock()
        msg.reply.return_value = "pong_msg"
        svc.handle_ping(msg)
        msg.reply.assert_called_once_with("ovos.common_play.pong")
        svc.bus.emit.assert_called_with("pong_msg")

    def test_handle_home_calls_manage_display(self):
        svc = self._make_service()
        from ovos_media.gui import OCPGUIState
        svc.ocp = MagicMock()
        svc.handle_home(MagicMock())
        svc.ocp.gui.manage_display.assert_called_once_with(OCPGUIState.HOME)

    def test_handle_search_start_shows_spinner(self):
        svc = self._make_service()
        from ovos_media.gui import OCPGUIState
        svc.ocp = MagicMock()
        svc.handle_search_start(MagicMock())
        svc.ocp.gui.manage_display.assert_called_once_with(OCPGUIState.SPINNER)

    def test_handle_search_end_does_not_raise(self):
        svc = self._make_service()
        # Should not raise — may log a warning
        try:
            svc.handle_search_end(MagicMock())
        except Exception as e:
            self.fail(f"handle_search_end raised {e}")


if __name__ == "__main__":
    unittest.main()

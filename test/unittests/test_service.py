"""Tests for MediaService lifecycle and bus message handlers."""
import unittest
from unittest.mock import MagicMock, patch


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

    def test_no_home_or_search_end_handler_methods(self):
        """MediaService does not implement handle_home or handle_search_end
        at all — 'ovos.common_play.home'/'.search.start'/'.search.end' are
        pipeline-side signals with nothing for this daemon to do in
        response; see test_autoplay_and_search_gating.py::
        TestPipelineSideSignalsAreNotHandled for the bus-level no-op proof
        (home must not disturb playback in progress)."""
        svc = self._make_service()
        self.assertFalse(hasattr(svc, "handle_home"))
        self.assertFalse(hasattr(svc, "handle_search_end"))


class TestServiceModuleFunctions(unittest.TestCase):
    """Test module-level status callback functions."""

    def test_on_ready_does_not_raise(self):
        from ovos_media.service import on_ready
        on_ready()  # should not raise

    def test_on_alive_does_not_raise(self):
        from ovos_media.service import on_alive
        on_alive()  # should not raise

    def test_on_started_does_not_raise(self):
        from ovos_media.service import on_started
        on_started()  # should not raise

    def test_on_error_does_not_raise(self):
        from ovos_media.service import on_error
        on_error()  # should not raise

    def test_on_error_with_message_does_not_raise(self):
        from ovos_media.service import on_error
        on_error("Test error")  # should not raise

    def test_on_stopping_does_not_raise(self):
        from ovos_media.service import on_stopping
        on_stopping()  # should not raise


class TestMediaServiceBusInitialization(unittest.TestCase):
    """Test MediaService initialization when bus=None."""

    def test_service_creates_message_bus_client_when_bus_is_none(self):
        """When bus=None, MediaService should create MessageBusClient and call run_in_thread()."""
        from ovos_media.service import MediaService

        with patch("ovos_media.service.MessageBusClient") as mock_mbc_class, \
             patch("ovos_media.service.OCPMediaPlayer"), \
             patch("ovos_media.service.ProcessStatus") as mock_status, \
             patch("ovos_media.service.Configuration", return_value={"media": {}}):
            mock_mbc_instance = MagicMock()
            mock_mbc_class.return_value = mock_mbc_instance
            mock_status.return_value = MagicMock()

            # Create service with bus=None
            svc = MediaService(bus=None)

            # Verify MessageBusClient was instantiated
            mock_mbc_class.assert_called_once()

            # Verify run_in_thread was called on the bus instance
            mock_mbc_instance.run_in_thread.assert_called_once()

            # Verify bus is set to the created instance
            self.assertIs(svc.bus, mock_mbc_instance)

    def test_service_uses_provided_bus_when_given(self):
        """When bus is provided, MediaService should use it without creating a new one."""
        from ovos_media.service import MediaService

        with patch("ovos_media.service.MessageBusClient") as mock_mbc_class, \
             patch("ovos_media.service.OCPMediaPlayer"), \
             patch("ovos_media.service.ProcessStatus") as mock_status, \
             patch("ovos_media.service.Configuration", return_value={"media": {}}):
            mock_status.return_value = MagicMock()
            provided_bus = MagicMock()

            # Create service with a provided bus
            svc = MediaService(bus=provided_bus)

            # Verify MessageBusClient was NOT called
            mock_mbc_class.assert_not_called()

            # Verify the service uses the provided bus
            self.assertIs(svc.bus, provided_bus)


class TestMediaServiceProcessStatusNamespace(unittest.TestCase):
    """MediaService must claim its own 'media' process-status identity,
    not squat ovos-audio's 'audio' one; two daemons sharing a bus would
    otherwise both answer mycroft.audio.is_ready."""

    def _make_service_with_real_status(self):
        from ovos_utils.fakebus import FakeBus
        from ovos_media.service import MediaService

        bus = FakeBus()
        with patch("ovos_media.service.MessageBusClient"), \
             patch("ovos_media.service.OCPMediaPlayer"), \
             patch("ovos_media.service.Configuration", return_value={"media": {}}):
            svc = MediaService(bus=bus)
        return svc, bus

    def test_responds_on_media_is_ready(self):
        from ovos_bus_client.message import Message

        svc, bus = self._make_service_with_real_status()
        svc.status.set_ready()

        responses = []
        bus.on("mycroft.media.is_ready.response",
               lambda m: responses.append(m))
        bus.emit(Message("mycroft.media.is_ready"))

        self.assertEqual(len(responses), 1)
        self.assertTrue(responses[0].data["status"])

    def test_does_not_respond_on_audio_is_ready(self):
        from ovos_bus_client.message import Message

        svc, bus = self._make_service_with_real_status()
        svc.status.set_ready()

        responses = []
        bus.on("mycroft.audio.is_ready.response",
               lambda m: responses.append(m))
        bus.emit(Message("mycroft.audio.is_ready"))

        self.assertEqual(len(responses), 0)

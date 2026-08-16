"""Coverage tests for ovos_media/service.py.

Targets module-level callback functions and bus initialization path.
"""
import unittest
from unittest.mock import MagicMock, patch


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


if __name__ == "__main__":
    unittest.main()

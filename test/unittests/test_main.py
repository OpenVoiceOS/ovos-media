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
#
"""Unit tests for ovos_media.__main__.main()."""
import sys
import unittest
from unittest.mock import patch, MagicMock, call


class TestMainStartsService(unittest.TestCase):
    """main() must create a MediaService, start it, and shut it down.

    argv defaults to None -> sys.argv[1:] (so the real installed console
    script actually parses its own CLI args). These tests call main() with
    no explicit argv, so sys.argv is patched to a bare argv (no flags) for
    the duration of the test class to avoid leaking the test runner's own
    argv into argparse."""

    def setUp(self):
        self._argv_patcher = patch.object(sys, "argv", ["ovos-media"])
        self._argv_patcher.start()
        self.addCleanup(self._argv_patcher.stop)

    def _run_main(self, ready_hook=None, error_hook=None, stopping_hook=None,
                  watchdog=None):
        """Run main() with all heavy-weight side-effects mocked out."""
        kwargs = {}
        if ready_hook is not None:
            kwargs["ready_hook"] = ready_hook
        if error_hook is not None:
            kwargs["error_hook"] = error_hook
        if stopping_hook is not None:
            kwargs["stopping_hook"] = stopping_hook
        if watchdog is not None:
            kwargs["watchdog"] = watchdog

        with patch("ovos_media.__main__.reset_sigint_handler"), \
             patch("ovos_media.__main__.init_service_logger"), \
             patch("ovos_media.__main__.LOG"), \
             patch("ovos_media.__main__.setup_locale"), \
             patch("ovos_media.__main__.wait_for_exit_signal"), \
             patch("ovos_media.__main__.MediaService") as mock_svc_cls:
            mock_instance = MagicMock()
            mock_svc_cls.return_value = mock_instance
            from ovos_media.__main__ import main
            main(**kwargs)
            return mock_svc_cls, mock_instance

    def test_service_is_created(self):
        mock_cls, mock_instance = self._run_main()
        mock_cls.assert_called_once()

    def test_service_daemon_set_true(self):
        _, mock_instance = self._run_main()
        self.assertTrue(mock_instance.daemon)

    def test_service_start_called(self):
        _, mock_instance = self._run_main()
        mock_instance.start.assert_called_once()

    def test_service_shutdown_called(self):
        _, mock_instance = self._run_main()
        mock_instance.shutdown.assert_called_once()

    def test_custom_hooks_forwarded_to_service(self):
        ready = MagicMock()
        error = MagicMock()
        stopping = MagicMock()
        watchdog = MagicMock()

        mock_cls, _ = self._run_main(
            ready_hook=ready,
            error_hook=error,
            stopping_hook=stopping,
            watchdog=watchdog,
        )
        _, kwargs = mock_cls.call_args
        self.assertEqual(kwargs.get("ready_hook"), ready)
        self.assertEqual(kwargs.get("error_hook"), error)
        self.assertEqual(kwargs.get("stopping_hook"), stopping)
        self.assertEqual(kwargs.get("watchdog"), watchdog)

    def test_reset_sigint_called(self):
        with patch("ovos_media.__main__.reset_sigint_handler") as mock_reset, \
             patch("ovos_media.__main__.init_service_logger"), \
             patch("ovos_media.__main__.LOG"), \
             patch("ovos_media.__main__.setup_locale"), \
             patch("ovos_media.__main__.wait_for_exit_signal"), \
             patch("ovos_media.__main__.MediaService"):
            from ovos_media.__main__ import main
            main()
            mock_reset.assert_called_once()

    def test_setup_locale_called(self):
        with patch("ovos_media.__main__.reset_sigint_handler"), \
             patch("ovos_media.__main__.init_service_logger"), \
             patch("ovos_media.__main__.LOG"), \
             patch("ovos_media.__main__.setup_locale") as mock_locale, \
             patch("ovos_media.__main__.wait_for_exit_signal"), \
             patch("ovos_media.__main__.MediaService"):
            from ovos_media.__main__ import main
            main()
            mock_locale.assert_called_once()

    def test_wait_for_exit_signal_called(self):
        with patch("ovos_media.__main__.reset_sigint_handler"), \
             patch("ovos_media.__main__.init_service_logger"), \
             patch("ovos_media.__main__.LOG"), \
             patch("ovos_media.__main__.setup_locale"), \
             patch("ovos_media.__main__.wait_for_exit_signal") as mock_wait, \
             patch("ovos_media.__main__.MediaService"):
            from ovos_media.__main__ import main
            main()
            mock_wait.assert_called_once()


if __name__ == "__main__":
    unittest.main()

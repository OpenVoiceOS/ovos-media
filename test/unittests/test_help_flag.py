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
"""ovos_media.__main__ --help/--version must short-circuit before any
service or socket is touched (Quick-win #1)."""
import subprocess
import sys
import unittest
from unittest.mock import patch, MagicMock


class TestHelpFlagInProcess(unittest.TestCase):
    """Fast in-process checks: --help/--version raise SystemExit(0) and
    never construct a MediaService."""

    def test_help_exits_zero_without_starting_service(self):
        with patch("ovos_media.__main__.MediaService") as mock_svc_cls, \
             patch("ovos_media.__main__.wait_for_exit_signal") as mock_wait:
            from ovos_media.__main__ import main
            with self.assertRaises(SystemExit) as ctx:
                main(argv=["--help"])
            self.assertEqual(ctx.exception.code, 0)
            mock_svc_cls.assert_not_called()
            mock_wait.assert_not_called()

    def test_version_exits_zero_without_starting_service(self):
        with patch("ovos_media.__main__.MediaService") as mock_svc_cls:
            from ovos_media.__main__ import main
            with self.assertRaises(SystemExit) as ctx:
                main(argv=["--version"])
            self.assertEqual(ctx.exception.code, 0)
            mock_svc_cls.assert_not_called()

    def test_unknown_flag_exits_nonzero_without_starting_service(self):
        with patch("ovos_media.__main__.MediaService") as mock_svc_cls:
            from ovos_media.__main__ import main
            with self.assertRaises(SystemExit) as ctx:
                main(argv=["--bogus-flag"])
            self.assertNotEqual(ctx.exception.code, 0)
            mock_svc_cls.assert_not_called()

    def test_no_args_still_starts_service(self):
        """Regression guard: adding the parser must not break normal
        daemon startup (argv defaults to empty, not the test runner's
        real sys.argv)."""
        with patch("ovos_media.__main__.reset_sigint_handler"), \
             patch("ovos_media.__main__.init_service_logger"), \
             patch("ovos_media.__main__.LOG"), \
             patch("ovos_media.__main__.setup_locale"), \
             patch("ovos_media.__main__.wait_for_exit_signal"), \
             patch("ovos_media.__main__.MediaService") as mock_svc_cls:
            mock_svc_cls.return_value = MagicMock()
            from ovos_media.__main__ import main
            main()
            mock_svc_cls.assert_called_once()


class TestHelpFlagSubprocess(unittest.TestCase):
    """End-to-end: run the real entrypoint in a subprocess. This is the
    behavioral proof — it exercises the real sys.argv path and confirms no
    bus socket is bound (the process exits almost instantly instead of
    blocking in wait_for_exit_signal)."""

    def test_help_subprocess_exits_zero_and_prints_usage(self):
        proc = subprocess.run(
            [sys.executable, "-m", "ovos_media", "--help"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("usage:", (proc.stdout + proc.stderr).lower())

    def test_version_subprocess_exits_zero_and_prints_version(self):
        from ovos_media.version import __version__
        proc = subprocess.run(
            [sys.executable, "-m", "ovos_media", "--version"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn(__version__, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()

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
service or socket is touched (Quick-win #1).

The `ovos-media` console script (setuptools entry point
`ovos_media.__main__:main`) invokes `main()` with NO arguments, so it only
ever exercises the function's *default* argv handling — never a value
supplied by a test. Any test that calls `main(argv=[...])` explicitly is
exercising a path the installed script never takes, and would stay green
even if the default silently ignored real process args (the exact bug this
module guards against). The behavioral proof therefore MUST invoke the
actual installed script via its real entry point, not `python -m
ovos_media` (the module path bypasses the console-script argv wiring
entirely) and not `main(argv=...)` with an explicit list."""
import subprocess
import sys
import unittest
from pathlib import Path
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
        """Regression guard: the default argv=None path (what the real
        console script uses) must still start the daemon when the actual
        process argv is empty."""
        with patch("ovos_media.__main__.reset_sigint_handler"), \
             patch("ovos_media.__main__.init_service_logger"), \
             patch("ovos_media.__main__.LOG"), \
             patch("ovos_media.__main__.setup_locale"), \
             patch("ovos_media.__main__.wait_for_exit_signal"), \
             patch("ovos_media.__main__.MediaService") as mock_svc_cls, \
             patch.object(sys, "argv", ["ovos-media"]):
            mock_svc_cls.return_value = MagicMock()
            from ovos_media.__main__ import main
            main()
            mock_svc_cls.assert_called_once()


class TestHelpFlagSubprocess(unittest.TestCase):
    """End-to-end: run the real INSTALLED `ovos-media` console script (not
    `python -m ovos_media`, which never exercises the entry-point's own
    argv wiring). This is the behavioral proof that
    `ovos-media --help`/`--version` exit fast without binding a bus socket,
    and that `ovos-media --bogus` is rejected instead of silently booting
    the daemon."""

    @classmethod
    def setUpClass(cls):
        # NOTE: shutil.which("ovos-media") resolves against the FULL ambient
        # PATH, so it can certify whatever "ovos-media" console script
        # happens to be first on PATH — e.g. a stale script installed in an
        # unrelated shared venv — instead of the one this test run just
        # installed into sys.executable's venv. That silently validated the
        # wrong binary and produced false test failures. The console script
        # is always installed as a sibling of the interpreter that installed
        # it, so resolve it relative to sys.executable instead of trusting
        # ambient PATH.
        candidate = Path(sys.executable).parent / "ovos-media"
        cls.exe = str(candidate) if candidate.is_file() else None
        if not cls.exe:
            raise unittest.SkipTest(
                f"ovos-media console script not found at {candidate} — "
                "package must be installed (uv pip install -e .) into "
                "this interpreter's venv, not just importable, for this "
                "test to exercise the real entry point")

    def test_help_subprocess_exits_zero_and_prints_usage(self):
        proc = subprocess.run(
            [self.exe, "--help"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("usage:", (proc.stdout + proc.stderr).lower())

    def test_version_subprocess_exits_zero_and_prints_version(self):
        from ovos_media.version import __version__
        proc = subprocess.run(
            [self.exe, "--version"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn(__version__, proc.stdout + proc.stderr)

    def test_bogus_flag_subprocess_exits_nonzero_and_does_not_hang(self):
        proc = subprocess.run(
            [self.exe, "--bogus-flag-that-does-not-exist"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("error", (proc.stdout + proc.stderr).lower())


if __name__ == "__main__":
    unittest.main()

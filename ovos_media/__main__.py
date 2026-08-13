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
import argparse
import sys

from ovos_utils import wait_for_exit_signal
from ovos_utils.log import init_service_logger, LOG
from ovos_utils.process_utils import reset_sigint_handler

from ovos_config.locale import setup_locale
from ovos_media.service import MediaService, on_ready, on_error, on_stopping
from ovos_media.version import __version__


def main(ready_hook=on_ready, error_hook=on_error, stopping_hook=on_stopping,
         watchdog=lambda: None, argv=None):
    """Start the Media Service and connect to the Message Bus

    @param argv: CLI arguments to parse. Defaults to None, which means
        "parse the real process argv" (sys.argv[1:]) — this is what makes
        the installed `ovos-media` console script (entry point:
        ovos_media.__main__:main) actually parse --help/--version/unknown
        flags instead of silently booting the daemon. Pass an explicit
        list (e.g. []) to override, e.g. from tests that must not leak the
        test runner's own argv in here.
    """
    parser = argparse.ArgumentParser(prog="ovos-media",
                                     description="OCP-native audio/video/web "
                                                 "media service for OpenVoiceOS")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    # parse_args exits the process (SystemExit) on --help/--version/bad args
    # before any service/socket is touched, matching every other OVOS daemon
    parser.parse_args(sys.argv[1:] if argv is None else list(argv))

    reset_sigint_handler()
    init_service_logger("media")
    LOG.set_level("DEBUG")
    setup_locale()
    service = MediaService(ready_hook=ready_hook, error_hook=error_hook,
                           stopping_hook=stopping_hook, watchdog=watchdog)
    service.daemon = True
    service.start()
    wait_for_exit_signal()
    service.shutdown()


if __name__ == '__main__':
    main(argv=sys.argv[1:])

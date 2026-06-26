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

from functools import wraps

from ovos_config import Configuration
from ovos_bus_client.session import SessionManager
from ovos_utils.log import LOG


def require_default_session():
    """Decorator gating a bus handler to the local/"default" session only.

    ovos-media is conceptually a *single* player bound to its own device — the
    ``"default"`` session.  In a HiveMind split the OCP pipeline runs on the
    server and forwards playback commands stamped with the *originating*
    session.  A server-side ovos-media must NOT act on a satellite's command
    (``session_id != "default"``): the satellite has its own embedded
    ovos-media that handles it.  hivemind-core NATs the satellite's session to
    ``"default"`` for that embedded instance (or the satellite sets
    ``validate_source: false``), so the satellite's instance sees ``"default"``
    and executes.

    Mirrors :func:`ovos_audio.utils.require_default_session`.

    A decorated handler runs only if any of:
        - ``message`` is ``None`` (internal/synthetic call), OR
        - the owning object's ``validate_source`` is falsy (act on everything), OR
        - the message's session id is ``"default"`` (local request).

    Otherwise it logs at debug level and returns ``None`` without acting.

    The decorated method's ``self`` must expose a ``validate_source``
    attribute; if missing it defaults to ``True`` (filter enabled).
    """

    def _decorator(func):
        @wraps(func)
        def func_wrapper(self, message=None):
            validate = getattr(self, "validate_source", True)
            validated = message is None or \
                        not validate or \
                        SessionManager.get(message).session_id == "default"
            if validated:
                return func(self, message)
            LOG.debug(f"ignoring '{message.msg_type}' message, "
                      f"not from the default/local session")
            return None

        return func_wrapper

    return _decorator


def validate_message_context(message, native_sources=None):
    destination = message.context.get("destination")
    if destination:
        # moved to global config level, used to be in "Audio" subsection 
        native_sources = native_sources or \
                        Configuration().get("native_sources") or \
                        Configuration().get("Audio", {}).get("native_sources") or \
                        ["debug_cli", "audio"]
        if any(s in destination for s in native_sources):
            # request from device
            return True
        # external request, do not handle
        return False
    # broadcast for everyone
    return True

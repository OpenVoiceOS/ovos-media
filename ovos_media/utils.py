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

import math
import unicodedata
from functools import wraps

from ovos_config import Configuration
from ovos_bus_client.session import SessionManager
from ovos_utils.log import LOG


def is_real_number(value) -> bool:
    """True only for an actual finite real number.

    ``bool`` is an ``int`` subclass but is never a legitimate numeric value
    here (durations/positions/etc), and ``NaN``/``inf``/``-inf`` ARE valid
    ``float`` instances that pass a bare ``isinstance(x, (int, float))``
    check yet blow up downstream (``int(nan)`` raises ``ValueError``,
    ``int(inf)`` raises ``OverflowError``). Centralizing the check here
    keeps every bus-fed numeric field guarded the same way.
    """
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def is_injection_char(c: str) -> bool:
    """True for a character that acts as a newline/format-injection
    surface in log viewers or HTTP stacks: C0/C1 controls (category Cc,
    also covering the historical ``< 0x20`` and ``0x7f`` checks), format
    chars like zero-width/bidi overrides (Cf), and the Unicode line/
    paragraph separators U+2028/U+2029 (Zl/Zp) - all of which act as
    newline-equivalents even though they aren't ASCII control characters.
    """
    return unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp")


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
            if message is None or not validate:
                return func(self, message)
            try:
                is_default = SessionManager.get(message).session_id == "default"
            except Exception as e:
                # malformed session context (eg. empty/non-str session_id) —
                # a hostile or buggy peer must never be able to crash a
                # gated handler; treat it as NOT the default/local session
                LOG.warning(f"ignoring '{message.msg_type}' message, "
                           f"malformed session context: {e}")
                return None
            if is_default:
                return func(self, message)
            LOG.debug(f"ignoring '{message.msg_type}' message, "
                      f"not from the default/local session")
            return None

        return func_wrapper

    return _decorator


def validate_message_context(message, native_sources=None):
    destination = message.context.get("destination")
    if destination:
        # a bare string destination is legitimate (ovos-bus-client's
        # .reply()/.forward() produce one) - normalize it to a single-element
        # list so membership below is exact, not substring containment
        # (destination "audio_settings_skill" must NOT match native source
        # "audio")
        if isinstance(destination, str):
            destination = [destination]
        elif not isinstance(destination, (list, tuple, set)):
            # anything else (int, dict, ...) is not a valid destination
            # container - refuse without raising, treat as external
            return False
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

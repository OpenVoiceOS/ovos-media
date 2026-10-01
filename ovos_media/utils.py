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

from ovos_bus_client.session import SessionManager
from ovos_utils.log import LOG


def is_default_session(message=None, validate_source: bool = True) -> bool:
    """True when a message may drive this device's playback.

    ovos-media is conceptually a *single* player bound to its own device —
    the ``"default"`` session.  In a HiveMind split the OCP pipeline runs on
    the server and forwards playback commands stamped with the *originating*
    session.  A server-side ovos-media must NOT act on a satellite's command
    (``session_id != "default"``): the satellite has its own embedded
    ovos-media that handles it.  hivemind-core NATs the satellite's session
    to ``"default"`` for that embedded instance (or the satellite sets
    ``validate_source: false``), so the satellite's instance sees
    ``"default"`` and executes.

    Mirrors :func:`ovos_audio.utils.require_default_session`.

    A message passes if any of:
        - it is ``None`` (internal/synthetic call), OR
        - ``validate_source`` is falsy (act on everything), OR
        - its session id is ``"default"`` (local request).

    Malformed session handling follows SESSION-1's field-vs-carrier split:
    a malformed FIELD (e.g. an empty or wrong-typed ``session_id`` on a
    session that is a JSON object) is treated as if the field were omitted
    and resolves to the ``"default"`` session (SESSION-1 §2, §2.1 — a
    consumer MUST NOT reject a Message over any single field's value); a
    malformed CARRIER (a ``session`` value that is not a JSON object at
    all) is refused, never defaulted (SESSION-1 §2.5) — ``SessionManager``
    raises on it and the except path below implements the drop. Either
    way a hostile or buggy peer can never crash a gated handler; the trust
    boundary for session identity is the bus/bridge (HIVEMIND-BRIDGE-1
    session NAT), not consumer-side field policing.
    """
    if message is None or not validate_source:
        return True
    try:
        return SessionManager.get(message).session_id == "default"
    except Exception as e:
        LOG.warning(f"ignoring '{message.msg_type}' message, "
                    f"malformed session context: {e}")
        return False

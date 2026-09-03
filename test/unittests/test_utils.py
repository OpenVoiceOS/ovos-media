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
"""Unit tests for ovos_media.utils — is_default_session."""
import unittest


class TestIsDefaultSessionMalformedContext(unittest.TestCase):
    """SESSION-1 splits malformed sessions into fields and carriers.

    A malformed FIELD (empty or wrong-typed ``session_id`` on a session
    that IS a JSON object) behaves as if the field were omitted and
    resolves to the "default" session (§2, §2.1) — the gate opens; a
    consumer MUST NOT reject a Message over one field's value. A
    malformed CARRIER (``session`` present but not a JSON object) is
    refused and MUST NOT be defaulted (§2.5). Neither may ever raise
    out of a gated handler.
    """

    def test_empty_session_id_field_defaults_no_exception(self):
        from ovos_bus_client.message import Message
        from ovos_media.utils import is_default_session
        msg = Message("x", context={"session": {"session_id": ""}})

        self.assertTrue(is_default_session(msg))  # must not raise

    def test_none_session_id_field_defaults_no_exception(self):
        from ovos_bus_client.message import Message
        from ovos_media.utils import is_default_session
        msg = Message("x", context={"session": {"session_id": None}})

        self.assertTrue(is_default_session(msg))  # must not raise

    def test_empty_session_object_defaults(self):
        # {} is equivalent to an absent session (§2.1)
        from ovos_bus_client.message import Message
        from ovos_media.utils import is_default_session
        msg = Message("x", context={"session": {}})

        self.assertTrue(is_default_session(msg))

    def test_str_session_carrier_refused_no_exception(self):
        from ovos_bus_client.message import Message
        from ovos_media.utils import is_default_session
        msg = Message("x", context={"session": "not_a_dict"})

        self.assertFalse(is_default_session(msg))  # must not raise

    def test_list_session_carrier_refused_no_exception(self):
        from ovos_bus_client.message import Message
        from ovos_media.utils import is_default_session
        msg = Message("x", context={"session": ["not", "a", "dict"]})

        self.assertFalse(is_default_session(msg))  # must not raise

    def test_valid_default_session_still_runs(self):
        from ovos_bus_client.message import Message
        from ovos_media.utils import is_default_session
        msg = Message("x", context={"session": {"session_id": "default"}})

        self.assertTrue(is_default_session(msg))

    def test_missing_session_context_still_defers_to_default(self):
        # absent "session" key: legitimate local emitters omit it, so this
        # keeps falling through to SessionManager.get's local default.
        from ovos_bus_client.message import Message
        from ovos_media.utils import is_default_session
        msg = Message("x", context={})

        self.assertTrue(is_default_session(msg))

    def test_named_session_refused(self):
        from ovos_bus_client.message import Message
        from ovos_media.utils import is_default_session
        msg = Message("x", context={"session": {"session_id": "sat-1"}})

        self.assertFalse(is_default_session(msg))

    def test_named_session_allowed_when_source_not_validated(self):
        from ovos_bus_client.message import Message
        from ovos_media.utils import is_default_session
        msg = Message("x", context={"session": {"session_id": "sat-1"}})

        self.assertTrue(is_default_session(msg, validate_source=False))

    def test_synthetic_call_allowed(self):
        from ovos_media.utils import is_default_session

        self.assertTrue(is_default_session(None))


if __name__ == "__main__":
    unittest.main()

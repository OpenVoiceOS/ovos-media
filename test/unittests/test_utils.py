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
    """A malformed session context (empty session_id, non-dict session)
    must never crash a gated handler — it must be refused (treated as
    NOT-default) and logged, not raised, so any peer cannot DoS a handler
    with context={"session": {"session_id": ""}}."""

    def test_empty_session_id_refused_no_exception(self):
        from ovos_bus_client.message import Message
        from ovos_media.utils import is_default_session
        msg = Message("x", context={"session": {"session_id": ""}})

        self.assertFalse(is_default_session(msg))  # must not raise

    def test_str_session_refused_no_exception(self):
        from ovos_bus_client.message import Message
        from ovos_media.utils import is_default_session
        msg = Message("x", context={"session": "not_a_dict"})

        self.assertFalse(is_default_session(msg))  # must not raise

    def test_valid_default_session_still_runs(self):
        from ovos_bus_client.message import Message
        from ovos_media.utils import is_default_session
        msg = Message("x", context={"session": {"session_id": "default"}})

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

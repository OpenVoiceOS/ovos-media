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
"""Unit tests for ovos_media.utils — require_default_session."""
import unittest


class TestRequireDefaultSessionMalformedContext(unittest.TestCase):
    """A malformed session context (empty session_id, non-dict session)
    must never crash a gated handler — it must be refused (treated as
    NOT-default) and logged, not raised, so any peer cannot DoS a handler
    with context={"session": {"session_id": ""}}."""

    def _make_handler(self):
        from ovos_media.utils import require_default_session

        calls = []

        class _Svc:
            validate_source = True

            @require_default_session()
            def handle(self, message=None):
                calls.append(message)
                return "ran"

        return _Svc(), calls

    def test_empty_session_id_refused_no_exception(self):
        from ovos_bus_client.message import Message
        svc, calls = self._make_handler()
        msg = Message("x", context={"session": {"session_id": ""}})

        result = svc.handle(msg)  # must not raise

        self.assertIsNone(result)
        self.assertEqual(calls, [])

    def test_str_session_refused_no_exception(self):
        from ovos_bus_client.message import Message
        svc, calls = self._make_handler()
        msg = Message("x", context={"session": "not_a_dict"})

        result = svc.handle(msg)  # must not raise

        self.assertIsNone(result)
        self.assertEqual(calls, [])

    def test_valid_default_session_still_runs(self):
        from ovos_bus_client.message import Message
        svc, calls = self._make_handler()
        msg = Message("x", context={"session": {"session_id": "default"}})

        result = svc.handle(msg)

        self.assertEqual(result, "ran")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()

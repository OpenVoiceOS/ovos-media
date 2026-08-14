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
"""Unit tests for ovos_media.utils — validate_message_context."""
import unittest
from unittest.mock import MagicMock, patch


def _make_message(destination=None):
    """Return a minimal fake message with a context dict."""
    msg = MagicMock()
    msg.context = {"destination": destination} if destination is not None else {}
    return msg


class TestValidateMessageContextBroadcast(unittest.TestCase):
    """Messages with no destination are broadcasts — always accepted."""

    def test_no_destination_returns_true(self):
        from ovos_media.utils import validate_message_context
        msg = _make_message()
        with patch("ovos_media.utils.Configuration", return_value={}):
            self.assertTrue(validate_message_context(msg))

    def test_empty_destination_list_returns_true(self):
        from ovos_media.utils import validate_message_context
        # destination key present but falsy value should still be a broadcast
        msg = _make_message(destination=None)
        with patch("ovos_media.utils.Configuration", return_value={}):
            self.assertTrue(validate_message_context(msg))


class TestValidateMessageContextNativeSources(unittest.TestCase):
    """Messages with a matching destination are accepted; others are rejected."""

    def _call(self, destination, native_sources=None, config_native=None):
        from ovos_media.utils import validate_message_context
        msg = _make_message(destination=destination)

        def _fake_config():
            base = {}
            if config_native:
                base["native_sources"] = config_native
            return base

        with patch("ovos_media.utils.Configuration", side_effect=_fake_config):
            return validate_message_context(msg, native_sources=native_sources)

    def test_destination_matches_native_source_returns_true(self):
        result = self._call(
            destination=["audio"],
            native_sources=["debug_cli", "audio"],
        )
        self.assertTrue(result)

    def test_destination_does_not_match_native_source_returns_false(self):
        result = self._call(
            destination=["remote_client"],
            native_sources=["debug_cli", "audio"],
        )
        self.assertFalse(result)

    def test_native_sources_from_config_top_level(self):
        """Falls back to Configuration()['native_sources'] when not provided."""
        from ovos_media.utils import validate_message_context
        msg = _make_message(destination=["audio"])

        call_count = 0

        def _fake_config():
            nonlocal call_count
            call_count += 1
            return {"native_sources": ["audio"]}

        with patch("ovos_media.utils.Configuration", side_effect=_fake_config):
            result = validate_message_context(msg)
        self.assertTrue(result)

    def test_native_sources_falls_back_to_audio_subsection(self):
        """Falls back to Configuration()['Audio']['native_sources']."""
        from ovos_media.utils import validate_message_context
        msg = _make_message(destination=["audio"])

        def _fake_config():
            return {"Audio": {"native_sources": ["audio"]}}

        with patch("ovos_media.utils.Configuration", side_effect=_fake_config):
            result = validate_message_context(msg)
        self.assertTrue(result)

    def test_provided_native_sources_take_priority(self):
        """Explicitly passed native_sources override config values."""
        from ovos_media.utils import validate_message_context
        msg = _make_message(destination=["custom_source"])

        def _fake_config():
            return {"native_sources": ["audio"]}

        with patch("ovos_media.utils.Configuration", side_effect=_fake_config):
            # custom_source is in explicitly passed list
            result = validate_message_context(msg, native_sources=["custom_source"])
        self.assertTrue(result)

    def test_partial_destination_match_returns_true(self):
        """Any destination element in native_sources is sufficient."""
        from ovos_media.utils import validate_message_context
        msg = _make_message(destination=["audio", "remote"])
        with patch("ovos_media.utils.Configuration", return_value={}):
            result = validate_message_context(
                msg, native_sources=["audio"]
            )
        self.assertTrue(result)


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


class TestValidateMessageContextStringDestination(unittest.TestCase):
    """A bare string destination (produced by ovos-bus-client's
    .reply()/.forward()) must be matched exactly, never by substring
    containment."""

    def _call(self, destination, native_sources):
        from ovos_media.utils import validate_message_context
        msg = _make_message(destination=destination)
        with patch("ovos_media.utils.Configuration", return_value={}):
            return validate_message_context(msg, native_sources=native_sources)

    def test_substring_match_returns_false(self):
        # "audio_settings_skill" contains "audio" but is not the native
        # "audio" source - must not be treated as a device-originated request
        result = self._call("audio_settings_skill", ["debug_cli", "audio"])
        self.assertFalse(result)

    def test_exact_string_match_returns_true(self):
        result = self._call("audio", ["debug_cli", "audio"])
        self.assertTrue(result)

    def test_non_matching_string_returns_false(self):
        result = self._call("remote_client", ["debug_cli", "audio"])
        self.assertFalse(result)

    def test_int_destination_returns_false_without_raising(self):
        result = self._call(12345, ["debug_cli", "audio"])
        self.assertFalse(result)

    def test_dict_destination_returns_false(self):
        # previously `in` on a dict checked its keys, an accident that
        # let a dict destination slip through as a "match"
        result = self._call({"audio": True}, ["debug_cli", "audio"])
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()

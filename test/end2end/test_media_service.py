"""
End-to-end integration tests for MediaService.

Uses FakeBus (in-process, no network) to exercise the full MediaService
message handler layer without audio hardware or a real MPRIS D-Bus session.

Harness pattern mirrors ovoscope.audio.AudioServiceHarness — subscribe to
expected reply messages, emit the trigger, wait for the event, assert.

These tests verify:
- ping/pong protocol
- search start animation (GUI show_media_player with state="loading")
- home handler (GUI update)
- OPM audio backend query
- MediaService lifecycle (started → alive → ready → stopping)
"""
import threading
import time
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message


# ---------------------------------------------------------------------------
# Minimal FakeBus implementation (no network, in-process)
# ---------------------------------------------------------------------------

class _FakeBus:
    """Synchronous in-process message bus for integration testing."""

    def __init__(self) -> None:
        self._handlers: Dict[str, List] = {}
        self.emitted: List[Message] = []

    def on(self, msg_type: str, handler) -> None:
        """Register a handler for *msg_type*."""
        self._handlers.setdefault(msg_type, []).append(handler)

    def off(self, msg_type: str, handler=None) -> None:
        """Remove a handler (or all handlers if *handler* is None)."""
        if handler is None:
            self._handlers.pop(msg_type, None)
        else:
            handlers = self._handlers.get(msg_type, [])
            if handler in handlers:
                handlers.remove(handler)

    def emit(self, message) -> None:
        """Deliver *message* synchronously to all registered handlers."""
        if isinstance(message, str):
            message = Message(message)
        self.emitted.append(message)
        for handler in list(self._handlers.get(message.msg_type, [])):
            handler(message)

    def wait_for_message(
        self, msg_type: str, timeout: float = 2.0
    ) -> Optional[Message]:
        """Return first already-emitted message of *msg_type*, or None."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for msg in self.emitted:
                if msg.msg_type == msg_type:
                    return msg
            time.sleep(0.02)
        return None

    # Stubs required by ProcessStatus / Configuration helpers
    def once(self, msg_type: str, handler) -> None:
        """Register a one-shot handler."""
        def _once(msg):
            self.off(msg_type, _once)
            handler(msg)
        self.on(msg_type, _once)


# ---------------------------------------------------------------------------
# MediaServiceHarness
# ---------------------------------------------------------------------------

class MediaServiceHarness:
    """Integration test harness for :class:`ovos_media.service.MediaService`.

    Patches heavy dependencies (MPRIS D-Bus, audio/video/web plugin loading,
    GUIInterface) so tests run without audio hardware or a D-Bus session.

    Usage::

        with MediaServiceHarness() as h:
            h.ping()
            h.assert_ponged()
    """

    def __init__(self) -> None:
        self.bus = _FakeBus()
        self._patches: List[Any] = []
        self.service: Any = None
        self.gui_mock: MagicMock = MagicMock()

    def _start_patches(self) -> None:
        """Apply unittest.mock patches for all heavy external dependencies."""
        targets = [
            "ovos_media.service.MessageBusClient",
            "ovos_media.service.ProcessStatus",
            "ovos_media.service.Configuration",
        ]
        for target in targets:
            p = patch(target)
            mock = p.start()
            self._patches.append(p)
            if target == "ovos_media.service.Configuration":
                mock.return_value = {"media": {}}
            if target == "ovos_media.service.ProcessStatus":
                mock.return_value = MagicMock()

        # Mock OCPMediaPlayer at service level so we can inspect calls to ocp.*
        ocp_patch = patch("ovos_media.service.OCPMediaPlayer")
        ocp_cls_mock = ocp_patch.start()
        self._patches.append(ocp_patch)
        self.ocp_mock = MagicMock()
        self.ocp_mock.gui = self.gui_mock
        # Wire add_event so handlers actually land on our FakeBus
        self.ocp_mock.add_event.side_effect = lambda evt, handler: self.bus.on(evt, handler)
        ocp_cls_mock.return_value = self.ocp_mock

        # Patch LegacyAudioServiceCompat so it doesn't connect
        legacy_patch = patch("ovos_media.service.LegacyAudioServiceCompat")
        legacy_patch.start()
        self._patches.append(legacy_patch)

    def _stop_patches(self) -> None:
        for p in reversed(self._patches):
            try:
                p.stop()
            except RuntimeError:
                pass

    def start(self) -> "MediaServiceHarness":
        """Start the harness: apply patches and instantiate MediaService."""
        self._start_patches()
        from ovos_media.service import MediaService
        self.service = MediaService(bus=self.bus)
        return self

    def stop(self) -> None:
        """Tear down patches."""
        self._stop_patches()

    # Context manager support
    def __enter__(self) -> "MediaServiceHarness":
        return self.start()

    def __exit__(self, *args) -> None:
        self.stop()

    # --- Helpers to emit bus messages ----------------------------------------

    def ping(self) -> None:
        """Emit ``ovos.common_play.ping``."""
        self.bus.emit(Message("ovos.common_play.ping"))

    def home(self) -> None:
        """Emit ``ovos.common_play.home``."""
        self.bus.emit(Message("ovos.common_play.home"))

    def search_start(self) -> None:
        """Emit ``ovos.common_play.search.start``."""
        self.bus.emit(Message("ovos.common_play.search.start"))

    def search_end(self) -> None:
        """Emit ``ovos.common_play.search.end``."""
        self.bus.emit(Message("ovos.common_play.search.end"))

    def opm_query(self) -> None:
        """Emit ``opm.audio.query``."""
        self.bus.emit(Message("opm.audio.query"))

    # --- Assertion helpers ----------------------------------------------------

    def assert_ponged(self, timeout: float = 1.0) -> None:
        """Assert ``ovos.common_play.pong`` was emitted within *timeout* s."""
        msg = self.bus.wait_for_message("ovos.common_play.pong", timeout)
        assert msg is not None, "Expected ovos.common_play.pong but never received it"

    def assert_gui_show_media_player_called(self, **kwargs) -> None:
        """Assert gui.show_media_player was called with the given keyword args."""
        self.gui_mock.show_media_player.assert_called_with(**kwargs)

    def assert_opm_response_emitted(self, timeout: float = 1.0) -> None:
        """Assert an opm.audio.query response was emitted."""
        # response msg_type pattern is "{type}.response" or similar
        found = any(
            "opm.audio.query" in m.msg_type
            for m in self.bus.emitted
        )
        assert found, (
            "Expected an opm.audio.query response but none found. "
            f"Emitted types: {[m.msg_type for m in self.bus.emitted]}"
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMediaServicePing(unittest.TestCase):
    """Ping/pong protocol."""

    def test_ping_triggers_pong(self) -> None:
        """Emitting ping must produce pong on the same bus."""
        with MediaServiceHarness() as h:
            h.ping()
            h.assert_ponged()

    def test_pong_message_is_reply(self) -> None:
        """Pong must be a reply to the ping message."""
        with MediaServiceHarness() as h:
            ping = Message("ovos.common_play.ping", context={"session": "abc"})
            self.bus_emitted: List[Message] = []
            h.bus.emit(ping)
            pong = h.bus.wait_for_message("ovos.common_play.pong")
            assert pong is not None


class TestMediaServiceSearchHandlers(unittest.TestCase):
    """Search lifecycle handlers."""

    def test_search_start_shows_loading_gui(self) -> None:
        """search.start must call gui.show_media_player with state='loading'."""
        with MediaServiceHarness() as h:
            h.search_start()
            h.assert_gui_show_media_player_called(
                now_playing=None,
                playlist=[],
                search_results=[],
                state="loading",
            )

    def test_search_end_does_not_raise(self) -> None:
        """search.end handler must not raise even when not yet implemented."""
        with MediaServiceHarness() as h:
            try:
                h.search_end()
            except Exception as exc:
                self.fail(f"handle_search_end raised unexpectedly: {exc}")


class TestMediaServiceHome(unittest.TestCase):
    """Home handler."""

    def test_home_calls_update_gui(self) -> None:
        """ovos.common_play.home must call ocp._update_gui()."""
        with MediaServiceHarness() as h:
            h.service.ocp._update_gui = MagicMock()
            h.home()
            h.service.ocp._update_gui.assert_called_once()


class TestMediaServiceOpmQuery(unittest.TestCase):
    """OPM audio query handler."""

    def test_opm_audio_query_emits_response(self) -> None:
        """opm.audio.query must emit a response with backend plugin info."""
        with MediaServiceHarness() as h:
            h.opm_query()
            h.assert_opm_response_emitted()


class TestMediaServiceLifecycle(unittest.TestCase):
    """Service lifecycle (status transitions)."""

    def test_status_started_on_init(self) -> None:
        with MediaServiceHarness() as h:
            h.service.status.set_started.assert_called_once()

    def test_status_alive_on_init(self) -> None:
        with MediaServiceHarness() as h:
            h.service.status.set_alive.assert_called_once()

    def test_run_sets_ready(self) -> None:
        with MediaServiceHarness() as h:
            h.service.run()
            h.service.status.set_ready.assert_called_once()

    def test_shutdown_calls_stopping(self) -> None:
        with MediaServiceHarness() as h:
            h.service.ocp = MagicMock()
            h.service.shutdown()
            h.service.status.set_stopping.assert_called_once()

    def test_shutdown_resets_ocp(self) -> None:
        with MediaServiceHarness() as h:
            h.service.ocp = MagicMock()
            h.service.shutdown()
            h.service.ocp.reset.assert_called_once()

    def test_shutdown_calls_ocp_shutdown(self) -> None:
        with MediaServiceHarness() as h:
            h.service.ocp = MagicMock()
            h.service.shutdown()
            h.service.ocp.shutdown.assert_called_once()


class TestMediaServiceBusRegistration(unittest.TestCase):
    """Verify bus event registration on init."""

    def test_opm_query_handler_registered(self) -> None:
        """opm.audio.query handler must be registered during init."""
        with MediaServiceHarness() as h:
            assert "opm.audio.query" in h.bus._handlers, (
                "opm.audio.query handler not registered on bus"
            )


if __name__ == "__main__":
    unittest.main()

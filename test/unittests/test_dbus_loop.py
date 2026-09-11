"""Tests for the D-Bus thread — connection lifecycle, retry bound, teardown.

All DBus I/O is mocked; these run without a D-Bus session.
"""
import asyncio
import unittest
from unittest.mock import MagicMock, patch, AsyncMock

from dbus_next.constants import BusType


def _make_loop(config=None):
    """Return a DbusLoop that has not been started."""
    from ovos_media.mpris.loop import DbusLoop
    return DbusLoop(config=config or {})


class TestDbusType(unittest.TestCase):
    """dbus_type maps the config string onto a BusType."""

    def test_dbus_type_session_by_default(self):
        loop = _make_loop({})
        self.assertEqual(loop.dbus_type, BusType.SESSION)

    def test_dbus_type_system_when_configured(self):
        loop = _make_loop({"dbus_type": "system"})
        self.assertEqual(loop.dbus_type, BusType.SYSTEM)

    def test_dbus_type_session_case_insensitive(self):
        loop = _make_loop({"dbus_type": "SESSION"})
        self.assertEqual(loop.dbus_type, BusType.SESSION)


class TestConnect(unittest.IsolatedAsyncioTestCase):
    """connect() opens the bus and lets the exporter publish on it."""

    async def test_successful_connect_runs_on_connect_hook(self):
        loop = _make_loop()
        hook = AsyncMock()
        loop.on_connect = hook
        mock_bus = MagicMock()
        mock_bus.wait_for_disconnect = MagicMock(return_value=asyncio.Future())
        with patch("ovos_media.mpris.loop.DbusMessageBus") as mock_bus_cls:
            mock_bus_cls.return_value.connect = AsyncMock(return_value=mock_bus)
            self.assertTrue(await loop.connect())
        hook.assert_awaited_once_with(mock_bus)
        self.assertEqual(loop.dbus, mock_bus)

    async def test_connect_without_hook_still_succeeds(self):
        loop = _make_loop()
        mock_bus = MagicMock()
        mock_bus.wait_for_disconnect = MagicMock(return_value=asyncio.Future())
        with patch("ovos_media.mpris.loop.DbusMessageBus") as mock_bus_cls:
            mock_bus_cls.return_value.connect = AsyncMock(return_value=mock_bus)
            self.assertTrue(await loop.connect())

    async def test_connect_hooks_disconnect_callback(self):
        """The connection's disconnect future must get a done-callback that
        logs, so a bus dying mid-session is not silent."""
        loop = _make_loop()
        mock_bus = MagicMock()
        disconnect_future = asyncio.Future()
        mock_bus.wait_for_disconnect = MagicMock(return_value=disconnect_future)
        with patch("ovos_media.mpris.loop.DbusMessageBus") as mock_bus_cls:
            mock_bus_cls.return_value.connect = AsyncMock(return_value=mock_bus)
            await loop.connect()
        self.assertTrue(disconnect_future._callbacks)

    def _finished_future(self, exc=None):
        future = asyncio.Future()
        if exc is None:
            future.set_result(None)
        else:
            future.set_exception(exc)
        return future

    async def test_disconnect_logs_warning(self):
        loop = _make_loop()
        future = self._finished_future()
        with patch("ovos_media.mpris.loop.LOG") as mock_log:
            loop._on_disconnect(future)
        mock_log.warning.assert_called_once()
        self.assertIn("connection lost", mock_log.warning.call_args[0][0])

    async def test_disconnect_after_shutdown_does_not_log(self):
        loop = _make_loop()
        loop.shutdown_event.set()
        future = self._finished_future()
        with patch("ovos_media.mpris.loop.LOG") as mock_log:
            loop._on_disconnect(future)
        mock_log.warning.assert_not_called()

    async def test_disconnect_retrieves_exception_even_on_shutdown(self):
        # a future whose exception is never retrieved makes asyncio log its
        # own warning on top of ours; the retrieval must happen regardless
        # of whether the shutdown path decides to log.
        loop = _make_loop()
        loop.shutdown_event.set()
        future = self._finished_future(exc=ConnectionError("boom"))
        loop._on_disconnect(future)  # must not raise, must not warn twice
        self.assertTrue(future.exception() is not None)  # retrieval is idempotent

    async def test_disconnect_cancelled_future_is_not_queried_for_exception(self):
        loop = _make_loop()
        future = asyncio.Future()
        future.cancel()
        with patch("ovos_media.mpris.loop.LOG") as mock_log:
            loop._on_disconnect(future)  # must not raise CancelledError
        mock_log.warning.assert_called_once()


class TestDbusGracefulDegradation(unittest.IsolatedAsyncioTestCase):
    """event_loop must warn and return gracefully when D-Bus is unavailable."""

    async def test_dbus_connection_failure_logs_warning_and_returns(self):
        loop = _make_loop()
        loop.tick = AsyncMock()

        with patch("ovos_media.mpris.loop.DbusMessageBus") as mock_bus_cls, \
                patch("ovos_media.mpris.loop.LOG") as mock_log:
            mock_bus_cls.return_value.connect = AsyncMock(
                side_effect=ConnectionError("no D-Bus"))
            await loop.event_loop()
            mock_log.warning.assert_called_once()
            warning_msg = mock_log.warning.call_args[0][0]
            self.assertIn("MPRIS unavailable", warning_msg)
        loop.tick.assert_not_awaited()


class TestEventLoopTick(unittest.IsolatedAsyncioTestCase):
    """The loop's only job once connected is to await the tick."""

    async def test_tick_awaited_once_per_iteration(self):
        loop = _make_loop()
        loop.dbus = MagicMock()  # pre-connected, skip the connect branch
        loop.shutdown_event = MagicMock()
        loop.shutdown_event.is_set.side_effect = [False, False, True]
        loop.tick = AsyncMock()
        await loop.event_loop()
        self.assertEqual(loop.tick.await_count, 2)

    async def test_loop_idles_when_no_tick_is_registered(self):
        loop = _make_loop()
        loop.dbus = MagicMock()
        loop.shutdown_event = MagicMock()
        loop.shutdown_event.is_set.side_effect = [False, True]
        with patch("ovos_media.mpris.loop.asyncio.sleep",
                   new=AsyncMock()) as mock_sleep:
            await loop.event_loop()
            mock_sleep.assert_awaited_once()


class TestCallAsync(unittest.TestCase):
    """call_async is the one way work crosses onto the loop thread."""

    async def _work(self):
        return 42

    def test_posts_the_coroutine_to_the_loop(self):
        loop = _make_loop()
        coro = self._work()
        with patch.object(loop, "is_alive", return_value=True), \
                patch("ovos_media.mpris.loop.asyncio.run_coroutine_threadsafe") as post:
            loop.call_async(coro)
            post.assert_called_once_with(coro, loop.loop)
        coro.close()

    def test_command_is_dropped_when_the_thread_is_gone(self):
        # a headless install has no session bus: run() returns and every
        # transport verb after that would otherwise queue on a dead loop
        loop = _make_loop()
        coro = self._work()
        with patch("ovos_media.mpris.loop.asyncio.run_coroutine_threadsafe") as post:
            self.assertIsNone(loop.call_async(coro))
            post.assert_not_called()

    def test_command_is_dropped_after_shutdown(self):
        loop = _make_loop()
        loop.shutdown_event.set()
        coro = self._work()
        with patch.object(loop, "is_alive", return_value=True), \
                patch("ovos_media.mpris.loop.asyncio.run_coroutine_threadsafe") as post:
            self.assertIsNone(loop.call_async(coro))
            post.assert_not_called()


class TestRunRetryBound(unittest.TestCase):
    """run() must bound its retry loop and never recurse unboundedly."""

    def test_run_retries_bounded_and_does_not_recurse(self):
        dbus_loop = _make_loop()
        dbus_loop.shutdown_event = MagicMock()
        dbus_loop.shutdown_event.is_set.return_value = False

        loop = MagicMock()
        loop.run_until_complete = MagicMock(side_effect=RuntimeError("boom"))
        dbus_loop.loop = loop

        with patch("ovos_media.mpris.loop.LOG") as mock_log:
            dbus_loop.run()  # must return, not raise RecursionError
            mock_log.error.assert_called_with("MPRIS exited")

        # initial attempt + 5 retries = 6 calls
        self.assertEqual(loop.run_until_complete.call_count, 6)

    def test_run_stops_immediately_once_shutdown_event_is_set(self):
        dbus_loop = _make_loop()
        dbus_loop.shutdown_event = MagicMock()
        dbus_loop.shutdown_event.is_set.return_value = True

        loop = MagicMock()
        loop.run_until_complete = MagicMock(side_effect=RuntimeError("boom"))
        dbus_loop.loop = loop

        dbus_loop.run()
        loop.run_until_complete.assert_called_once()

    def test_run_returns_cleanly_on_success(self):
        dbus_loop = _make_loop()
        dbus_loop.shutdown_event = MagicMock()
        dbus_loop.shutdown_event.is_set.return_value = False

        loop = MagicMock()
        loop.run_until_complete = MagicMock(return_value=None)
        dbus_loop.loop = loop

        dbus_loop.run()
        loop.run_until_complete.assert_called_once()


class TestShutdown(unittest.TestCase):
    """shutdown() must set the shutdown_event and stop the loop."""

    def test_shutdown_sets_event_and_stops_loop(self):
        dbus_loop = _make_loop()
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = False
        dbus_loop.loop = mock_loop
        dbus_loop.shutdown_event = MagicMock()

        with patch.object(dbus_loop, "join"):  # thread never started here
            dbus_loop.shutdown()

        dbus_loop.shutdown_event.set.assert_called_once()
        mock_loop.call_soon_threadsafe.assert_called_once_with(mock_loop.stop)
        mock_loop.close.assert_called_once()

    def test_shutdown_leaves_a_running_loop_open(self):
        dbus_loop = _make_loop()
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True
        dbus_loop.loop = mock_loop

        with patch.object(dbus_loop, "join"):
            dbus_loop.shutdown()

        mock_loop.close.assert_not_called()

    def test_shutdown_cancels_the_pending_disconnect_task(self):
        # a live loop with a real still-pending disconnect watcher: shutdown
        # must cancel and await it rather than leave it pending for the loop
        # to destroy later ("Task was destroyed but it is pending!").
        import threading
        dbus_loop = _make_loop()
        thread = threading.Thread(target=dbus_loop.loop.run_forever, daemon=True)
        thread.start()

        async def _never_resolves():
            await asyncio.Future()

        async def _install_task():
            dbus_loop._disconnect_task = asyncio.ensure_future(_never_resolves())

        asyncio.run_coroutine_threadsafe(_install_task(), dbus_loop.loop).result(timeout=2)
        task = dbus_loop._disconnect_task
        with patch.object(dbus_loop, "join"):  # the Thread itself never started
            dbus_loop.shutdown()
        thread.join(timeout=2)
        self.assertTrue(task.done())
        self.assertTrue(task.cancelled())


class TestPatchDbusNext(unittest.TestCase):
    """Malformed introspection XML must cost the member, not the interface."""

    def test_unparseable_member_is_skipped(self):
        import xml.etree.ElementTree as ET
        import dbus_next.introspection
        from ovos_media.mpris.loop import patch_dbus_next

        patch_dbus_next()
        xml = ('<interface name="org.mpris.MediaPlayer2.Player">'
               '<property name="Rate" type="d" access="read"/>'
               '<property name="Broken"/>'
               '</interface>')
        iface = dbus_next.introspection.Interface.from_xml(ET.fromstring(xml))
        self.assertEqual([p.name for p in iface.properties], ["Rate"])

    def test_interface_without_a_name_still_raises(self):
        import xml.etree.ElementTree as ET
        import dbus_next.introspection
        from dbus_next.errors import InvalidIntrospectionError
        from ovos_media.mpris.loop import patch_dbus_next

        patch_dbus_next()
        with self.assertRaises(InvalidIntrospectionError):
            dbus_next.introspection.Interface.from_xml(
                ET.fromstring('<interface/>'))


if __name__ == "__main__":
    unittest.main()

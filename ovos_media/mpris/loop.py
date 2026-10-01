"""The D-Bus thread.

Everything MPRIS does happens on one asyncio loop running in one thread, and
:class:`DbusLoop` owns that thread, the loop and the connection. It knows
nothing about MPRIS itself: the exporter registers what to publish once the
connection is up, and the external-player manager supplies the per-iteration
work.

There are exactly two ways across the thread boundary. Work goes in through
:meth:`DbusLoop.call_async`, which posts a coroutine onto the loop from
whatever thread asked for it. Player state goes out through the dispatcher, so
the player keeps a single writer.
"""
import asyncio
from threading import Thread, Event

from ovos_utils.log import LOG


def patch_dbus_next():
    """Make dbus_next tolerate malformed introspection XML.

    Real players ship interfaces we cannot parse (chromium is the usual
    offender). Upstream raises on the whole interface; skip the members we
    cannot read and keep the rest.
    """
    from dbus_next.errors import InvalidIntrospectionError
    import dbus_next.introspection

    def from_xml(element):
        """Convert a :class:`xml.etree.ElementTree.Element` into a
        :class:`Interface`.

        The element must be valid DBus introspection XML for an ``interface``.

        :param element: The parsed XML element.
        :type element: :class:`xml.etree.ElementTree.Element`

        :raises:
            - :class:`InvalidIntrospectionError <dbus_next.InvalidIntrospectionError>` - If the XML tree is not valid introspection data.
        """
        name = element.attrib.get('name')
        if not name:
            raise InvalidIntrospectionError('interfaces must have a "name" attribute')

        interface = dbus_next.introspection.Interface(name)

        for child in element:
            try:
                if child.tag == 'method':
                    interface.methods.append(dbus_next.introspection.Method.from_xml(child))
                elif child.tag == 'signal':
                    interface.signals.append(dbus_next.introspection.Signal.from_xml(child))
                elif child.tag == 'property':
                    interface.properties.append(dbus_next.introspection.Property.from_xml(child))
            except:
                continue
        return interface

    dbus_next.introspection.Interface.from_xml = from_xml


patch_dbus_next()

from dbus_next.aio import MessageBus as DbusMessageBus
from dbus_next.constants import BusType


class DbusLoop(Thread):
    """The asyncio loop thread every MPRIS coroutine runs on."""

    def __init__(self, config=None, daemonic=True):
        super().__init__()
        self.daemon = daemonic
        self.config = config or {}
        self.dbus = None
        self.loop = asyncio.new_event_loop()
        self.shutdown_event = Event()
        # set once the connection is up, by whoever wants to publish on it
        self.on_connect = None
        # awaited once per iteration; supplies the pacing
        self.tick = None
        # the task watching for disconnect; kept so shutdown() can cancel
        # and await it instead of leaving it pending when the loop closes
        self._disconnect_task = None

    @property
    def dbus_type(self):
        config = self.config.get("dbus_type") or "session"
        return BusType.SYSTEM if config.lower().strip() == "system" else \
            BusType.SESSION

    def call_async(self, coro):
        """Post *coro* onto the loop from another thread.

        The one way in. Returns the concurrent future so a caller that needs
        the result can wait for it; nothing in ovos-media does. On a machine
        with no session bus the thread has already returned, and the work is
        dropped rather than left queued on a loop that will never run it.
        """
        if not self.is_alive() or self.shutdown_event.is_set():
            LOG.debug("MPRIS loop is not running, dropping command")
            coro.close()
            return None
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    async def connect(self):
        """Open the bus and let the exporter publish itself.

        Returns False when there is no session bus to talk to, which is the
        normal state on a headless install.
        """
        try:
            self.dbus = await DbusMessageBus(bus_type=self.dbus_type).connect()
            if self.on_connect is not None:
                await self.on_connect(self.dbus)
        except Exception as e:
            LOG.warning(f"MPRIS unavailable: could not connect to D-Bus session bus: {e}")
            return False
        self._disconnect_task = asyncio.ensure_future(self.dbus.wait_for_disconnect())
        self._disconnect_task.add_done_callback(self._on_disconnect)
        return True

    def _on_disconnect(self, future) -> None:
        """Notice the bus going away mid-session.

        The daemon already survives this silently (the tick loop just keeps
        failing to talk to a dead connection until it reconnects); this only
        adds the log line so "why did my desktop widget stop responding"
        has something to grep for.
        """
        # the future's exception must be retrieved even when a deliberate
        # shutdown is why we are here, or asyncio logs its own "exception
        # was never retrieved" warning on top of this one
        exc = None
        if not future.cancelled():
            exc = future.exception()
        if self.shutdown_event.is_set():
            return
        if exc is not None:
            LOG.debug(f"MPRIS disconnect watcher ended with: {exc}")
        LOG.warning("MPRIS session bus connection lost; desktop controls "
                    "inactive until restart")

    async def event_loop(self):
        self.shutdown_event.clear()
        while not self.shutdown_event.is_set():
            if not self.dbus and not await self.connect():
                return
            if self.tick is not None:
                await self.tick()
            else:
                await asyncio.sleep(self.config.get("mpris_poll_interval", 1))

    def run(self):
        count = 0
        max_count = 5
        while True:
            try:
                self.loop.run_until_complete(self.event_loop())
                return
            except Exception as e:
                if self.shutdown_event.is_set():
                    return
                LOG.exception(e)
                count += 1
                if count <= max_count:
                    LOG.warning(f"MPRIS daemon crashed, restarting: retry {count} out of {max_count}")
                    continue
                LOG.error("MPRIS exited")
                return

    @staticmethod
    async def _cancel_and_wait(task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            LOG.debug(f"MPRIS disconnect watcher raised while shutting down: {e}")

    def shutdown(self) -> None:
        """Stop the loop and release the thread."""
        self.shutdown_event.set()
        # cancel the disconnect watcher and wait for it to actually finish
        # on the loop it runs on; otherwise it is still pending when the
        # loop closes and asyncio logs "Task was destroyed but it is
        # pending!" on every shutdown, headless embeds included
        if self._disconnect_task is not None and self.loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self._cancel_and_wait(self._disconnect_task), self.loop)
            try:
                future.result(timeout=2)
            except Exception as e:
                LOG.debug(f"MPRIS disconnect watcher did not stop cleanly: {e}")
        self.loop.call_soon_threadsafe(self.loop.stop)
        # wait for the loop to finish from the outside (this runs on a
        # different thread)
        self.join(timeout=5)
        if not self.loop.is_running():
            self.loop.close()

"""These tests run the real worker thread, so they wait for it.

A bus message now becomes a command the player runs on its dispatcher, a
moment after the emit returns. The harness yields 50 ms after each emit,
which is enough on a fast machine and not on a loaded one — a coverage
run turned assertions that read the player straight after an emit into
race losses. Rather than sprinkle waits through the test files, every
harness bus emit is followed here by one call to ``Dispatcher.settle()``,
which returns as soon as the player is idle. No assertion changes, and no
fixed sleeps are added.

An emit made by the worker itself (a command emitting on the bus) is left
alone: it is already inside the work settle() would be waiting for.
"""
import pytest
from ovoscope.media import OCPPlayerHarness

from ovos_media.player.dispatcher import Dispatcher

_original_enter = OCPPlayerHarness.__enter__


def _settling_enter(self):
    harness = _original_enter(self)
    player = getattr(harness, "player", None)
    dispatcher = getattr(player, "dispatcher", None)
    if isinstance(dispatcher, Dispatcher):
        emit = harness.bus.emit

        def settling_emit(message):
            result = emit(message)
            if not dispatcher.in_command():
                dispatcher.settle(timeout=10)
            return result

        harness.bus.emit = settling_emit
    return harness


@pytest.fixture(autouse=True)
def settle_after_every_emit(monkeypatch):
    monkeypatch.setattr(OCPPlayerHarness, "__enter__", _settling_enter)

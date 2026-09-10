"""Unit tests drive the player directly, so their dispatcher runs inline.

These tests call player methods from the test thread and assert the result
on the next line. A worker thread would turn every one of them into a wait
loop without testing anything more: the ordering they exercise is the
order they call things in. The dispatcher therefore runs in immediate mode
here, and the ordering guarantee itself is tested against a real worker in
test_dispatcher.py and test_transport_ordering.py, which ask for one
explicitly with ``Dispatcher(immediate=False)``.

The end-to-end suite runs the real threaded dispatcher, unpatched.
"""
import pytest

from ovos_media.player.dispatcher import Dispatcher

_original_init = Dispatcher.__init__


@pytest.fixture(autouse=True)
def immediate_dispatchers(monkeypatch):
    def patched(self, name="ocp-dispatcher", immediate=None):
        _original_init(self, name=name,
                       immediate=True if immediate is None else immediate)

    monkeypatch.setattr(Dispatcher, "__init__", patched)

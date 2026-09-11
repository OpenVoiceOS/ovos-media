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

from ovos_media.player import OCPMediaPlayer
from ovos_media.player.dispatcher import Dispatcher

_original_init = Dispatcher.__init__


@pytest.fixture(autouse=True)
def immediate_dispatchers(monkeypatch):
    def patched(self, name="ocp-dispatcher", immediate=None):
        _original_init(self, name=name,
                       immediate=True if immediate is None else immediate)

    monkeypatch.setattr(Dispatcher, "__init__", patched)


_original_player_init = OCPMediaPlayer.__init__
# exposed so a test that specifically wants the production default (eg.
# test_player_mpris_config.py's own default-is-True regression test) can
# bypass the blanket opt-out below without instantiating a real player
OCPMediaPlayer._unpatched_init = _original_player_init


@pytest.fixture(autouse=True)
def mpris_off_by_default(monkeypatch):
    """media.enable_mpris defaults to True in production (ovos-media is a
    desktop MPRIS player unless configured off), but a unit test that builds
    a player from a config lacking the key must not inherit that default: it
    would start a real D-Bus thread and claim org.mpris.MediaPlayer2.OCP on
    whatever session bus the test runner has. A test exercising MPRIS itself
    opts back in by setting enable_mpris explicitly in its own config, or by
    calling OCPMediaPlayer._unpatched_init directly.
    """
    def patched(self, bus, config=None, validate_source=True, likes=None):
        config = dict(config or {})
        config.setdefault("enable_mpris", False)
        _original_player_init(self, bus=bus, config=config,
                              validate_source=validate_source, likes=likes)

    monkeypatch.setattr(OCPMediaPlayer, "__init__", patched)

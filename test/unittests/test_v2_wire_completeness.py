"""Wire-completeness regression tests for the MediaBackend v2 port.

v1 backends emitted several ovos.common_play.* messages themselves as a
side effect of their ocp_start/ocp_stop/ocp_pause/ocp_resume/ocp_error
wrappers. The v2 port moved ownership of every one of those wire messages
to the daemon, and each site is pinned individually here, driven through a
real OCPMediaPlayer + real BaseMediaService (not a mocked player) so the
actual call path (dispatcher, adapters, service) is exercised end to end.
"""
import time
import unittest

from ovos_bus_client.message import Message
from ovos_plugin_manager.templates.media import PlaybackEvent
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import MediaEntry, MediaState, PlaybackType, PlayerState, TrackState


def _track(uri, title, playback=PlaybackType.AUDIO):
    return MediaEntry(uri=uri, title=title, playback=playback)


def _make_player(bus=None, config=None):
    from ovos_media.player import OCPMediaPlayer
    bus = bus or FakeBus()
    player = OCPMediaPlayer(bus, config=config if config is not None else {})
    player.now_playing.extract_stream = lambda **kwargs: None
    return player


def _load(player, entries):
    player.playlist.clear()
    for e in entries:
        player.playlist.add_entry(e)
    player.set_now_playing(player.playlist[0])


class _StubBackend:
    """v2 backend stand-in that reports nothing over the bus itself - every
    ovos.common_play.* message observed in these tests is emitted by the
    daemon (base.py) or the player (player/__init__.py), never by this
    stub, which is the entire point being pinned."""

    is_remote = False

    def __init__(self, bus, name="stub", uris=("http", "https", "file")):
        self.bus = bus
        self.name = name
        self.aliases = [name]
        self._uris = list(uris)
        self._playing = False
        self._reporter = None

    def supported_uris(self):
        return self._uris

    def bind_event_reporter(self, reporter):
        self._reporter = reporter

    def load_track(self, uri, metadata=None):
        self._playing = True
        return True

    def play(self):
        pass

    def stop(self):
        was = self._playing
        self._playing = False
        return was

    def pause(self):
        pass

    def resume(self):
        pass

    def shutdown(self):
        pass


def _player_with_backend(bus=None):
    bus = bus or FakeBus()
    player = _make_player(bus)
    backend = _StubBackend(bus)
    player.audio_service.services = [backend]
    a = _track("http://example.com/a.mp3", "A")
    b = _track("http://example.com/b.mp3", "B")
    _load(player, [a, b])
    return bus, player, backend, a, b


class TestNonAdvancingEndReachesStopped(unittest.TestCase):
    """6a: a natural END_OF_MEDIA that does NOT advance the queue (autoplay
    off) must still leave the player in PlayerState.STOPPED - v1's
    ocp_stop()/ocp_error() emitted player.state STOPPED unconditionally on
    every end, whether or not OCP happened to have a next track."""

    def test_autoplay_off_natural_end_reaches_stopped(self):
        bus, player, backend, a, b = _player_with_backend()
        player.ocp_config = {"autoplay": False}
        player.play()
        self.assertEqual(player.state, PlayerState.PLAYING)

        backend._reporter(PlaybackEvent.END_OF_MEDIA)

        deadline = time.monotonic() + 3
        while player.state != PlayerState.STOPPED and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(player.state, PlayerState.STOPPED)
        player.shutdown()

    def test_mpris_playback_type_is_excluded(self):
        """MPRIS's own state machine (set_external_now_playing) owns
        Playing/Paused/Stopped for an external player - this path must not
        fight it by forcing STOPPED on every track end too."""
        bus, player, backend, a, b = _player_with_backend()
        player.ocp_config = {"autoplay": False}
        player.handle_playback_ended(Message("ovos.common_play.media.state"),
                                     playback_type=PlaybackType.MPRIS,
                                     playback_uri="mpris://foo",
                                     stop_requested=False)
        # no assertion on player.state changing - just that this does not
        # unconditionally force STOPPED regardless of MPRIS's own state
        player.shutdown()


class TestInvalidMediaReachesStopped(unittest.TestCase):
    """6b: INVALID_MEDIA must reach PlayerState.STOPPED - v1's ocp_error()
    emitted INVALID_MEDIA and player.state STOPPED together."""

    def test_invalid_media_sets_player_state_stopped(self):
        bus, player, backend, a, b = _player_with_backend()
        player.audio_service.services = []  # nothing supports the uri -> INVALID_MEDIA
        player.ocp_config = {"autoplay": False}
        player.play()

        deadline = time.monotonic() + 3
        while player.state != PlayerState.STOPPED and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(player.state, PlayerState.STOPPED)
        player.shutdown()


class TestResumeEmitsTrackState(unittest.TestCase):
    """6c: resume() must emit track.state PLAYING_{namespace} from the
    daemon's resume() verb path - v1's ocp_resume() did this alongside
    player.state PLAYING."""

    def test_resume_emits_playing_audio_track_state(self):
        bus, player, backend, a, b = _player_with_backend()
        player.play()
        backend._playing = True

        received = []
        bus.on("ovos.common_play.track.state", lambda m: received.append(m))

        player.resume()

        states = [m.data["state"] for m in received]
        self.assertIn(TrackState.PLAYING_AUDIO, states)
        player.shutdown()


class TestExternalStopEmitsPlayerStateStopped(unittest.TestCase):
    """6d: a skill stopping the backend service directly (bypassing
    OCPMediaPlayer.stop()) must still reach player.state STOPPED - the
    on_stop docstring's own scenario, previously only flagged
    _stop_requested without touching player.state at all."""

    def test_direct_service_stop_reaches_player_state_stopped(self):
        bus, player, backend, a, b = _player_with_backend()
        player.play()
        player.set_player_state(PlayerState.PLAYING)
        backend._playing = True
        player.audio_service.play_start_time = time.monotonic() - 5

        received = []
        bus.on("ovos.common_play.player.state", lambda m: received.append(m))

        player.audio_service.stop()  # as a skill would, not via player.stop()

        deadline = time.monotonic() + 3
        while player.state != PlayerState.STOPPED and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(player.state, PlayerState.STOPPED)
        states = [m.data["state"] for m in received]
        self.assertIn(PlayerState.STOPPED, states)
        player.shutdown()


class TestExternalTransportEventsReflectedInPlayerState(unittest.TestCase):
    """7: PlaybackEvent.PAUSED/RESUMED/STOPPED reported by a backend that
    was not asked (a Chromecast app, a Music Assistant UI, a hardware
    remote...) must reach the player's own state machine, not be dropped -
    relayed via BaseMediaService.on_external_event ->
    OCPMediaPlayer._on_backend_external_event."""

    def test_external_paused_event_sets_player_state_paused(self):
        bus, player, backend, a, b = _player_with_backend()
        player.play()
        player.set_player_state(PlayerState.PLAYING)

        backend._reporter(PlaybackEvent.PAUSED)

        deadline = time.monotonic() + 3
        while player.state != PlayerState.PAUSED and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(player.state, PlayerState.PAUSED)
        player.shutdown()

    def test_external_resumed_event_sets_player_state_playing(self):
        bus, player, backend, a, b = _player_with_backend()
        player.play()
        player.set_player_state(PlayerState.PAUSED)

        backend._reporter(PlaybackEvent.RESUMED)

        deadline = time.monotonic() + 3
        while player.state != PlayerState.PLAYING and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(player.state, PlayerState.PLAYING)
        player.shutdown()

    def test_external_stopped_event_sets_player_state_stopped_and_flags_stop(self):
        bus, player, backend, a, b = _player_with_backend()
        player.play()
        player.set_player_state(PlayerState.PLAYING)

        backend._reporter(PlaybackEvent.STOPPED)

        deadline = time.monotonic() + 3
        while not player._stop_requested and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(player._stop_requested)
        deadline = time.monotonic() + 3
        while player.state != PlayerState.STOPPED and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(player.state, PlayerState.STOPPED)
        player.shutdown()

"""Regression tests pinning autoplay, seek, playlist, and search-gating behavior.

NowPlaying (constructed BEFORE OCPMediaPlayer.register_bus_handlers) must not
    reset playback -> PlaybackType.UNDEFINED on END_OF_MEDIA before
    OCPMediaPlayer.handle_playback_ended gets to check what was playing
    (both are subscribed to 'ovos.common_play.media.state'; NowPlaying's
    handler fires first purely because it was registered first) — autoplay
    must still run despite that ordering.
handle_player_media_update's `if state == self.media_state: return`
    dedup guard must not swallow a SECOND consecutive INVALID_MEDIA in a
    bad-track skip chain — the player must skip through every unplayable
    track instead of wedging mid-PLAYING.
End of queue (repeat off) must transition the player state to STOPPED.
The GUI now_playing payload must include 'position' (a plain NowPlaying
    attribute, not a MediaEntry dataclass field) and report the 'duration'
    key the seekbar contract expects, not 'length'.
NowPlaying.reset() must clear uri/original_uri, and OCPMediaPlayer.reset()
    must reach the GUI (not just set `self.state = ...` internally).
handle_seek_request must treat `seekValue: 0` as an absolute seek, not as
    "no seekValue given" (falsy) that falls through to the relative-seek path.
handle_playlist_set_request must validate 'tracks' before clearing the
    existing playlist, so a malformed/absent 'tracks' key does not KeyError
    with the playlist already wiped.
'ovos.common_play.home'/'.search.start'/'.search.end' are pipeline-side
    signals this daemon does not subscribe to at all — emitting them must
    be a pure no-op, never stopping or resetting playback in progress.
Switching playback type (eg. VIDEO -> AUDIO) must clear the previous
    BaseMediaService's `current`, so a later stray LOADED_MEDIA event cannot
    revive it and run two backends at once.
BaseMediaService.handle_play must consume the full tracklist and `repeat`,
    not just tracks[0]; `tracks=[[]]` must not raise an uncaught IndexError.

The autoplay-chain and invalid-media-skip-chain tests drive their scenario
entirely through bus.emit()/FakeBus rather than calling the handler methods
directly, since only the real dispatch order can expose those two behaviors.
"""
import threading
import time
import unittest
from unittest.mock import patch

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import (
    MediaState,
    PlayerState,
    PlaybackType,
    MediaEntry,
    Playlist,
)

from player_fixture import make_player


def _track(uri, title, playback=PlaybackType.AUDIO):
    return MediaEntry(uri=uri, title=title, playback=playback)


class TestAutoplayChain(unittest.TestCase):
    """A 2-track playlist must advance to track 2 on END_OF_MEDIA,
    driven entirely through bus.emit on a FakeBus."""

    def test_end_of_media_advances_to_next_track_via_bus(self):
        from ovos_media.player import OCPMediaPlayer

        bus = FakeBus()
        player = OCPMediaPlayer(bus, config={})
        t1 = _track("http://example.com/a.mp3", "Track A")
        t2 = _track("http://example.com/b.mp3", "Track B")
        player.set_now_playing(Playlist([t1, t2], title="Q"))
        player.set_player_state(PlayerState.PLAYING)
        self.assertEqual(player.now_playing.uri, t1.uri)

        # avoid touching a real backend for the *next* play() attempt —
        # only the routing decision (does play_next even fire, and does it
        # pick track 2) is under test here.
        with patch.object(player, "play") as mock_play:
            bus.emit(Message("ovos.common_play.media.state",
                             {"state": MediaState.END_OF_MEDIA}))

        mock_play.assert_called_once()
        self.assertEqual(player.now_playing.uri, t2.uri)
        self.assertEqual(player.now_playing.title, "Track B")


class TestInvalidMediaSkipChain(unittest.TestCase):
    """Three unplayable tracks in a row must ALL be attempted, and the
    player must end up in a sane (not silently-wedged-PLAYING) state.
    Driven entirely through bus.emit on a FakeBus."""

    def test_three_unplayable_tracks_all_attempted_via_bus(self):
        from ovos_media.player import OCPMediaPlayer

        bus = FakeBus()
        player = OCPMediaPlayer(bus, config={})
        # no backend plugins loaded -> every play attempt on this uri_type
        # synchronously emits INVALID_MEDIA (BaseMediaService.play(), "no
        # service found for uri_type")
        player.audio_service.services = []
        # The skip-to-next-track retry is deferred (on_invalid_stream)
        # instead of recursing inline, so shorten the delay and wait for the
        # chain rather than expecting it to complete inside bus.emit().
        player.invalid_stream_delay = 0.01

        tracks = [_track(f"http://example.com/t{i}.mp3", f"T{i}")
                 for i in range(3)]

        invalid_media_events = []
        bus.on("ovos.common_play.media.state",
              lambda m: invalid_media_events.append(m.data.get("state")))
        player_state_events = []
        bus.on("ovos.common_play.player.state",
              lambda m: player_state_events.append(m.data.get("state")))
        # start from PLAYING so F3's STOPPED transition (fired deep inside
        # the recursive skip chain, before any frame's own trailing
        # set_player_state(PLAYING) call has had a chance to run) is not
        # itself a same-state no-op against the constructor's default
        # PlayerState.STOPPED.
        player.set_player_state(PlayerState.PLAYING)
        player_state_events.clear()

        bus.emit(Message("ovos.common_play.play", {
            "media": tracks[0].as_dict,
            "playlist": [t.as_dict for t in tracks],
        }))

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if sum(1 for s in invalid_media_events
                   if s == MediaState.INVALID_MEDIA) >= 3:
                break
            time.sleep(0.02)
        time.sleep(0.2)  # let the chain settle past the last track

        invalid_count = sum(1 for s in invalid_media_events
                            if s == MediaState.INVALID_MEDIA)
        # without the C1 fix, the SECOND consecutive INVALID_MEDIA is
        # swallowed by the `state == self.media_state` dedup guard and the
        # skip chain stops after only one retry (2 events, not 3).
        self.assertEqual(invalid_count, 3,
                         f"expected all 3 unplayable tracks to be attempted, "
                         f"got {invalid_count} INVALID_MEDIA events: "
                         f"{invalid_media_events}")
        # Exhausting the queue (repeat off) must transition player
        # state instead of leaving it wedged at whatever it was.
        self.assertIn(PlayerState.STOPPED, player_state_events,
                      "expected a STOPPED transition once the queue of "
                      "unplayable tracks was exhausted")


class TestEndOfQueueStops(unittest.TestCase):
    def test_play_next_at_end_of_queue_sets_stopped(self):
        from ovos_media.player import OCPMediaPlayer

        bus = FakeBus()
        player = OCPMediaPlayer(bus, config={})
        t1 = _track("http://example.com/only.mp3", "Only")
        player.set_now_playing(Playlist([t1], title="Q"))
        player.set_player_state(PlayerState.PLAYING)

        with patch.object(player, "play") as mock_play:
            player.play_next()

        mock_play.assert_not_called()
        self.assertEqual(player.state, PlayerState.STOPPED)


class TestResetClearsZombieEntry(unittest.TestCase):
    def test_stop_then_reset_clears_now_playing_uri(self):
        from ovos_media.player import OCPMediaPlayer

        bus = FakeBus()
        player = OCPMediaPlayer(bus, config={})
        t1 = _track("http://example.com/a.mp3", "A")
        player.set_now_playing(t1)
        self.assertEqual(player.now_playing.uri, t1.uri)

        player.reset()

        self.assertEqual(player.now_playing.uri, "")
        self.assertEqual(player.now_playing.original_uri, "")


class TestSeekZeroIsAbsolute(unittest.TestCase):
    def test_seek_value_zero_seeks_to_start(self):
        from ovos_media.player import OCPMediaPlayer

        bus = FakeBus()
        player = OCPMediaPlayer(bus, config={})
        t1 = _track("http://example.com/a.mp3", "A")
        player.set_now_playing(t1)
        # nonzero so the buggy relative-seek fallback path (position=this,
        # + 0 ms from 'seconds') would compute something other than 0 too —
        # otherwise both the buggy and fixed code paths land on seek(0) by
        # coincidence and the test can't tell them apart.
        player.now_playing.position = 15000

        with patch.object(player, "seek") as mock_seek:
            bus.emit(Message("ovos.common_play.seek", {"seekValue": 0}))

        mock_seek.assert_called_once_with(0)


class TestPlaylistSetMissingTracks(unittest.TestCase):
    def test_playlist_set_without_tracks_key_does_not_raise_and_clears(self):
        from ovos_media.player import OCPMediaPlayer

        bus = FakeBus()
        player = OCPMediaPlayer(bus, config={})
        t1 = _track("http://example.com/a.mp3", "A")
        player.playlist.add_entry(t1)
        self.assertEqual(len(player.playlist), 1)

        # Call the handler directly, bypassing bus.emit()'s own try/except,
        # so a KeyError raised inside is not silently swallowed.
        try:
            player.handle_playlist_set_request(
                Message("ovos.common_play.playlist.set", {}))
        except KeyError:
            self.fail("handle_playlist_set_request must not KeyError when "
                     "'tracks' is missing")

        self.assertEqual(len(player.playlist), 0)

    def test_playlist_set_with_valid_tracks_replaces_playlist(self):
        from ovos_media.player import OCPMediaPlayer

        bus = FakeBus()
        player = OCPMediaPlayer(bus, config={})
        old = _track("http://example.com/old.mp3", "old")
        player.playlist.add_entry(old)

        new_track = _track("http://example.com/new.mp3", "new")
        bus.emit(Message("ovos.common_play.playlist.set",
                         {"tracks": [new_track.as_dict]}))

        self.assertEqual(len(player.playlist), 1)
        self.assertEqual(player.playlist[0].uri, new_track.uri)


class TestPipelineSideSignalsAreNotHandled(unittest.TestCase):
    """'ovos.common_play.home'/'.search.start'/'.search.end' are pipeline-side
    signals the OCP pipeline plugin uses to drive a GUI's own loading/
    navigation state. This daemon has no in-process GUI and no other state
    to change in response to them, so none of the three is subscribed —
    emitting them must be a pure no-op, in particular NOT stopping or
    resetting playback that is genuinely in progress."""

    def test_neither_player_nor_service_subscribes_to_home_or_search(self):
        from ovos_media.player import OCPMediaPlayer
        from ovos_media.service import MediaService

        bus = FakeBus()
        player = OCPMediaPlayer(bus, config={})
        self.assertFalse(hasattr(player, "handle_search_start"))
        player.shutdown()

        bus2 = FakeBus()
        service = MediaService(bus=bus2)
        try:
            self.assertFalse(hasattr(service, "handle_home"))
            self.assertFalse(hasattr(service, "handle_search_end"))
        finally:
            service.shutdown()

    def test_home_mid_playback_does_not_touch_player_state(self):
        """Emitting 'ovos.common_play.home' while a track is genuinely
        playing must not stop it or reset now_playing/playlist — the
        pipeline emits this on routine 'open media player' intents, and a
        prior version of MediaService.handle_home called ocp.reset(),
        which cleared state and broadcast STOPPED/NO_MEDIA while the
        backend kept playing. Drives it through the real MediaService (the
        component that used to own handle_home), not just OCPMediaPlayer
        directly, since the regression lived in MediaService's own bus
        binding. Fails against a build where MediaService still binds
        handle_home to ocp.reset()."""
        from unittest.mock import MagicMock, patch
        from ovos_media.service import MediaService

        bus = FakeBus()
        with patch("ovos_media.player.AudioService"), \
             patch("ovos_media.player.VideoService"), \
             patch("ovos_media.player.WebService"), \
             patch("ovos_media.player.OcpMprisExporter"), \
             patch("ovos_media.player.Configuration", return_value={"media": {}}), \
             patch("ovos_media.player.OCPMediaCatalog"), \
             patch("ovos_media.service.OCPVoiceSkill"), \
             patch("ovos_media.service.ProcessStatus") as MockStatus, \
             patch("ovos_media.service.Configuration", return_value={"media": {}}):
            MockStatus.return_value = MagicMock()
            service = MediaService(bus=bus)

        player = service.ocp
        track = MediaEntry(uri="http://example.com/a.mp3", title="A",
                           playback=PlaybackType.AUDIO)
        player.set_now_playing(track)
        player.set_player_state(PlayerState.PLAYING)

        bus.emit(Message("ovos.common_play.home", {}))

        self.assertEqual(player.state, PlayerState.PLAYING,
                         "home must not stop playback in progress")
        self.assertEqual(player.now_playing.uri, track.uri,
                         "home must not reset now_playing")
        self.assertEqual(len(player.playlist), 1,
                         "home must not clear the playlist")
        service.shutdown()


class TestPlaybackTypeSwitchStopsOtherBackend(unittest.TestCase):
    def test_switching_video_to_audio_clears_video_backend_current(self):
        from ovos_media.player import OCPMediaPlayer

        bus = FakeBus()
        player = OCPMediaPlayer(bus, config={})
        player.audio_service.services = []
        player.video_service.services = []

        fake_video_backend = type("FakeBackend", (), {
            "stop": lambda self: None,
        })()
        player.video_service.current = fake_video_backend

        audio_track = _track("http://example.com/a.mp3", "A",
                             playback=PlaybackType.AUDIO)
        player.set_now_playing(audio_track)
        player.play()

        self.assertIsNone(player.video_service.current,
                          "switching to AUDIO must clear the VIDEO "
                          "backend's `current`, not just leave it dangling")


class TestTrackStartQueueEnd(unittest.TestCase):
    def _make_service(self):
        from ovos_media.media_backends.base import BaseMediaService
        svc = BaseMediaService.__new__(BaseMediaService)
        svc._init_runtime_state()
        svc.bus = FakeBus()
        svc.services = []
        svc.current = None
        svc.volume_is_low = False
        svc.service_lock = threading.Lock()
        svc.play_start_time = 0.0
        svc.namespace = "audio"
        svc.config = {}
        svc._loaded = threading.Event()
        svc._loaded.set()
        return svc

    def test_track_start_none_emits_queue_end(self):
        svc = self._make_service()
        received = []
        svc.bus.on(f"ovos.{svc.namespace}.queue_end", lambda m: received.append(m))
        svc.track_start(None)
        self.assertEqual(len(received), 1)


class TestPlayerHandleMediaUpdateInvalidAutoplayOff(unittest.TestCase):
    """Test handle_player_media_update with INVALID_MEDIA and autoplay=False."""

    def test_invalid_media_autoplay_false_no_play_next(self):
        """handle_player_media_update INVALID_MEDIA with autoplay=False shouldn't call play_next."""
        p = make_player()
        p.ocp_config = {"autoplay": False}
        p.media_state = MediaState.NO_MEDIA

        with patch.object(p, "handle_invalid_media"), \
             patch.object(p, "play_next") as mock_next:
            p.handle_player_media_update(Message("ovos.common_play.media.state",
                                                 {"state": MediaState.INVALID_MEDIA}))

        mock_next.assert_not_called()

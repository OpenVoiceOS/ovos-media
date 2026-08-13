"""Regression tests for defects found during the 2026-08 wave-2 audit.

F1: NowPlaying (constructed BEFORE OCPMediaPlayer.register_bus_handlers)
    resets playback -> PlaybackType.UNDEFINED on END_OF_MEDIA before
    OCPMediaPlayer.handle_playback_ended gets to check what was playing
    (both are subscribed to 'ovos.common_play.media.state'; NowPlaying's
    handler fires first purely because it was registered first) — autoplay
    never ran because the UNDEFINED check always tripped.
C1: handle_player_media_update's `if state == self.media_state: return`
    dedup guard swallowed the SECOND consecutive INVALID_MEDIA in a bad-track
    skip chain, wedging the player mid-PLAYING instead of skipping through
    every unplayable track.
F3: end of queue (repeat off) left the player state untouched instead of
    transitioning to STOPPED.
F2/C3: the GUI now_playing payload was missing 'position' (a plain
    NowPlaying attribute, not a MediaEntry dataclass field) and reported
    'length' instead of the 'duration' key the seekbar contract expects.
F4: NowPlaying.reset() did not clear uri/original_uri, and
    OCPMediaPlayer.reset() used a bare `self.state = ...` assignment that
    never reached the GUI.
F5: handle_seek_request treated `seekValue: 0` as "no seekValue given"
    (falsy) and fell through to the relative-seek path.
F6: handle_playlist_set_request cleared the playlist before validating
    'tracks', so a malformed/absent 'tracks' key KeyError'd with the
    playlist already wiped.
F7/F8: 'ovos.common_play.search.start' was handled by both
    OCPMediaPlayer.handle_search_start (ungated) and
    MediaService.handle_search_start (also ungated, now deleted), double
    pushing the GUI "loading" state, including for non-default sessions.
C4: switching playback type (eg. VIDEO -> AUDIO) left the previous
    BaseMediaService's `current` set, so a later stray LOADED_MEDIA event
    could revive it and run two backends at once.
C6: BaseMediaService.handle_play only ever looked at tracks[0], silently
    dropping the rest of the tracklist and `repeat`; `tracks=[[]]` raised
    an uncaught IndexError.

CRITICAL: the F1/C1 tests drive their scenario entirely through
bus.emit()/FakeBus — calling the handler methods directly was the
false-green mechanism that let both defects through the first audit wave.
"""
import threading
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


def _track(uri, title, playback=PlaybackType.AUDIO):
    return MediaEntry(uri=uri, title=title, playback=playback)


class TestF1AutoplayChain(unittest.TestCase):
    """F1: a 2-track playlist must advance to track 2 on END_OF_MEDIA,
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


class TestC1InvalidMediaSkipChain(unittest.TestCase):
    """C1: three unplayable tracks in a row must ALL be attempted, and the
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

        invalid_count = sum(1 for s in invalid_media_events
                            if s == MediaState.INVALID_MEDIA)
        # without the C1 fix, the SECOND consecutive INVALID_MEDIA is
        # swallowed by the `state == self.media_state` dedup guard and the
        # skip chain stops after only one retry (2 events, not 3).
        self.assertEqual(invalid_count, 3,
                         f"expected all 3 unplayable tracks to be attempted, "
                         f"got {invalid_count} INVALID_MEDIA events: "
                         f"{invalid_media_events}")
        # F3: exhausting the queue (repeat off) must transition player
        # state instead of leaving it wedged at whatever it was.
        self.assertIn(PlayerState.STOPPED, player_state_events,
                      "expected a STOPPED transition once the queue of "
                      "unplayable tracks was exhausted")


class TestF3EndOfQueueStops(unittest.TestCase):
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


class TestF2GuiSeekbarPayload(unittest.TestCase):
    def test_update_gui_includes_position_and_duration(self):
        from ovos_media.player import OCPMediaPlayer

        bus = FakeBus()
        player = OCPMediaPlayer(bus, config={})
        t1 = _track("http://example.com/a.mp3", "A")
        player.set_now_playing(t1)
        player.now_playing.position = 4200
        player.now_playing.length = 90000

        captured = {}
        player.gui.show_media_player = lambda **kw: captured.update(kw)
        player._update_gui()

        np = captured["now_playing"]
        self.assertIn("position", np)
        self.assertIn("duration", np)
        self.assertEqual(np["position"], 4200)
        self.assertEqual(np["duration"], 90000)
        # 'length' round-trips too since it's still a real MediaEntry field
        self.assertEqual(np["length"], 90000)


class TestF4ResetClearsZombieEntry(unittest.TestCase):
    def test_stop_then_reset_clears_now_playing_uri_and_pushes_gui(self):
        from ovos_media.player import OCPMediaPlayer

        bus = FakeBus()
        player = OCPMediaPlayer(bus, config={})
        t1 = _track("http://example.com/a.mp3", "A")
        player.set_now_playing(t1)
        self.assertEqual(player.now_playing.uri, t1.uri)

        captured = []
        player.gui.show_media_player = lambda **kw: captured.append(kw)
        player.reset()

        self.assertEqual(player.now_playing.uri, "")
        self.assertEqual(player.now_playing.original_uri, "")
        self.assertTrue(captured, "reset() must push a GUI update")
        self.assertIsNone(captured[-1]["now_playing"],
                          "GUI must not keep showing the stopped track "
                          "(zombie now_playing entry)")


class TestF5SeekZeroIsAbsolute(unittest.TestCase):
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


class TestF6PlaylistSetMissingTracks(unittest.TestCase):
    def test_playlist_set_without_tracks_key_does_not_raise_and_clears(self):
        from ovos_media.player import OCPMediaPlayer

        bus = FakeBus()
        player = OCPMediaPlayer(bus, config={})
        t1 = _track("http://example.com/a.mp3", "A")
        player.playlist.add_entry(t1)
        self.assertEqual(len(player.playlist), 1)

        # call the handler directly (not F1/C1 — not exempt from that rule)
        # so a KeyError raised inside is not silently swallowed by
        # bus.emit()'s own try/except, which would mask the defect.
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


class TestF7F8SearchStartSessionGating(unittest.TestCase):
    def test_named_session_search_start_pushes_no_gui_update(self):
        from ovos_media.player import OCPMediaPlayer
        from ovos_bus_client.session import Session

        bus = FakeBus()
        player = OCPMediaPlayer(bus, config={})
        captured = []
        player.gui.show_media_player = lambda **kw: captured.append(kw)

        msg = Message("ovos.common_play.search.start", {})
        msg.context["session"] = Session("some-satellite-session").serialize()
        bus.emit(msg)

        self.assertEqual(len(captured), 0)

    def test_default_session_search_start_pushes_exactly_one_gui_update(self):
        from ovos_media.player import OCPMediaPlayer

        bus = FakeBus()
        player = OCPMediaPlayer(bus, config={})
        captured = []
        player.gui.show_media_player = lambda **kw: captured.append(kw)

        bus.emit(Message("ovos.common_play.search.start", {}))

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["state"], "loading")

    def test_media_service_no_longer_registers_its_own_search_start_handler(self):
        from ovos_media.service import MediaService

        bus = FakeBus()
        service = MediaService(bus=bus)
        try:
            self.assertFalse(hasattr(service, "handle_search_start"))
        finally:
            service.shutdown()


class TestC4PlaybackTypeSwitchStopsOtherBackend(unittest.TestCase):
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


class TestC6TracklistAndRepeat(unittest.TestCase):
    def _make_service(self):
        from ovos_media.media_backends.base import BaseMediaService
        svc = BaseMediaService.__new__(BaseMediaService)
        svc.bus = FakeBus()
        svc.services = []
        svc.current = None
        svc.validate_source = False
        svc.volume_is_low = False
        svc.service_lock = threading.Lock()
        svc.play_start_time = 0.0
        svc.namespace = "audio"
        svc.config = {}
        svc._pending_playlist = []
        svc._pending_repeat = False
        svc._last_full_playlist = []
        svc._loaded = threading.Event()
        svc._loaded.set()
        return svc

    def test_handle_play_with_empty_track_entry_does_not_raise(self):
        svc = self._make_service()
        with patch("threading.Timer") as mock_timer:
            svc.handle_play(Message("ovos.audio.service.play",
                                    {"tracks": [[]]}))
            mock_timer.assert_not_called()

    def test_handle_play_with_empty_entry_then_real_track_uses_the_real_one(self):
        svc = self._make_service()
        with patch("threading.Timer") as mock_timer:
            svc.handle_play(Message("ovos.audio.service.play",
                                    {"tracks": [[], "http://example.com/a.mp3"]}))
            mock_timer.assert_called_once()
            call_args = mock_timer.call_args[1].get("args") or mock_timer.call_args[0][2]
            self.assertEqual(call_args[0], "http://example.com/a.mp3")

    def test_handle_play_stores_remaining_tracks_and_repeat(self):
        svc = self._make_service()
        with patch("threading.Timer"):
            svc.handle_play(Message("ovos.audio.service.play", {
                "tracks": ["http://example.com/a.mp3",
                          "http://example.com/b.mp3",
                          "http://example.com/c.mp3"],
                "repeat": True,
            }))
        self.assertEqual(svc._pending_playlist,
                         ["http://example.com/b.mp3", "http://example.com/c.mp3"])
        self.assertTrue(svc._pending_repeat)
        self.assertEqual(svc._last_full_playlist,
                         ["http://example.com/a.mp3",
                          "http://example.com/b.mp3",
                          "http://example.com/c.mp3"])

    def test_track_start_none_advances_to_next_pending_track(self):
        svc = self._make_service()
        svc._pending_playlist = ["http://example.com/b.mp3"]
        svc._last_full_playlist = ["http://example.com/a.mp3", "http://example.com/b.mp3"]
        with patch.object(svc, "play") as mock_play:
            svc.track_start(None)
        mock_play.assert_called_once_with("http://example.com/b.mp3")
        self.assertEqual(svc._pending_playlist, [])

    def test_track_start_none_repeats_full_playlist_when_exhausted(self):
        svc = self._make_service()
        svc._pending_playlist = []
        svc._pending_repeat = True
        svc._last_full_playlist = ["http://example.com/a.mp3", "http://example.com/b.mp3"]
        with patch.object(svc, "play") as mock_play:
            svc.track_start(None)
        mock_play.assert_called_once_with("http://example.com/a.mp3")
        self.assertEqual(svc._pending_playlist, ["http://example.com/b.mp3"])

    def test_track_start_none_emits_queue_end_when_no_repeat_and_exhausted(self):
        svc = self._make_service()
        received = []
        svc.bus.on(f"ovos.{svc.namespace}.queue_end", lambda m: received.append(m))
        svc.track_start(None)
        self.assertEqual(len(received), 1)


if __name__ == "__main__":
    unittest.main()

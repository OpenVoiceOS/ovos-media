"""Regression tests pinning shuffle startup, MPRIS reflection, and playlist validation.

Shuffle mode must actually start playback after selecting a track
    (play_shuffle documents that it does not call play(); both call sites
    must play the selected track themselves, not leave it silently selected).
The MPRIS reflection must produce a now-playing dict with a uri, since
    dict2entry refuses one without — an external-player update must not
    raise after the takeover has already stopped local playback.
A malformed playlist.set payload must not wipe the existing playlist before
    validation aborts the mutation.
"""
import unittest
from unittest.mock import MagicMock

from ovos_utils.fakebus import FakeBus
from ovos_bus_client.message import Message
from ovos_utils.ocp import MediaEntry, PlaybackType, MediaState, TrackState

from ovos_media.player import OCPMediaPlayer


def _entry(uri, title="t"):
    return MediaEntry(uri=uri, title=title, playback=PlaybackType.AUDIO)


def _player(tracks=3):
    bus = FakeBus()
    p = OCPMediaPlayer(bus)
    p.audio_service = MagicMock()
    p.audio_service.services = [MagicMock()]
    p.video_service = MagicMock()
    p.video_service.services = []
    p.web_service = MagicMock()
    p.web_service.services = []
    p.mpris = None
    p.playlist.clear()
    for i in range(tracks):
        p.playlist.add_entry(_entry(f"file:///t{i}.mp3", f"t{i}"))
    p.set_now_playing(p.playlist[0])
    return bus, p


class TestShuffleStartsPlayback(unittest.TestCase):
    """C1 - a shuffled advance must reach the backend."""

    def test_shuffled_end_of_media_starts_next_track(self):
        bus, p = _player()
        p.shuffle = True
        p.play()
        p.audio_service.reset_mock()
        # natural track end via the bus, as the backend emits it
        bus.emit(Message("ovos.common_play.media.state",
                         {"state": MediaState.END_OF_MEDIA}))
        self.assertTrue(p.audio_service.play.called,
                        "shuffled advance never asked the backend to play")
        p.shutdown()

    def test_shuffled_prev_starts_playback(self):
        bus, p = _player()
        p.shuffle = True
        p.play()
        p.audio_service.reset_mock()
        p.play_prev()
        self.assertTrue(p.audio_service.play.called)
        p.shutdown()


class TestMprisReflectionAlwaysConstructs(unittest.TestCase):
    """C2 - reflected metadata without xesam:url must still make a valid
    now-playing entry instead of raising out of set_now_playing."""

    def test_meta_without_url_gets_synthetic_uri(self):
        from ovos_media.mpris import OcpMprisExporter

        class _V:  # dbus variant stand-in
            def __init__(self, value):
                self.value = value

        exporter = OcpMprisExporter.__new__(OcpMprisExporter)
        meta = {"state": "Playing", "loop_state": None,
                "xesam:title": _V("Song"), "xesam:artist": _V(["Band"]),
                "mpris:length": _V(200000)}
        data = exporter._meta2dict("org.mpris.MediaPlayer2.spotify", meta)
        self.assertEqual(data["uri"], "mpris://org.mpris.MediaPlayer2.spotify")

    def test_meta_with_url_maps_to_uri(self):
        from ovos_media.mpris import OcpMprisExporter

        class _V:
            def __init__(self, value):
                self.value = value

        exporter = OcpMprisExporter.__new__(OcpMprisExporter)
        meta = {"state": "Playing", "loop_state": None,
                "xesam:title": _V("Song"),
                "xesam:url": _V("https://x/song.mp3")}
        data = exporter._meta2dict("p", meta)
        self.assertEqual(data["uri"], "https://x/song.mp3")

    def test_external_now_playing_does_not_wedge_player(self):
        bus, p = _player()
        p.play()
        p.set_external_now_playing({
            "external_player": "org.mpris.MediaPlayer2.vlc",
            "state": "Playing", "title": "Song", "artist": "Band",
            "image": "", "length": 200000, "uri": "mpris://vlc"})
        self.assertEqual(p.now_playing.title, "Song")
        p.shutdown()


class TestPlaylistSetValidatesBeforeClearing(unittest.TestCase):
    """C3 - a malformed payload must not destroy the current playlist."""

    def _set(self, p, bus, tracks):
        bus.emit(Message("ovos.common_play.playlist.set", {"tracks": tracks}))

    def test_string_payload_keeps_playlist(self):
        bus, p = _player()
        self._set(p, bus, "notalist")
        self.assertEqual(len(p.playlist), 3)
        p.shutdown()

    def test_bad_entry_is_skipped_good_ones_kept(self):
        bus, p = _player()
        self._set(p, bus, [{"title": "no uri"},
                           {"uri": "file:///ok.mp3", "title": "ok"}])
        self.assertEqual(len(p.playlist), 1)
        self.assertEqual(p.playlist[0].uri, "file:///ok.mp3")
        p.shutdown()

    def test_all_bad_entries_clears_to_empty_not_partial(self):
        bus, p = _player()
        self._set(p, bus, [{"title": "no uri"}])
        self.assertEqual(len(p.playlist), 0)
        p.shutdown()

    def test_garbage_length_is_sanitized_not_left_poisoning_sum(self):
        # a non-numeric length must not survive into the playlist: it
        # would otherwise blow up Playlist.length's sum() over all
        # entries the next time anything asks for the playlist length
        bus, p = _player()
        self._set(p, bus, [{"uri": "file:///ok.mp3", "title": "ok",
                            "length": "garbage"}])
        self.assertEqual(len(p.playlist), 1)
        self.assertEqual(p.playlist[0].length, 0)
        # must not raise
        total = p.playlist.length
        self.assertIsInstance(total, (int, float))
        p.shutdown()

    def test_none_length_is_sanitized(self):
        bus, p = _player()
        self._set(p, bus, [{"uri": "file:///ok.mp3", "title": "ok",
                            "length": None}])
        self.assertEqual(p.playlist[0].length, 0)
        p.shutdown()


class TestStopClearsPausedOnDuckFlag(unittest.TestCase):
    """D5 sibling to pause(): stop() must reset the duck-pause flag same
    as pause() does, or a later ovos.utterance.handled fires a spurious
    restore_volume against whatever plays next."""

    def test_stop_resets_paused_on_duck(self):
        bus, p = _player()
        p.audio_service.services = [MagicMock()]
        p.play()
        p._paused_on_duck = True
        p.stop()
        self.assertFalse(p._paused_on_duck)
        p.shutdown()

    def test_stop_during_duck_restores_volume(self):
        # _paused_on_duck is shared by cork (pause) and duck (volume-lower,
        # still PLAYING). stop() must still restore volume for the duck
        # case, or the belated unduck/record_end no-ops on it (already
        # STOPPED) and volume stays lowered for the rest of the session.
        from ovos_utils.ocp import PlaybackType
        bus, p = _player()
        p.audio_service.services = [MagicMock()]
        p.playback_type = PlaybackType.AUDIO
        p.play()
        p._paused_on_duck = True  # simulates handle_duck_request having fired
        p.audio_service.reset_mock()
        p.stop()
        self.assertTrue(p.audio_service.restore_volume.called)
        self.assertFalse(p._paused_on_duck)
        p.shutdown()

    def test_stop_after_cork_does_not_resume_playback(self):
        # cork (pause-based) uses the same flag; stop() restoring volume
        # unconditionally must not accidentally resume playback.
        bus, p = _player()
        p.audio_service.services = [MagicMock()]
        p.play()
        p._paused_on_duck = True  # simulates handle_cork_request having fired
        p.audio_service.reset_mock()
        p.stop()
        self.assertFalse(p.audio_service.play.called)
        self.assertFalse(p.audio_service.resume.called)
        p.shutdown()


if __name__ == "__main__":
    unittest.main()

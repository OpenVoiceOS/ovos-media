"""Regression tests for the closing-certification findings.

C1: shuffle mode selected tracks but never started them (play_shuffle
    documents that it does not call play(); both call sites returned
    without playing - permanent silence on every shuffled advance).
C2: the MPRIS reflection produced a now-playing dict without a uri, which
    dict2entry refuses - every external-player update raised, after the
    takeover had already stopped local playback.
C3: a malformed playlist.set payload wiped the existing playlist before
    validation aborted mid-mutation.
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


if __name__ == "__main__":
    unittest.main()

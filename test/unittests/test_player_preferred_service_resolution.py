"""Tests for OCPMediaPlayer._resolve_preferred_service resilience to
backends whose `.name`/`.aliases` access raises."""
import unittest
from unittest.mock import MagicMock, patch

from ovos_utils.ocp import PlayerState, MediaState, LoopState, PlaybackType


def _make_player():
    from ovos_media.player import OCPMediaPlayer

    with patch("ovos_media.player.AudioService"), \
         patch("ovos_media.player.VideoService"), \
         patch("ovos_media.player.WebService"), \
         patch("ovos_media.player.OcpMprisExporter"), \
         patch("ovos_media.player.Configuration", return_value={"media": {}}), \
         patch("ovos_media.player.OCPMediaCatalog"):
        p = OCPMediaPlayer.__new__(OCPMediaPlayer)
        p._init_runtime_state()
        p.ocp_config = {}
        p._live_config = False
        p._validate_source_override = True
        p.state = PlayerState.STOPPED
        p.loop_state = LoopState.NONE
        p.media_state = MediaState.NO_MEDIA
        p.shuffle = False
        p.track_history = {}
        p._paused_on_duck = False
        p.now_playing = MagicMock()
        p.now_playing.playback = PlaybackType.AUDIO
        p.playlist = MagicMock()
        p.media = MagicMock()
        p.audio_service = MagicMock()
        p.video_service = MagicMock()
        p.web_service = MagicMock()
        p.current = None
        p.mpris = None
        from ovos_utils.fakebus import FakeBus
        p.bus = FakeBus()
    return p


class _RaisingBackend:
    """A backend whose `.name` property raises, like a real broken plugin."""

    @property
    def name(self):
        raise RuntimeError("backend is misconfigured")


class _GoodBackend:
    name = "good"
    aliases = []


class TestResolvePreferredServiceSkipsRaisingBackend(unittest.TestCase):

    def test_resolves_good_backend_despite_raising_peer(self):
        p = _make_player()
        media_service = MagicMock()
        media_service.get_preferred_players.return_value = ["good"]
        media_service.services = [_RaisingBackend(), _GoodBackend()]

        result = p._resolve_preferred_service(media_service)

        self.assertIs(result, media_service.services[1])

    def test_no_match_returns_none(self):
        p = _make_player()
        media_service = MagicMock()
        media_service.get_preferred_players.return_value = ["nonexistent"]
        media_service.services = [_RaisingBackend(), _GoodBackend()]

        result = p._resolve_preferred_service(media_service)

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()

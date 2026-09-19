"""The mocked OCPMediaPlayer every player-side test builds on.

The player is created with ``__new__`` so no plugin loading, MPRIS export
or bus connection happens, then ``_init_runtime_state()`` supplies the
real dispatcher, queue and roster. Everything the player collaborates with
(the catalog, the three backend services, NowPlaying) is a MagicMock, and
the bus is a FakeBus so emissions can be captured.
"""
from unittest.mock import MagicMock, patch

from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import (LoopState, MediaState, MediaType, PlaybackType,
                            PlayerState)


def make_player(playback_type: PlaybackType = PlaybackType.AUDIO):
    """Return a minimal OCPMediaPlayer with all external deps mocked.

    Args:
        playback_type: PlaybackType to assign to now_playing.
    """
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
        p.state = PlayerState.STOPPED
        p.loop_state = LoopState.NONE
        p.media_state = MediaState.NO_MEDIA
        p.shuffle = False
        p.track_history = {}
        p._paused_on_duck = False
        p.now_playing = MagicMock()
        p.now_playing.playback = playback_type
        p.now_playing.skill_id = "test.skill"
        p.now_playing.uri = "http://example.com/track.mp3"
        p.now_playing.original_uri = "http://example.com/track.mp3"
        p.now_playing.title = "Test Track"
        p.now_playing.artist = "Test Artist"
        p.now_playing.image = ""
        p.now_playing.length = 180000
        p.now_playing.position = 0
        p.now_playing.media_type = MediaType.GENERIC
        p.now_playing.infocard = {
            "uri": "http://example.com/track.mp3",
            "title": "Test Track",
        }
        p.now_playing.as_dict = {
            "uri": "http://example.com/track.mp3",
            "title": "Test Track",
            "artist": "Test Artist",
            "image": "",
        }
        p.playlist = MagicMock()
        p.playlist.as_list.return_value = []
        p.playlist.entries = []
        p.playlist.position = 0
        p.playlist.__len__ = lambda self: 0
        p.media = MagicMock()
        p.media.search_playlist.entries = []
        p.audio_service = MagicMock()
        p.audio_service.current = None
        p.video_service = MagicMock()
        p.video_service.current = None
        p.web_service = MagicMock()
        p.web_service.current = None
        p.mpris = None
        p.bus = FakeBus()
    return p

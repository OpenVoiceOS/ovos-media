"""Live tracking of the media the player currently has loaded."""
import dataclasses

from ovos_plugin_manager.ocp import load_stream_extractors
from ovos_utils.log import LOG
from ovos_utils.ocp import (MediaEntry, MediaState, MediaType, PlaybackType,
                            PlayerState, TrackState)

from ovos_media.bus.schemas import (decode_media, decode_media_state,
                                    decode_playback_time, decode_track_state)
from ovos_media.player import streams


class NowPlaying(MediaEntry):
    """ Live Tracking of currently playing media via bus events """

    def __init__(self, bus, player=None, *args, **kwargs):
        self.bus = bus
        self._player = player
        self.stream_xtract = load_stream_extractors()
        self.position = 0
        super().__init__(*args, **kwargs)
        self.original_uri = self.uri

    def as_entry(self) -> MediaEntry:
        """
        Return a MediaEntry representation of this object
        """
        return MediaEntry(**self.as_dict)

    @property
    def as_dict(self) -> dict:
        """
        Return a dict representation of this object's MediaEntry fields.

        NowPlaying is not itself decorated with @dataclass and carries plain
        instance attributes (bus, _player, stream_xtract, ...) alongside the
        MediaEntry dataclass fields it inherits. orjson only recognizes a
        type as a dataclass via that exact class's own __dict__, not an
        inherited one, so serializing a NowPlaying instance directly (as the
        inherited MediaEntry.as_dict does) raises
        "TypeError: Type is not JSON serializable: NowPlaying".

        Build a genuine MediaEntry from just the declared dataclass fields
        and delegate serialization to it; fields added to MediaEntry keep
        surviving the round-trip automatically since they are read from
        `dataclasses.fields(MediaEntry)` rather than a hand-maintained list.
        """
        fields = {f.name: getattr(self, f.name) for f in dataclasses.fields(MediaEntry)}
        return MediaEntry(**fields).as_dict

    def reset(self):
        """
        Reset the NowPlaying MediaEntry to default parameters
        """
        self.title = ""
        self.artist = ""
        self.skill_id = ""
        self.position = 0
        self.length = 0
        self.javascript = ""
        self.playback = PlaybackType.UNDEFINED
        self.status = TrackState.DISAMBIGUATION
        self.media_type = MediaType.GENERIC
        self.skill_icon = ""
        self.image = ""
        # Without clearing these, a stopped/home'd player still reports
        # the previous track's uri via now_playing.as_dict / GUI payload
        self.uri = ""
        self.original_uri = ""

    def update(self, entry: MediaEntry, skipkeys: list = None, newonly: bool = False):
        """
        Update this MediaEntry
        @param entry: dict or MediaEntry object to update this object with
        @param skipkeys: list of keys to not change
        @param newonly: if True, only adds new keys; existing keys are unchanged
        """
        if isinstance(entry, MediaEntry):
            entry = entry.as_dict
        super().update(entry, skipkeys, newonly)
        # uri updates should not be skipped
        if newonly and entry.get("uri"):
            super().update({"uri": entry["uri"]})

    def extract_stream(self, allowed_schemes=None):
        """
        Get metadata from ocp_plugins and add it to this MediaEntry
        @param allowed_schemes: extra uri schemes to accept, see
            streams.extract_stream
        """
        streams.extract_stream(self, self.playback == PlaybackType.VIDEO,
                               self.stream_xtract,
                               allowed_schemes=allowed_schemes)

    # bus api
    def handle_external_play(self, message):
        """
        Handle 'ovos.common_play.play' Messages. Update the metadata with new
        data received unconditionally, otherwise previous song keys might
        bleed into the new track
        @param message: Message associated with request
        """
        media = decode_media(message.data)
        if media is None:
            return
        self.update(media, newonly=False)

    # events from media services
    def handle_track_state_change(self, message):
        """
        Handle 'ovos.common_play.track.state' Messages. Update status
        @param message: Message with updated `state` data
        @return:
        """
        state = decode_track_state(message.data)
        if state == self.status:
            return
        LOG.info(f"TrackState changed: {repr(self.status)} -> {repr(state)}")
        self.status = state

        if state in (TrackState.PLAYING_AUDIO, TrackState.PLAYING_VIDEO,
                     TrackState.PLAYING_WEBVIEW, TrackState.PLAYING_SKILL,
                     TrackState.PLAYING_AUDIOSERVICE, TrackState.PLAYING_MPRIS):
            # backend confirmed playback started — mark player as PLAYING
            if hasattr(self, '_player') and self._player is not None:
                if getattr(self._player, "_stop_requested", False) is True:
                    # A stop has already settled this player. This message was
                    # emitted by a backend before the stop reached it and
                    # arrived after, so it is evidence about playback that has
                    # since stopped, not about playback now. Acting on it puts
                    # the player back into PLAYING with NO_MEDIA and an empty
                    # title, which is then what ovos.common_play.status
                    # answers. play() clears the flag before the next track,
                    # so real playback is unaffected.
                    #
                    # `is True` rather than a truth test: _player is a
                    # MagicMock in much of the suite, and every attribute of
                    # one is truthy, so a plain test would read "stopped" for
                    # every mocked player. The real flag is a bool and is only
                    # ever set to True or False.
                    LOG.debug(f"ignoring {repr(state)}: it arrived after a "
                              f"stop, so the player stays "
                              f"{repr(self._player.state)}")
                    return
                self._player.set_player_state(PlayerState.PLAYING)
                # reset the per-queue failure guards on evidence of PLAYBACK,
                # not on LOADED_MEDIA. base.py's handle_media_state_change
                # emits LOADED_MEDIA and THEN, for a track that loads fine but
                # raises out of current.play(), INVALID_MEDIA — with the guard
                # reset on LOADED_MEDIA that sequence reset both _failed_uris
                # and _track_failed_spoken on every single failing track (rate
                # limit degraded to per-track chatter, and a REPEAT queue where
                # every track loads-ok-but-fails-to-play never accumulated
                # enough of _failed_uris to trip all_failed(), looping without
                # bound). This TrackState.PLAYING_* branch only fires after
                # current.play() returns without raising, ie. real evidence of
                # playback — see base.py's handle_media_state_change.
                self._player._failed_uris.clear()
                self._player._track_failed_spoken = False
                self._player._cannot_seek_spoken = False
        elif state in (TrackState.QUEUED_SKILL, TrackState.QUEUED_VIDEO,
                       TrackState.QUEUED_AUDIO, TrackState.QUEUED_AUDIOSERVICE,
                       TrackState.QUEUED_WEBVIEW):
            # audio service is handling playback and this is queued in playlist
            pass
        elif state == TrackState.DISAMBIGUATION:
            # alternative results list — no playback state change
            pass
        # NOTE: pause is a PlayerState/MediaState concern, never a TrackState —
        # there is no TrackState.PAUSED_* member, so no pause branch belongs here.

    def on_end_of_media(self):
        """
        End-of-media reset, invoked as a plain method call by
        OCPMediaPlayer.handle_player_media_update (the only consumer of
        END_OF_MEDIA on 'ovos.common_play.media.state') AFTER it has captured
        the pre-reset playback type and uri. Playback ended, so allow the next track to
        change metadata again.
        """
        self.reset()

    def handle_media_state_change(self, message):
        """
        Handle 'ovos.common_play.media.state' Messages. If ended, reset.

        NOT registered on the bus (see ovos_media.bus.api): kept as a callable
        entry point for out-of-tree callers that drive NowPlaying directly.
        @param message: Message with updated MediaState
        """
        state = decode_media_state(message.data)
        if state == MediaState.END_OF_MEDIA:
            self.on_end_of_media()

    def handle_sync_seekbar(self, message):
        """
        Handle 'ovos.common_play.playback_time' Messages sent by audio backend
        @param message: Message with 'length' and 'position' data
        """
        for field, value in decode_playback_time(message.data).items():
            setattr(self, field, value)

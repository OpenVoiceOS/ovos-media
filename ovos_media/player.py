import random
import time
from os.path import join, dirname
from threading import RLock
from typing import List, Union

from json_database import JsonStorageXDG

from ovos_config import Configuration
from ovos_config.meta import get_xdg_base
from ovos_media.gui import OCPGUIInterface, OCPGUIState
from ovos_media.media_backends import AudioService, VideoService, WebService
from ovos_media.mpris import MprisPlayerCtl
from ovos_plugin_manager.ocp import load_stream_extractors
from ovos_plugin_manager.templates.media import MediaBackend
from ovos_utils.gui import is_gui_connected, is_gui_running
from ovos_utils.log import LOG
from ovos_utils.messagebus import Message
from ovos_utils.ocp import MediaType, Playlist
from ovos_utils.ocp import OCP_ID, PlayerState, LoopState, PlaybackType, PlaybackMode, TrackState, MediaState, \
    MediaEntry
from ovos_workshop import OVOSAbstractApplication
from ovos_workshop.decorators.ocp import ocp_search
from ovos_workshop.skills.common_play import OVOSCommonPlaybackSkill


class OCPMediaCatalog(OVOSCommonPlaybackSkill):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.skill_icon = f"{dirname(__file__)}/qt5/images/liked.svg"

        self.liked_songs = JsonStorageXDG("OCP_liked_songs",
                                          subfolder=get_xdg_base())
        LOG.debug(f"Liked songs playlist loaded: {self.liked_songs.path}")
        self.search_playlist = Playlist()
        self.ocp_skills = {}
        self.featured_skills = {}
        self.search_lock = RLock()
        self.add_event("ovos.common_play.skills.detach", self.handle_ocp_skill_detach)
        self.add_event("ovos.common_play.announce", self.handle_skill_announce)

        # TODO - add search results clear/replace events

        # register keywords
        def norm_name(n):
            return n.split("|")[0].split("(")[0].split("[")[0].split("{")[0].split("-")[0].strip()

        self.register_ocp_keyword(MediaType.MUSIC, "song_name",
                                  [norm_name(n["title"]) for n in self.liked_songs.values()])
        self.register_ocp_keyword(MediaType.MUSIC, "playlist_name",
                                  ["favorite", "liked", "favorites",
                                   "favorite songs", "favorite tracks",
                                   "favorite music", "my favorite songs",
                                   "my favorite tracks", "my favorite music",
                                   "liked songs", "liked tracks", "liked music",
                                   "my liked songs", "my liked tracks", "my liked music"])

    @ocp_search()
    def search_db(self, phrase, media_type):
        base_score = 15 if media_type == MediaType.MUSIC else 0
        entities = self.ocp_voc_match(phrase)
        base_score += 30 * len(entities)

        if entities.get("playlist_name"):
            if phrase.lower() == entities["playlist_name"]:
                base_score = 100
            yield {
                "match_confidence": min(base_score + 35, 100),
                "media_type": MediaType.MUSIC,
                "playback": PlaybackType.AUDIO,
                "playlist": self.liked_songs_playlist,  # return full playlist result
                "skill_icon": self.skill_icon,
                "title": "Liked Songs",
                "skill_id": self.skill_id
            }

        if entities.get("song_name"):
            title = entities["song_name"]
            candidates = [song for song in self.liked_songs_playlist
                          if title.lower() in song["title"].lower()]
            for c in candidates:
                c["match_confidence"] = min(base_score + 40, 100)
                c["media_type"] = MediaType.MUSIC
                c["playback"] = PlaybackType.AUDIO
                c["skill_id"] = self.skill_id
                c["skill_icon"] = self.skill_icon
                yield c

    @property
    def liked_songs_playlist(self):
        pl = list(self.liked_songs.values())
        for idx, p in enumerate(pl):
            pl[idx]["media_type"] = MediaType.MUSIC
            pl[idx]["playback"] = PlaybackType.AUDIO
            # HACK to allow sort_by_conf to work once this is in a Playlist object
            pl[idx]["match_confidence"] = p.get("play_count", 0) + 50
        return sorted(pl, key=lambda k: k.get("play_count", 0), reverse=True)

    def handle_skill_announce(self, message):
        skill_id = message.data.get("skill_id")
        skill_name = message.data.get("skill_name") or skill_id
        img = message.data.get("image") or message.data.get("thumbnail")
        has_featured = bool(message.data.get("featured_tracks"))
        media_types = message.data.get("media_types") or \
                      message.data.get("media_type") or \
                      [MediaType.GENERIC]

        if skill_id not in self.ocp_skills:
            LOG.debug(f"Registered {skill_id}")
            self.ocp_skills[skill_id] = []

        if has_featured:
            LOG.debug(f"Found skill with featured media: {skill_id}")
            self.featured_skills[skill_id] = {
                "skill_id": skill_id,
                "skill_name": skill_name,
                "image": img,
                "media_types": media_types
            }

    def handle_ocp_skill_detach(self, message):
        skill_id = message.data["skill_id"]
        if skill_id in self.ocp_skills:
            self.ocp_skills.pop(skill_id)
        if skill_id in self.featured_skills:
            self.featured_skills.pop(skill_id)

    def get_featured_skills(self, adult=False):
        # trigger a presence announcement from all loaded ocp skills
        self.bus.emit(Message("ovos.common_play.skills.get"))
        time.sleep(0.2)
        skills = list(self.featured_skills.values())
        if adult:
            return skills
        return [s for s in skills
                if MediaType.ADULT not in s["media_types"] and
                MediaType.HENTAI not in s["media_types"]]

    def clear(self):
        self.search_playlist.clear()

    def replace(self, playlist):
        self.search_playlist.replace(playlist)


class NowPlaying(MediaEntry):
    """ Live Tracking of currently playing media via bus events """

    def __init__(self, bus, *args, **kwargs):
        self.bus = bus
        self.stream_xtract = load_stream_extractors()
        self.position = 0
        super().__init__(*args, **kwargs)
        self.original_uri = self.uri
        self.bus.on("ovos.common_play.track.state", self.handle_track_state_change)
        self.bus.on("ovos.common_play.media.state", self.handle_media_state_change)
        self.bus.on("ovos.common_play.play", self.handle_external_play)
        self.bus.on("ovos.common_play.playback_time", self.handle_sync_seekbar)

    def as_entry(self) -> MediaEntry:
        """
        Return a MediaEntry representation of this object
        """
        return MediaEntry(**self.as_dict)

    @property
    def as_dict(self) -> dict:
        """
        Return a dict representation of this MediaEntry
        """
        return {"uri": self.uri,
                "title": self.title,
                "artist": self.artist,
                "image": self.image,
                "playback": self.playback,
                "status": self.status,
                "media_type": self.media_type,
                "length": self.length,
                "skill_id": self.skill_id,
                "skill_icon": self.skill_icon}

    def shutdown(self):
        """
        Remove NowPlaying events from the MessageBusClient
        """
        self.bus.remove("ovos.common_play.track.state", self.handle_track_state_change)
        self.bus.remove("ovos.common_play.media.state", self.handle_media_state_change)
        self.bus.remove('ovos.common_play.play', self.handle_external_play)
        self.bus.remove('ovos.common_play.playback_time', self.handle_sync_seekbar)

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

    def extract_stream(self):
        """
        Get metadata from ocp_plugins and add it to this MediaEntry
        """
        uri = self.uri
        if not uri:
            raise ValueError("No URI to extract stream from")
        if self.playback == PlaybackType.VIDEO:
            video = True
        else:
            video = False
        meta = self.stream_xtract.extract_stream(uri, video)
        # update media entry with new data
        if meta:
            LOG.info(f"OCP plugins metadata: {meta}")
            self.update(meta, newonly=True)
            self.original_uri = uri

        # validate extracted uri
        if not any((self.uri.startswith(s) for s in ["http", "file", "/"])):
            raise ValueError(f"invalid stream: {uri}")

    # bus api
    def handle_external_play(self, message):
        """
        Handle 'ovos.common_play.play' Messages. Update the metadata with new
        data received unconditionally, otherwise previous song keys might
        bleed into the new track
        @param message: Message associated with request
        """
        if message.data.get("tracks"):
            # backwards compat / old style
            playlist = message.data["tracks"]
            media = playlist[0]
        else:
            media = message.data.get("media", {})
        if media:
            self.update(media, newonly=False)

    # events from media services
    def handle_track_state_change(self, message):
        """
        Handle 'ovos.common_play.track.state' Messages. Update status
        @param message: Message with updated `state` data
        @return:
        """
        state = message.data.get("state")
        if state is None:
            raise ValueError(f"Got state update message with no state: "
                             f"{message}")
        if isinstance(state, int):
            state = TrackState(state)
        if not isinstance(state, TrackState):
            raise ValueError(f"Expected int or TrackState, but got: {state}")

        if state == self.status:
            return
        LOG.info(f"TrackState changed: {repr(self.status)} -> {repr(state)}")
        self.status = state

        if state == TrackState.PLAYING_SKILL:
            # skill is handling playback internally
            pass
        elif state == TrackState.PLAYING_VIDEO:
            # ovos common play is handling playback in GUI
            pass
        elif state == TrackState.PLAYING_AUDIO:
            # ovos common play is handling playback in GUI
            pass

        elif state == TrackState.DISAMBIGUATION:
            # alternative results list
            pass
        elif state in [TrackState.QUEUED_SKILL,
                       TrackState.QUEUED_VIDEO,
                       TrackState.QUEUED_AUDIO]:
            # audio service is handling playback and this is in playlist
            pass

    def handle_media_state_change(self, message):
        """
        Handle 'ovos.common_play.media.state' Messages. If ended, reset.
        @param message: Message with updated MediaState
        """
        state = message.data.get("state")
        if state is None:
            raise ValueError(f"Got state update message with no state: "
                             f"{message}")
        if isinstance(state, int):
            state = MediaState(state)
        if not isinstance(state, MediaState):
            raise ValueError(f"Expected int or TrackState, but got: {state}")

        if state == MediaState.END_OF_MEDIA:
            # playback ended, allow next track to change metadata again
            self.reset()

    def handle_sync_seekbar(self, message):
        """
        Handle 'ovos.common_play.playback_time' Messages sent by audio backend
        @param message: Message with 'length' and 'position' data
        """
        self.length = message.data["length"]
        self.position = message.data["position"]

    def handle_sync_trackinfo(self, message):
        """
        Handle 'mycroft.audio.service.track_info_reply' Messages with current
        media defined in message.data
        @param message: Message with dict MediaEntry data
        """
        self.update(message.data)


class OCPMediaPlayer(OVOSAbstractApplication):
    """OCP Virtual Media Player

    for OVOS this is all that exists and represents all loaded and currently playing media

    "now playing" is tracked and managed by this interface
    """

    def __init__(self, bus=None, config=None, resources_dir=None, skill_id=OCP_ID, **kwargs):
        resources_dir = resources_dir or join(dirname(__file__), "res")
        self.ocp_config = config or Configuration().get("OCP", {})

        self.state: PlayerState = PlayerState.STOPPED
        self.loop_state: LoopState = LoopState.NONE
        self.media_state: MediaState = MediaState.NO_MEDIA
        self.playlist: Playlist = Playlist()
        self.shuffle: bool = False
        self.track_history = {}  # Dict of track URI to play count

        # Define things referenced in `bind`
        self.now_playing: NowPlaying = None
        self.playlist: Playlist = Playlist("Search Results",
                                           skill_id="")  # TODO icon
        self.media: OCPMediaCatalog = None
        self.audio_service = None
        self.video_service = None
        self.web_service = None
        self.current: MediaBackend = None
        self.mpris: MprisPlayerCtl = None

        self._paused_on_duck = False
        super().__init__(skill_id=skill_id, bus=bus, resources_dir=resources_dir, **kwargs)

    def bind(self, bus=None):
        """
        Initialize components that need a MessageBusClient or instance of this
        object.
        @param bus: MessageBusClient object to register events on
        """
        super(OCPMediaPlayer, self).bind(bus)
        self.now_playing = NowPlaying(bus)
        self.media = OCPMediaCatalog(bus=self.bus, skill_id=OCP_ID + ".favorites")
        self.audio_service = AudioService(self.bus)
        self.video_service = VideoService(self.bus)
        self.web_service = WebService(self.bus)
        self.register_bus_handlers()
        # mpris settings
        manage_players = self.ocp_config.get("manage_external_players", False)
        if self.ocp_config.get('disable_mpris'):
            LOG.info("MPRIS integration is disabled")
            self.mpris = None
        else:
            self.mpris = MprisPlayerCtl(self, manage_players=manage_players)

        self.gui = OCPGUIInterface()
        self.gui.bind(self)
        # TODO - update gui for no-media in now_playing page

    def register_bus_handlers(self):
        # ovos common play bus api
        self.add_event('ovos.common_play.player.state', self.handle_player_state_update)
        self.add_event('ovos.common_play.media.state', self.handle_player_media_update)
        self.add_event('ovos.common_play.play', self.handle_play_request)
        self.add_event('ovos.common_play.pause', self.handle_pause_request)
        self.add_event('ovos.common_play.resume', self.handle_resume_request)
        self.add_event('ovos.common_play.stop', self.handle_stop_request)
        self.add_event('ovos.common_play.next', self.handle_next_request)
        self.add_event('ovos.common_play.previous', self.handle_prev_request)
        self.add_event('ovos.common_play.seek', self.handle_seek_request)
        self.add_event('ovos.common_play.get_track_length', self.handle_track_length_request)
        self.add_event('ovos.common_play.set_track_position', self.handle_set_track_position_request)
        self.add_event('ovos.common_play.get_track_position', self.handle_track_position_request)
        self.add_event('ovos.common_play.track_info', self.handle_track_info_request)
        self.add_event('ovos.common_play.list_backends', self.handle_list_backends_request)
        self.add_event('ovos.common_play.playlist.set', self.handle_playlist_set_request)
        self.add_event('ovos.common_play.playlist.clear', self.handle_playlist_clear_request)
        self.add_event('ovos.common_play.playlist.queue', self.handle_playlist_queue_request)
        self.add_event('ovos.common_play.duck', self.handle_duck_request)
        self.add_event('ovos.common_play.unduck', self.handle_unduck_request)
        self.add_event('ovos.common_play.cork', self.handle_cork_request)
        self.add_event('ovos.common_play.uncork', self.handle_uncork_request)
        self.add_event('ovos.common_play.shuffle.toggle', self.handle_shuffle_toggle_request)
        self.add_event('ovos.common_play.shuffle.set', self.handle_set_shuffle)
        self.add_event('ovos.common_play.shuffle.unset', self.handle_unset_shuffle)
        self.add_event('ovos.common_play.repeat.toggle', self.handle_repeat_toggle_request)
        self.add_event('ovos.common_play.repeat.set', self.handle_set_repeat)
        self.add_event('ovos.common_play.repeat.unset', self.handle_unset_repeat)
        self.add_event('ovos.common_play.SEI.get', self.handle_get_SEIs)
        self.add_event('ovos.common_play.search.start', self.handle_search_start)
        self.add_event("ovos.common_play.like", self.handle_like)
        self.add_event("ovos.common_play.unlike", self.handle_unlike)
        self.add_event("ovos.common_play.status", self.handle_status)
        self.handle_get_SEIs(Message("ovos.common_play.SEI.get"))  # report to ovos-core
        self.handle_status(Message("ovos.common_play.status"))  # report to ovos-core

    def handle_status(self, message):
        self.bus.emit(message.response({
            "playback_type": self.playback_type,
            "media_type": self.now_playing.media_type,
            "player_state": self.state,
            "loop_state": self.loop_state,
            "media_state": self.media_state,
            "shuffle": self.shuffle,
            "playlist_position": self.playlist.position,
            "playlist_size": len(self.playlist),
            "title": self.now_playing.title,
            "artist": self.now_playing.artist,
            "image": self.now_playing.image
        }))

    def handle_like(self, message):
        # sent from GUI or intent
        uri = message.data.get("uri") or self.now_playing.original_uri
        title = message.data.get("title") or self.now_playing.title
        image = message.data.get("image") or message.data.get("thumbnail") or self.now_playing.image
        artist = message.data.get("artist") or self.now_playing.artist
        self.media.liked_songs[uri] = {"title": title, "artist": artist,
                                       "image": image, "uri": uri}
        self.media.liked_songs.store()
        LOG.info(f"liked song: {uri}")
        self.gui.update_buttons()  # show in Player
        self.gui.update_ocp_cards()  # show in Home
        self.bus.emit(message.forward("mycroft.audio.play_sound",
                                      {"uri": "snd/acknowledge.mp3"}))

    def handle_unlike(self, message):
        # sent from GUI or intent
        uri = message.data.get("uri") or self.now_playing.original_uri
        if uri in self.media.liked_songs:
            self.media.liked_songs.pop(uri)
            self.media.liked_songs.store()
            LOG.info(f"unliked song: {uri}")

    def handle_search_start(self, message):
        self.gui.manage_display(OCPGUIState.SPINNER)

    @property
    def active_skill(self) -> str:
        """
        Return the skill_id of the skill providing the current media
        """
        return self.now_playing.skill_id

    @active_skill.setter
    def active_skill(self, val):
        """
        Return the skill_id of the skill providing the current media
        """
        self.now_playing.skill_id = val

    @property
    def playback_type(self) -> PlaybackType:
        """
        Return the PlaybackType for the current media
        """
        if self.now_playing:
            return self.now_playing.playback

    @playback_type.setter
    def playback_type(self, val):
        """
        Return the PlaybackType for the current media
        """
        assert isinstance(val, PlaybackType)
        if self.now_playing:
            self.now_playing.playback = val

    @property
    def tracks(self) -> List[MediaEntry]:
        """
        Return the current queue as a list of MediaEntry objects
        """
        if self.playlist:
            return self.playlist.entries
        return []

    @property
    def search_results(self) -> List[MediaEntry]:
        """
        Return a list of the previous search results as MediaEntry objects
        """
        return self.media.search_playlist.entries

    @property
    def can_prev(self) -> bool:
        """
        Return true if there is a previous track in the queue to skip to
        """
        if self.playback_type != PlaybackType.MPRIS and \
                self.playlist.is_first_track:
            return False
        return True

    @property
    def can_next(self) -> bool:
        """
        Return true if there is a next track in the queue to skip to
        """
        if self.loop_state != LoopState.NONE or \
                self.shuffle or \
                self.playback_type == PlaybackType.MPRIS:
            return True
        elif self.ocp_config.get("merge_search", True) and \
                not self.media.search_playlist.is_last_track:
            return True
        elif not self.playlist.is_last_track:
            return True
        return False

    # state
    def set_media_state(self, state: MediaState):
        """
        Set self.media_state and emit an event announcing this state change.
        @param state: New MediaState
        """
        if not isinstance(state, MediaState):
            raise TypeError(f"Expected MediaState and got: {state}")
        if state == self.media_state:
            return
        self.bus.emit(Message("ovos.common_play.media.state",
                              {"state": self.media_state}))

    def set_player_state(self, state: PlayerState):
        """
        Set self.state, update the GUI and MPRIS (if available), and emit an
        event announcing this state change.
        @param state: New PlayerState
        """
        if not isinstance(state, PlayerState):
            raise TypeError(f"Expected PlayerState and got: {state}")
        if state == self.state:
            return
        self.bus.emit(Message("ovos.common_play.player.state",
                              {"state": self.state}))
        state2str = {PlayerState.PLAYING: "Playing",
                     PlayerState.PAUSED: "Paused",
                     PlayerState.STOPPED: "Stopped"}
        if self.mpris:
            self.mpris.update_props({"CanPause": self.state == PlayerState.PLAYING,
                                     "CanPlay": self.state == PlayerState.PAUSED,
                                     "PlaybackStatus": state2str[state]})
        self.handle_status(Message("ovos.common_play.status"))  # report full status to ovos-core

    def set_now_playing(self, track: Union[dict, MediaEntry, Playlist]):
        """
        Set `track` as the currently playing media, update the playlist, and
        notify any GUI or MPRIS clients. Adds `track` to `playlist`
        @param track: MediaEntry or dict representation of a MediaEntry to play
        """
        if isinstance(track, dict):
            track = MediaEntry.from_dict(track)
        if not isinstance(track, (MediaEntry, Playlist)):
            raise ValueError(f"Expected MediaEntry, but got: {track}")

        # remove existing MPRIS entry if we were tracking that
        if self.now_playing.playback == PlaybackType.MPRIS and \
                track.playback != PlaybackType.MPRIS:
            self.playlist.clear()

        self.now_playing.reset()  # reset now_playing to remove old metadata
        if isinstance(track, MediaEntry):
            # single track entry (MediaEntry)
            self.now_playing.update(track)
            if track not in self.playlist:  # compared by uri
                self.playlist.add_entry(track)
        elif isinstance(track, Playlist):
            # this is a playlist result (list of dicts)
            self.playlist.clear()
            for entry in track:
                self.playlist.add_entry(entry)
            self.now_playing.update(self.playlist[0])

        if track.playback == PlaybackType.MPRIS:
            self.playlist.clear()
            self.playlist.add_entry(track)
        else:
            # sync playlist position
            self.playlist.goto_track(self.now_playing)

        if self.mpris:
            self.mpris.update_props(
                {"Metadata": self.now_playing.mpris_metadata}
            )
        self.handle_status(Message("ovos.common_play.status"))  # report full status to ovos-core

    # stream handling
    def validate_stream(self) -> bool:
        """
        Validate that self.now_playing is playable and update the GUI if it is
        @return: True if the `now_playing` stream can be handled
        """
        if self.playback_type not in [PlaybackType.SKILL,
                                      PlaybackType.UNDEFINED,
                                      PlaybackType.MPRIS]:
            try:
                self.now_playing.extract_stream()
            except Exception as e:
                LOG.exception(e)
                return False
            # check for is_gui_running is much faster as it doesnt need bus messages back and forth
            has_gui = is_gui_running() or is_gui_connected(self.bus)
            if not has_gui or self.ocp_config.get("force_audioservice", False) or \
                    self.ocp_config.get("playback_mode") == PlaybackMode.FORCE_AUDIO:
                # No gui, so lets force playback to use audio only
                self.now_playing.playback = PlaybackType.AUDIO

        return True

    def handle_get_SEIs(self, message: Message):
        """report available StreamExtractorIds
        OCP plugins handle specific SEIs and return a real stream / extra metadata

        this moves parsing to playback time instead of search time

        SEIs are identifiers of the format "{SEI}//{uri}"
        that might be present in media results

        seis are NOT uris, a uri comes after {SEI}//

        eg. for the youtube plugin a skill can return
          "youtube//https://youtube.com/watch?v=wChqNkd6F24"
        """
        xtract = load_stream_extractors()  # @lru_cache, its a lazy loaded singleton
        self.bus.emit(message.response({"SEI": xtract.supported_seis}))

    def on_invalid_stream(self):
        """
        Handle media playback errors. Show an error and play the next track.
        """
        self.bus.emit(Message("mycroft.audio.play_sound", {"uri": "snd/error.mp3"}))
        self.gui.manage_display(OCPGUIState.PLAYBACK_ERROR)
        LOG.warning(f"Failed to play: {self.now_playing}")
        time.sleep(3)  # let the user process that playback failed before moving on
        self.play_next()

    # media controls
    def play_media(self, track: Union[dict, MediaEntry],
                   disambiguation: List[Union[dict, MediaEntry]] = None,
                   playlist: List[Union[dict, MediaEntry]] = None):
        """
        Start playing the requested media, replacing any current playback.
        @param track: dict or MediaEntry to start playing
        @param disambiguation: list of tracks returned from search
        @param playlist: list of tracks in the current playlist
        """
        if isinstance(track, dict):
            track = MediaEntry.from_dict(track)
            LOG.debug(f"deserialized: {track}")

        if isinstance(track, Playlist):
            playlist = track
            track = track[0]
        elif not isinstance(track, MediaEntry):
            raise TypeError(f"Expected MediaEntry, got: {track}")

        if self.mpris:
            self.mpris.stop()

        if disambiguation:
            self.media.search_playlist.replace([t for t in disambiguation
                                                if t not in self.media.search_playlist])
            self.media.search_playlist.sort_by_conf()
        if playlist:
            self.playlist.replace(playlist)
        if track in self.playlist:
            self.playlist.goto_track(track)
        self.set_now_playing(track)
        self.play()

    def play(self):
        """
        Start playback of the current `now_playing` MediaEntry. Displays the GUI
        player, updates track history, emits events for any listeners, and
        updates mpris (if configured).
        """
        # stop any external media players
        if self.mpris and not self.mpris.stop_event.is_set():
            self.mpris.stop()

        # track play count
        if self.now_playing.uri in self.media.liked_songs:
            if "play_count" not in self.media.liked_songs[self.now_playing.uri]:
                self.media.liked_songs[self.now_playing.uri]["play_count"] = 0
            self.media.liked_songs[self.now_playing.uri]["play_count"] += 1
            self.media.liked_songs.store()

        # validate new stream
        if not self.validate_stream():
            LOG.warning("Stream Validation Failed")
            self.on_invalid_stream()
            return

        self.gui.manage_display(OCPGUIState.PLAYER)

        self.track_history.setdefault(self.now_playing.uri, 0)
        self.track_history[self.now_playing.uri] += 1

        if self.playback_type == PlaybackType.AUDIO:
            LOG.debug("Requesting playback: PlaybackType.AUDIO")
            # TODO - get preferred service and pass to self.play
            self.audio_service.play(self.now_playing.uri)

        elif self.playback_type == PlaybackType.SKILL:
            # skill wants to handle playback
            LOG.debug("Requesting playback: PlaybackType.SKILL")
            self.bus.emit(Message(f'ovos.common_play.{self.now_playing.skill_id}.play',
                                  self.now_playing.infocard))
            self.bus.emit(Message("ovos.common_play.track.state",
                                  {"state": TrackState.PLAYING_SKILL}))

        elif self.playback_type == PlaybackType.VIDEO:
            LOG.debug("Requesting playback: PlaybackType.VIDEO")
            # TODO - get preferred service and pass to self.play
            self.video_service.play(self.now_playing.uri)

        elif self.playback_type == PlaybackType.WEBVIEW:
            LOG.debug("Requesting playback: PlaybackType.WEBVIEW")
            # TODO - get preferred service and pass to self.play
            self.web_service.play(self.now_playing.uri)

        else:
            raise ValueError("invalid playback request")

        if self.mpris:
            self.mpris.update_props({"CanGoNext": self.can_next})
            self.mpris.update_props({"CanGoPrevious": self.can_prev})

        self.set_player_state(PlayerState.PLAYING)
        self.gui.update_buttons()  # pause/play icon

    def play_shuffle(self):
        """
        Go to a random position in the playlist and set that MediaEntry as
        'now_playing` (does NOT call 'play').
        """
        LOG.debug("Shuffle == True")
        if len(self.playlist) > 1 and not self.playlist.is_last_track:
            # TODO: does the 'last track' matter in this case?
            self.playlist.set_position(random.randint(0, len(self.playlist)))
            self.set_now_playing(self.playlist.current_track)
        else:
            self.media.search_playlist.next_track()
            self.set_now_playing(self.media.search_playlist.current_track)

    def play_next(self):
        """
        Play the next track in the playlist or search results.
        End playback if there is no next track, accounting for repeat and
        shuffle settings.
        """
        if self.playback_type in [PlaybackType.MPRIS]:
            if self.mpris:
                self.mpris.play_next()
            return
        elif self.playback_type in [PlaybackType.SKILL]:
            LOG.debug(f"Defer playing next track to skill")
            self.bus.emit(Message(f'ovos.common_play.{self.now_playing.skill_id}.next'))
            return

        if self.loop_state == LoopState.REPEAT_TRACK:
            LOG.debug("Repeating single track")
        elif self.shuffle:
            LOG.debug("Shuffling")
            self.play_shuffle()
        elif not self.playlist.is_last_track:
            self.playlist.next_track()
            self.set_now_playing(self.playlist.current_track)
            LOG.info(f"Next track index: {self.playlist.position}")
        elif not self.media.search_playlist.is_last_track and \
                self.ocp_config.get("merge_search", True):
            while self.media.search_playlist.current_track in self.playlist:
                # Don't play media already played from the playlist
                self.media.search_playlist.next_track()
            self.set_now_playing(self.media.search_playlist.current_track)
            LOG.info(f"Next search index: "
                     f"{self.media.search_playlist.position}")
        else:
            if self.loop_state == LoopState.REPEAT and len(self.playlist):
                LOG.info("end of playlist, repeat == True")
                self.playlist.set_position(0)
            else:
                LOG.info("requested next, but there aren't any more tracks")
                return
        self.play()

    def play_prev(self):
        """
        Play the previous track in the playlist.
        If there is no previous track, do nothing.
        """
        if self.playback_type in [PlaybackType.MPRIS]:
            if self.mpris:
                self.mpris.play_prev()
            return
        elif self.playback_type in [PlaybackType.SKILL,
                                    PlaybackType.UNDEFINED]:
            self.bus.emit(Message(
                f'ovos.common_play.{self.now_playing.skill_id}.prev'))
            return

        if self.shuffle:
            # TODO: Should skipping back get a random track instead of previous?
            self.play_shuffle()
        elif not self.playlist.is_first_track:
            self.playlist.prev_track()
            self.set_now_playing(self.playlist.current_track)
            LOG.debug(f"Previous track index: {self.playlist.position}")
            self.play()
        else:
            LOG.debug("requested previous, but already in 1st track")

    def pause(self):
        """
        Ask the current playback to pause.
        """
        LOG.debug(f"Pausing playback: {self.playback_type}")
        if self.playback_type in [PlaybackType.AUDIO,
                                  PlaybackType.UNDEFINED]:
            self.audio_service.pause()
        if self.playback_type in [PlaybackType.VIDEO,
                                  PlaybackType.UNDEFINED]:
            self.video_service.pause()
        if self.playback_type in [PlaybackType.SKILL,
                                  PlaybackType.UNDEFINED]:
            self.bus.emit(Message(f'ovos.common_play.{self.active_skill}.pause'))
        if self.playback_type in [PlaybackType.MPRIS] and self.mpris:
            self.mpris.pause()
        self.set_player_state(PlayerState.PAUSED)
        self._paused_on_duck = False
        self.gui.update_buttons()  # pause/play icon

    def resume(self):
        """
        Ask any paused or stopped playback to resume.
        """
        LOG.debug(f"Resuming playback: {self.playback_type}")
        if self.playback_type in [PlaybackType.AUDIO,
                                  PlaybackType.UNDEFINED]:
            self.audio_service.resume()

        if self.playback_type in [PlaybackType.SKILL,
                                  PlaybackType.UNDEFINED]:
            self.bus.emit(Message(f'ovos.common_play.{self.active_skill}.resume'))

        if self.playback_type in [PlaybackType.VIDEO]:
            self.video_service.resume()

        if self.playback_type in [PlaybackType.MPRIS] and self.mpris:
            self.mpris.resume()

        self.set_player_state(PlayerState.PLAYING)
        self.gui.update_buttons()  # pause/play icon

    def seek(self, position: int):
        """
        Request playback to go to a specific position in the current media
        @param position: milliseconds position to seek to
        """
        if self.playback_type in [PlaybackType.AUDIO,
                                  PlaybackType.UNDEFINED]:
            self.audio_service.set_track_position(position / 1000)

    def stop(self):
        """
        Request stopping current playback and searching
        """
        # stop any search still happening
        self.bus.emit(Message("ovos.common_play.search.stop"))

        LOG.debug("Stopping playback")
        if self.playback_type in [PlaybackType.AUDIO,
                                  PlaybackType.UNDEFINED]:
            self.audio_service.stop()
        if self.playback_type in [PlaybackType.SKILL,
                                  PlaybackType.UNDEFINED]:
            self.stop_skill()
        if self.playback_type in [PlaybackType.VIDEO,
                                  PlaybackType.UNDEFINED]:
            self.video_service.stop()
        if self.playback_type in [PlaybackType.WEBVIEW,
                                  PlaybackType.UNDEFINED]:
            self.web_service.stop()
        if self.mpris and self.playback_type in [PlaybackType.MPRIS]:
            self.mpris.pause()
        self.set_player_state(PlayerState.STOPPED)
        self.gui.update_buttons()  # pause/play icon

    def handle_MPRIS_takeover(self):
        """ Called when a MPRIS external player becomes active"""
        self.audio_service.stop()
        self.video_service.stop()
        self.web_service.stop()
        self.stop_skill()
        self.now_playing.original_uri = ""

    def stop_skill(self):
        """
        Emit a Message notifying self.active_skill to stop
        """
        self.bus.emit(Message(f'ovos.common_play.{self.active_skill}.stop'))

    def reset(self):
        """
        Reset this instance to clear any media or settings
        """
        self.now_playing.reset()
        self.playlist.clear()
        self.media.clear()
        if self.playback_type != PlaybackType.MPRIS:
            self.set_media_state(MediaState.NO_MEDIA)
        self.shuffle = False
        self.loop_state = LoopState.NONE
        self.state: PlayerState = PlayerState.STOPPED

    def shutdown(self):
        """
        Shutdown this instance and its spawned objects. Remove events.
        """
        self.stop()
        if self.mpris:
            self.mpris.shutdown()
        self.now_playing.shutdown()
        self.media.shutdown()

    # player -> common play
    def handle_player_state_update(self, message):
        """
        Handles 'ovos.common_play.player.state' messages with player state updates
        @param message: Message providing new "state" data
        """
        state = message.data.get("state")
        if state is None:
            raise ValueError(f"Got state update message with no state: "
                             f"{message}")
        if isinstance(state, int):
            state = PlayerState(state)
        if not isinstance(state, PlayerState):
            raise ValueError(f"Expected int or PlayerState, but got: {state}")
        if state == self.state:
            return
        LOG.info(f"PlayerState changed: {repr(self.state)} -> {repr(state)}")
        if state == PlayerState.PLAYING:
            self.state = PlayerState.PLAYING
        elif state == PlayerState.PAUSED:
            self.state = PlayerState.PAUSED
        elif state == PlayerState.STOPPED:
            self.state = PlayerState.STOPPED

        if self.mpris:
            state2str = {PlayerState.PLAYING: "Playing",
                         PlayerState.PAUSED: "Paused",
                         PlayerState.STOPPED: "Stopped"}
            self.mpris.update_props({"CanPause": state == PlayerState.PLAYING,
                                     "CanPlay": state == PlayerState.PAUSED,
                                     "PlaybackStatus": state2str[state]})
        self.gui.update_buttons()  # update icons

    def handle_player_media_update(self, message):
        """
        Handles 'ovos.common_play.media.state' messages with media state updates
        @param message: Message providing new "state" data
        """
        state = message.data.get("state")
        if state is None:
            raise ValueError(f"Got state update message with no state: "
                             f"{message}")
        if isinstance(state, int):
            state = MediaState(state)
        if not isinstance(state, MediaState):
            raise ValueError(f"Expected int or MediaState, but got: {state}")
        if state == self.media_state:
            return
        LOG.info(f"MediaState changed: {repr(self.media_state)} -> {repr(state)}")
        self.media_state = state
        if state == MediaState.END_OF_MEDIA:
            self.handle_playback_ended(message)
        elif state == MediaState.INVALID_MEDIA:
            self.handle_invalid_media(message)
            if self.ocp_config.get("autoplay", True):
                self.play_next()
        self.gui.update_buttons()  # update icons

    def handle_invalid_media(self, message):
        self.gui.manage_display(OCPGUIState.PLAYBACK_ERROR)

    def handle_playback_ended(self, message):
        if len(self.playlist) and self.ocp_config.get("autoplay", True) and \
                self.playback_type not in [PlaybackType.MPRIS, PlaybackType.UNDEFINED]:
            # PlaybackType.UNDEFINED -> no media loaded, eg stop called explicitly
            # PlaybackType.MPRIS -> can't load media in MPRIS players
            LOG.debug(f"Playing next track")
            self.play_next()
            return

        LOG.info("Playback ended")
        # show search for 60 seconds and exit to homescreen
        self.gui.manage_display(OCPGUIState.DISAMBIGUATION, timeout=60)

    # ovos common play bus api requests
    def handle_play_request(self, message):
        LOG.debug("Received OCP playback request")
        repeat = message.data.get("repeat", False)
        if repeat:
            self.loop_state = LoopState.REPEAT

        media = message.data.get("media")
        playlist = message.data.get("playlist") or [media]
        disambiguation = message.data.get("disambiguation") or playlist

        self.play_media(media, disambiguation, playlist)

    def handle_pause_request(self, message):
        self.pause()
        # if mpris, wait for its status report instead to avoid flickering
        if not self.mpris:
            self.gui.update_buttons()  # update icon

    def handle_stop_request(self, message):
        self.stop()
        self.reset()
        # if mpris, wait for its status report instead to avoid flickering
        if not self.mpris:
            self.gui.update_buttons()  # update icon

    def handle_resume_request(self, message):
        self.resume()
        # if mpris, wait for its status report instead to avoid flickering
        if not self.mpris:
            self.gui.update_buttons()  # update icon

    def handle_seek_request(self, message):
        # from bus api
        miliseconds = message.data.get("seconds", 0) * 1000

        # from audio player GUI
        position = message.data.get("seekValue")
        if not position:
            position = self.now_playing.position or 0
            if self.playback_type in [PlaybackType.AUDIO,
                                      PlaybackType.UNDEFINED]:
                position = self.audio_service.get_track_position() or position
            position += miliseconds
        self.seek(position)

    def handle_next_request(self, message):
        self.play_next()

    def handle_prev_request(self, message):
        self.play_prev()

    def handle_set_shuffle(self, message):
        self.shuffle = True
        # if mpris, wait for its status report instead to avoid flickering
        if not self.mpris:
            self.gui.update_buttons()  # update icon

    def handle_unset_shuffle(self, message):
        self.shuffle = False
        # if mpris, wait for its status report instead to avoid flickering
        if not self.mpris:
            self.gui.update_buttons()  # update icon

    def handle_set_repeat(self, message):
        self.loop_state = LoopState.REPEAT
        # if mpris, wait for its status report instead to avoid flickering
        if not self.mpris:
            self.gui.update_buttons()  # update icon

    def handle_unset_repeat(self, message):
        self.loop_state = LoopState.NONE
        # if mpris, wait for its status report instead to avoid flickering
        if not self.mpris:
            self.gui.update_buttons()  # update icon

    # playlist control bus api
    def handle_repeat_toggle_request(self, message):
        if self.loop_state == LoopState.REPEAT_TRACK:
            self.loop_state = LoopState.NONE
        elif self.loop_state == LoopState.REPEAT:
            self.loop_state = LoopState.REPEAT_TRACK
        elif self.loop_state == LoopState.NONE:
            self.loop_state = LoopState.REPEAT
        LOG.info(f"Repeat: {self.loop_state}")
        if self.mpris and self.playback_type == PlaybackType.MPRIS:
            self.mpris.toggle_repeat()
        else:  # if mpris, wait for its status report instead to avoid flickering
            self.gui.update_buttons()  # update icon

    def handle_shuffle_toggle_request(self, message):
        self.shuffle = not self.shuffle
        LOG.info(f"Shuffle: {self.shuffle}")
        if self.mpris and self.playback_type == PlaybackType.MPRIS:
            self.mpris.toggle_shuffle()
        else:  # if mpris, wait for its status report instead to avoid flickering
            self.gui.update_buttons()  # update icon

    def handle_playlist_set_request(self, message):
        self.playlist.clear()
        self.handle_playlist_queue_request(message)

    def handle_playlist_queue_request(self, message):
        for track in message.data["tracks"]:
            self.playlist.add_entry(track)

    def handle_playlist_clear_request(self, message):
        self.playlist.clear()

    # audio ducking - NB: we distinguish ducking vs corking  (lower volume vs pause)
    def handle_cork_request(self, message):
        """
        Pause audio on 'recognizer_loop:record_begin'
        @param message: Message associated with event
        """
        if self.state == PlayerState.PLAYING:
            self.pause()
            self._paused_on_duck = True

    def handle_uncork_request(self, message):
        """
        Resume paused audio on 'recognizer_loop:record_begin'
        @param message: Message associated with event
        """
        if self.state == PlayerState.PAUSED and self._paused_on_duck:
            self.resume()
            self._paused_on_duck = False

    def handle_duck_request(self, message):
        """
        Lower volume on 'recognizer_loop:record_begin'
        @param message: Message associated with event
        """
        if self.state == PlayerState.PLAYING:
            if self.playback_type in [PlaybackType.VIDEO]:
                self.video_service.lower_volume()
            elif self.playback_type in [PlaybackType.AUDIO]:
                self.audio_service.lower_volume()
            self._paused_on_duck = True

    def handle_unduck_request(self, message):
        """
        Restore volume on 'recognizer_loop:record_begin'
        @param message: Message associated with event
        """
        if self.state == PlayerState.PAUSED and self._paused_on_duck:
            if self.playback_type in [PlaybackType.VIDEO]:
                self.video_service.restore_volume()
            elif self.playback_type in [PlaybackType.AUDIO]:
                self.audio_service.restore_volume()
            self._paused_on_duck = False

    # track data
    def handle_track_length_request(self, message):
        l = self.now_playing.length
        if self.playback_type == PlaybackType.AUDIO:
            l = self.audio_service.get_track_length() or l
        data = {"length": l}
        self.bus.emit(message.response(data))

    def handle_track_position_request(self, message):
        pos = self.now_playing.position
        if self.playback_type == PlaybackType.AUDIO:
            pos = self.audio_service.get_track_position() or pos
        data = {"position": pos}
        self.bus.emit(message.response(data))

    def handle_set_track_position_request(self, message):
        miliseconds = message.data.get("position")
        self.seek(miliseconds)

    def handle_track_info_request(self, message):
        data = self.now_playing.as_dict
        self.bus.emit(message.response(data))

    # internal info
    def handle_list_backends_request(self, message):
        data = self.audio_service.available_backends()
        self.bus.emit(message.response(data))

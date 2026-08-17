import threading
from os.path import dirname
from threading import RLock
from typing import List, Optional, Union

from json_database import JsonStorageXDG

from ovos_bus_client import MessageBusClient
from ovos_config import Configuration
from ovos_config.meta import get_xdg_base
from ovos_media.media_backends import AudioService, VideoService, WebService
from ovos_media.mpris import OcpMprisExporter
from ovos_media.bus.api import OCPBusApi
from ovos_media.utils import is_default_session
from ovos_media.bus.schemas import (decode_media_state, decode_playlist_tracks,
                                    decode_seek, decode_track_position,
                                    flatten_media_types, validated_entries)
from ovos_media.player.queue import (AllFailed, KeepCurrent, PlayQueue,
                                     QueueEnd)
from ovos_media.player.now_playing import NowPlaying
from ovos_plugin_manager.ocp import load_stream_extractors
from ovos_plugin_manager.templates.media import MediaBackend
from ovos_utils.log import LOG
from ovos_bus_client.message import Message
from ovos_utils.ocp import MediaType, Playlist
from ovos_utils.ocp import OCP_ID, PlayerState, LoopState, PlaybackType, PlaybackMode, TrackState, MediaState, \
    MediaEntry, PluginStream
from ovos_workshop.decorators.ocp import ocp_search
from ovos_workshop.skills.common_play import OVOSCommonPlaybackSkill


# locale/ and qt5/ live in the ovos_media package, one level above this
# subpackage; the catalog skill reads its dialogs, intents and icons from there
RESOURCES_DIR = dirname(dirname(__file__))


class OCPMediaCatalog(OVOSCommonPlaybackSkill):
    def __init__(self, *args, validate_source: bool = True, **kwargs):
        kwargs.setdefault("resources_dir", RESOURCES_DIR)
        super().__init__(*args, **kwargs)
        # mirrors the bus edge's session gate: keeps playback-affecting
        # intent handlers (shuffle on/off) on the local/"default" session, unless the owning service was configured
        # with media.validate_source: false (satellite acting on everything)
        self.validate_source = validate_source
        self.skill_icon = f"{RESOURCES_DIR}/qt5/images/liked.svg"

        self.liked_songs = JsonStorageXDG("OCP_liked_songs",
                                          subfolder=get_xdg_base())
        # Guards every liked_songs mutation + store() together, and every
        # unlocked read that iterates the dict (eg. liked_songs_playlist);
        # store() does a json.dump that iterates the dict, and
        # handle_like/handle_unlike/play()'s play-count block all mutate it
        # from separate bus-dispatch threads. OCPMediaPlayer aliases its
        # own _liked_songs_lock to this one so writers and readers share it.
        self.liked_songs_lock = RLock()
        LOG.debug(f"Liked songs playlist loaded: {self.liked_songs.path}")
        self.search_playlist = Playlist()
        self.ocp_skills = {}
        self.featured_skills = {}
        # TODO - add search results clear/replace events

        # register keywords
        def norm_name(n):
            return n.split("|")[0].split("(")[0].split("[")[0].split("{")[0].split("-")[0].strip()

        # ahocorasick_ner ("ner" extra) is OPTIONAL, but its absence is not
        # a pure speed optimization: OVOSCommonPlaybackSkill.ocp_voc_match
        # (used by search_db, see below) hard-depends on it, so without it
        # "play my liked songs" / "play my favorites" style searches never
        # match anything. It is still true that the five WhatSong/WhatAlbum/
        # WhatArtist/ShuffleOn/ShuffleOff intents registered below do not
        # depend on it and keep working.
        #
        # register_ocp_keyword() itself does two things: it registers the
        # samples with the local Aho-Corasick NER matcher (raises
        # ImportError without the "ner" extra), and it emits
        # 'ovos.common_play.register_keyword' on the bus so the OCP pipeline
        # classifier learns the keywords too - that emit does not need local
        # NER. In the installed ovos-workshop, the emit happens *after* the
        # per-language NER registration loop inside the same method, so an
        # ImportError there prevents the emit from ever running. We
        # replicate the (NER-independent) emit here directly so the
        # classifier still learns the keywords even without the "ner" extra.
        # liked-songs is a persisted JSON store editable outside this
        # process (GUI, manual edits, older/newer schema versions). A single
        # malformed entry (a non-dict value, or a dict missing "title") used
        # to raise here and kill daemon startup entirely. Skip and warn
        # instead — mirrors the defensive .get() style liked_songs_playlist
        # already uses.
        liked_titles = []
        for uri, song in self.liked_songs.items():
            if not isinstance(song, dict):
                LOG.warning(f"Skipping malformed liked song entry {uri!r}: "
                           f"expected a dict, got {type(song).__name__}")
                continue
            title = song.get("title", "")
            if not title:
                LOG.warning(f"Skipping liked song entry {uri!r}: missing/empty title")
                continue
            liked_titles.append(norm_name(title))

        try:
            self.register_ocp_keyword(MediaType.MUSIC, "song_name", liked_titles)
            self.register_ocp_keyword(MediaType.MUSIC, "playlist_name",
                                      ["favorite", "liked", "favorites",
                                       "favorite songs", "favorite tracks",
                                       "favorite music", "my favorite songs",
                                       "my favorite tracks", "my favorite music",
                                       "liked songs", "liked tracks", "liked music",
                                       "my liked songs", "my liked tracks", "my liked music"])
        except ImportError:
            LOG.warning("ahocorasick_ner not installed - OCP local keyword "
                       "NER matching disabled, and 'search_db' (eg. 'play "
                       "my liked songs') will find nothing until it is "
                       "installed. Install the 'ner' extra to fix this. "
                       "The classifier is still informed of the keywords "
                       "via the bus so media-type disambiguation still "
                       "works.")
            self._emit_ocp_keyword_registration(
                MediaType.MUSIC, "song_name", liked_titles)
            self._emit_ocp_keyword_registration(
                MediaType.MUSIC, "playlist_name",
                ["favorite", "liked", "favorites",
                 "favorite songs", "favorite tracks",
                 "favorite music", "my favorite songs",
                 "my favorite tracks", "my favorite music",
                 "liked songs", "liked tracks", "liked music",
                 "my liked songs", "my liked tracks", "my liked music"])

        # intents about the currently playing media, see issue #23
        self.register_intent_file("WhatSong.intent", self.handle_what_song)
        self.register_intent_file("WhatAlbum.intent", self.handle_what_album)
        self.register_intent_file("WhatArtist.intent", self.handle_what_artist)
        self.register_intent_file("ShuffleOn.intent", self.handle_shuffle_on)
        self.register_intent_file("ShuffleOff.intent", self.handle_shuffle_off)

    def _emit_ocp_keyword_registration(self, media_type: MediaType, label: str,
                                       samples: List[str]) -> None:
        """
        Emit the 'ovos.common_play.register_keyword' bus message that
        informs the OCP pipeline classifier about a set of keyword samples,
        WITHOUT going through OVOSCommonPlaybackSkill.register_ocp_keyword
        (which also registers the samples with the local Aho-Corasick NER
        matcher and requires the optional "ner" extra to be installed).

        This mirrors the (NER-independent) tail half of
        OVOSCommonPlaybackSkill.register_ocp_keyword: same message name and
        same payload shape, so the classifier cannot tell the difference.
        Used as a fallback when ahocorasick_ner is not installed.
        """
        samples = list(set(samples))
        for lang in self.native_langs:
            if len(samples) >= 20:
                csv_path = f"{self.ocp_cache_dir}/{self.skill_id}_{label}_{lang}.csv"
                with open(csv_path, "w") as f:
                    f.write("label,sample")
                    for s in samples:
                        f.write(f"\n{label},{s}")
                self.bus.emit(
                    Message('ovos.common_play.register_keyword',
                            {"skill_id": self.skill_id,
                             "label": label,
                             "csv": csv_path,
                             "media_type": media_type}))
            else:
                self.bus.emit(
                    Message('ovos.common_play.register_keyword',
                            {"skill_id": self.skill_id,
                             "label": label,
                             "samples": samples,
                             "media_type": media_type}))

    def _get_status(self, message: Message) -> Optional[dict]:
        """Query current player status via the existing status bus API.

        Reuses the 'ovos.common_play.status' request/response messages that
        OCPMediaPlayer.handle_status already answers; this avoids adding any
        new bus message types or coupling this skill directly to the
        OCPMediaPlayer instance.

        The request is forwarded from the triggering intent message so the
        session context (session_id, lang, etc) is preserved on the wire.

        Returns None if no response was received within the timeout (player
        not responding), as opposed to an empty dict, which means the player
        answered but nothing is currently playing.
        """
        response = self.bus.wait_for_response(message.forward("ovos.common_play.status"),
                                               timeout=3)
        return response.data if response else None

    # WhatSong/WhatAlbum/WhatArtist are deliberately UN-gated by session:
    # they mirror OCPMediaPlayer.handle_status, which itself answers every
    # session's "ovos.common_play.status" query with the single shared
    # player's state (its bus topic is not gated).
    # The consistency rule this repo follows is that each intent front-end
    # mirrors its backing handler's own gating - handle_status is global
    # read-only state, so these read handlers stay global too. Only the
    # shuffle on/off handlers below are gated, because they mirror
    # handle_set_shuffle/handle_unset_shuffle, which ARE gated.
    def handle_what_song(self, message):
        status = self._get_status(message)
        if status is None:
            self.speak_dialog("player.not.responding")
            return
        title = status.get("title")
        artist = status.get("artist")
        if not title:
            self.speak_dialog("nothing.playing")
        elif artist:
            self.speak_dialog("now.playing.song", {"title": title, "artist": artist})
        else:
            self.speak_dialog("now.playing.song.no.artist", {"title": title})

    def handle_what_album(self, message):
        status = self._get_status(message)
        if status is None:
            self.speak_dialog("player.not.responding")
            return
        if not status.get("title"):
            self.speak_dialog("nothing.playing")
        else:
            # NowPlaying/MediaEntry does not track album metadata, so this
            # always falls back gracefully instead of guessing or crashing.
            self.speak_dialog("no.album.info")

    def handle_what_artist(self, message):
        status = self._get_status(message)
        if status is None:
            self.speak_dialog("player.not.responding")
            return
        title = status.get("title")
        artist = status.get("artist")
        if not title:
            self.speak_dialog("nothing.playing")
        elif artist:
            self.speak_dialog("now.playing.artist", {"artist": artist})
        else:
            self.speak_dialog("no.artist.info")

    def _is_default_session(self, message: Message) -> bool:
        """Whether the player will act on a request forwarded from this
        message, using the same rule the bus edge applies to
        'ovos.common_play.shuffle.set'/'.unset'. On a non-default (e.g.
        HiveMind satellite) session with validate_source left True the
        emitted message is silently dropped by the player - this must not be
        reported back to the user as a success. When validate_source is
        False the player WILL act on it, so this front-end must agree."""
        return is_default_session(message, self.validate_source)

    def handle_shuffle_on(self, message):
        if not self._is_default_session(message):
            self.speak_dialog("cannot.control.device")
            return
        self.bus.emit(message.forward("ovos.common_play.shuffle.set"))
        self.speak_dialog("shuffle.on")

    def handle_shuffle_off(self, message):
        if not self._is_default_session(message):
            self.speak_dialog("cannot.control.device")
            return
        self.bus.emit(message.forward("ovos.common_play.shuffle.unset"))
        self.speak_dialog("shuffle.off")

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
                "playlist": [e.as_dict for e in self.liked_songs_playlist],
                "skill_icon": self.skill_icon,
                "title": "Liked Songs",
                "skill_id": self.skill_id
            }

        if entities.get("song_name"):
            title = entities["song_name"].lower()
            for entry in self.liked_songs_playlist:
                if title not in entry.title.lower():
                    continue
                result = entry.as_dict
                result["match_confidence"] = min(base_score + 40, 100)
                result["skill_id"] = self.skill_id
                result["skill_icon"] = self.skill_icon
                yield result

    @property
    def liked_songs_playlist(self) -> List[MediaEntry]:
        # canonicalize the persisted liked-songs store (raw dicts) into
        # MediaEntry objects; match_confidence tracks play_count so the entries
        # sort most-played-first once handed to a Playlist.
        # tolerate catalogs constructed via __new__ (bypassing __init__)
        # that never set liked_songs_lock
        lock = getattr(self, "liked_songs_lock", None) or RLock()
        with lock:
            items = list(self.liked_songs.items())
        entries = [MediaEntry(uri=uri,
                              title=song.get("title", ""),
                              artist=song.get("artist", ""),
                              image=song.get("image", ""),
                              media_type=MediaType.MUSIC,
                              playback=PlaybackType.AUDIO,
                              match_confidence=song.get("play_count", 0) + 50)
                   for uri, song in items]
        return sorted(entries, key=lambda e: e.match_confidence, reverse=True)

    def handle_skill_announce(self, message):
        skill_id = message.data.get("skill_id")
        skill_name = message.data.get("skill_name") or skill_id
        img = message.data.get("image") or message.data.get("thumbnail")
        has_featured = bool(message.data.get("featured_tracks"))
        media_types = message.data.get("media_types") or \
                      message.data.get("media_type") or \
                      [MediaType.GENERIC]
        media_types = flatten_media_types(media_types)

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

    def get_featured_skills(self, adult: bool = False) -> list:
        """Emit a skills-get broadcast and return the currently registered featured skills.

        The 200 ms wait allows in-process skill announcements to arrive before
        the list is read.  A threading.Event is used instead of time.sleep so
        the call can be interrupted by a shutdown signal in future work.
        """
        self.bus.emit(Message("ovos.common_play.skills.get"))
        threading.Event().wait(timeout=0.2)  # non-blocking sleep equivalent
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


class OCPMediaPlayer:
    """OCP Virtual Media Player

    for OVOS this is all that exists and represents all loaded and currently playing media

    "now playing" is tracked and managed by this interface
    """

    def __init__(self, bus: MessageBusClient, config: Optional[dict] = None,
                 validate_source: bool = True) -> None:
        self.bus = bus
        self.ocp_config = config or Configuration().get("media", {})
        # When True, playback-executing handlers act only on the local/"default"
        # session (see ovos_media.utils.is_default_session). A satellite
        # whose sessions are not NAT'd to "default" by hivemind-core should set
        # this False so its embedded ovos-media acts on all sessions.
        self.validate_source = validate_source

        self.state: PlayerState = PlayerState.STOPPED
        self.loop_state: LoopState = LoopState.NONE
        self.media_state: MediaState = MediaState.NO_MEDIA
        self.shuffle: bool = False
        self.track_history: dict = {}  # Dict of track URI to play count
        self.media: OCPMediaCatalog = OCPMediaCatalog(bus=bus, skill_id=OCP_ID + ".favorites",
                                                       validate_source=self.validate_source)
        self._init_runtime_state()
        # the owned queue, also the container the rest of the world reads as
        # "the playlist" (bus status, MPRIS track list)
        self.playlist: PlayQueue = self._queue

        self.now_playing: NowPlaying = NowPlaying(bus, player=self)
        # BaseMediaService.stop() calls this on_stop callback to flag the
        # stop on the player BEFORE the backend's ocp_stop() emits
        # END_OF_MEDIA, in the same thread. A bus subscription would have
        # had no such ordering guarantee against the END_OF_MEDIA it causes.
        self.audio_service = AudioService(bus, on_stop=self._mark_stop_requested)
        self.video_service = VideoService(bus, on_stop=self._mark_stop_requested)
        self.web_service = WebService(bus, on_stop=self._mark_stop_requested)
        self.current: Optional[MediaBackend] = None
        self.mpris: Optional[OcpMprisExporter] = None

        # MPRIS settings
        manage_players = self.ocp_config.get("manage_external_players", False)
        if self.ocp_config.get("enable_mpris", False) is False:
            LOG.info("MPRIS integration is disabled")
        else:
            self.mpris = OcpMprisExporter(self, config=self.ocp_config,
                                          manage_players=manage_players)

        # every bus subscription of this player, its NowPlaying and its
        # OCPMediaCatalog lives in one registration table (see
        # ovos_media.bus.api), which is also what shutdown() tears down.
        self.bus_api = OCPBusApi(bus, player=self)
        self._report_to_core()

    def _report_to_core(self) -> None:
        """Broadcast the supported StreamExtractorIds and the initial player
        status, so ovos-core learns this daemon's capabilities and state
        without having to ask for them."""
        self.handle_get_SEIs(Message("ovos.common_play.SEI.get"))
        self.handle_status(Message("ovos.common_play.status"))

    def _init_runtime_state(self) -> None:
        """Initialise the plain in-memory playback bookkeeping.

        Split out of __init__ so it can be applied on its own to a player whose
        heavyweight collaborators (services, GUI, MPRIS) are supplied by other
        means.
        """
        self._paused_on_duck: bool = False
        # Guards every read-modify-write of media_state/player_state
        # (including the compare-and-set in set_media_state/set_player_state)
        # so the END_OF_MEDIA compare-and-set cannot be executed twice
        # concurrently (two racing END_OF_MEDIA events used to double-advance
        # the queue). Reentrant because play() -> on_invalid_stream()
        # re-enters, and because set_player_state() calls handle_status()
        # which may itself touch player/media state.
        self._state_lock = RLock()
        # Alias of self.media.liked_songs_lock: guards every liked_songs
        # mutation + store() together, and every unlocked read that
        # iterates the dict. store() does a json.dump that iterates the
        # dict, and handle_like/handle_unlike/play()'s play-count block all
        # mutate it from separate bus-dispatch threads; without this a
        # store() racing a pop() raises "dictionary changed size during
        # iteration". Kept as a shared lock (not a private one) so
        # OCPMediaCatalog.liked_songs_playlist can snapshot under the same
        # lock writers use. Falls back to a private RLock when applied
        # standalone (eg. tests calling _init_runtime_state() before
        # self.media exists) so it stays usable outside full __init__.
        media = getattr(self, "media", None)
        self._liked_songs_lock = media.liked_songs_lock if media is not None else RLock()
        # True between a stop request and the next play(). An explicit stop
        # must NOT advance the queue, but OPM backends emit END_OF_MEDIA from
        # ocp_stop(), so a stop is indistinguishable from a natural track end at
        # the media.state level without this flag.
        self._stop_requested: bool = False
        # owns the user queue, the identity of the selected entry and the
        # failed-uri bookkeeping, and answers every "which track is next"
        # question; what to DO with the answer stays player policy
        self._queue: PlayQueue = PlayQueue(title="Search Results")
        self._invalid_timer: Optional[threading.Timer] = None
        # retry delay after an invalid stream, seconds (overridable in tests)
        self.invalid_stream_delay: float = 3.0
        # rate-limit "track.failed" to once per queue (cleared alongside
        # _failed_uris, ie. whenever a track successfully loads or the
        # player is reset) rather than once per skipped track
        self._track_failed_spoken: bool = False
        # True once "no.playback.backend" has been spoken for the lifetime
        # of this player — spoken only at the very first play attempt that
        # finds zero backends loaded, never again
        self._no_backend_dialog_spoken: bool = False

    # views on the owned queue's bookkeeping
    @property
    def _current_entry(self) -> Optional[MediaEntry]:
        return self._queue.current

    @_current_entry.setter
    def _current_entry(self, entry: Optional[MediaEntry]):
        self._queue.current = entry

    @property
    def _failed_uris(self) -> set:
        return self._queue.failed

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
        if not uri:
            # nothing playing and no uri in the request — persisting
            # under the empty-string key would create an unremovable store
            # entry (handle_unlike keys off "uri or now_playing.original_uri"
            # too, so it would never be able to target it), pollute the
            # liked-songs playlist, and broadcast an empty-string keyword
            # sample to the NER matcher on the next boot.
            LOG.warning("Cannot like: nothing is playing and no uri was given")
            self.media.speak_dialog("nothing.playing")
            return
        title = message.data.get("title") or self.now_playing.title
        image = message.data.get("image") or message.data.get("thumbnail") or self.now_playing.image
        artist = message.data.get("artist") or self.now_playing.artist
        with self._liked_songs_lock:
            self.media.liked_songs[uri] = {"title": title, "artist": artist,
                                           "image": image, "uri": uri}
            self.media.liked_songs.store()
        LOG.info(f"liked song: {uri}")
        self.bus.emit(message.forward("mycroft.audio.play_sound",
                                      {"uri": "snd/acknowledge.mp3"}))

    def handle_unlike(self, message):
        # sent from GUI or intent
        uri = message.data.get("uri") or self.now_playing.original_uri
        with self._liked_songs_lock:
            if uri in self.media.liked_songs:
                self.media.liked_songs.pop(uri)
                self.media.liked_songs.store()
                LOG.info(f"unliked song: {uri}")

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

    def _merged_queue(self) -> List[MediaEntry]:
        """Return the merged, deduplicated playback queue: the user queue plus
        the search results, unless ``merge_search`` is disabled in config."""
        return self._queue.merged(self.search_results,
                                  self.ocp_config.get("merge_search", True),
                                  user_entries=self.tracks)

    def _mark_stop_requested(self):
        """Flag that the current playback is ending because it was stopped.

        Called from OCPMediaPlayer.stop() and, via the ``on_stop`` callback,
        from BaseMediaService.stop() before the backend's ocp_stop() emits
        END_OF_MEDIA.

        Also cancels any pending invalid-stream retry timer. Without this,
        a stop arriving during the post-INVALID_MEDIA retry window left the
        timer armed, and it fired play_next() after the stop had already
        settled the player into STOPPED — spontaneously resuming playback.
        """
        with self._state_lock:
            self._stop_requested = True
            if self._invalid_timer is not None:
                self._invalid_timer.cancel()
                self._invalid_timer = None

    def _locator_uri(self, uri: str = None) -> Optional[str]:
        """The uri to locate the current track by, when entry identity does
        not resolve it: an explicitly given one, else the now-playing uri."""
        return uri or (self.now_playing.uri if self.now_playing else None)

    def _locator_position(self) -> Optional[int]:
        """The queue pointer, used as a locator hint when it agrees with the
        uri being looked for."""
        return getattr(self.playlist, "position", None)

    def _queue_index(self, queue: List[MediaEntry], uri: str = None) -> int:
        """Return the index of the currently selected track in *queue*, or -1."""
        return self._queue.index(queue, uri=self._locator_uri(uri),
                                 position=self._locator_position())

    @property
    def can_prev(self) -> bool:
        """
        Return true if there is a previous track in the queue to skip to
        """
        if self.playback_type == PlaybackType.MPRIS:
            return True
        return self._queue.has_prev(self._merged_queue(),
                                    uri=self._locator_uri(),
                                    position=self._locator_position())

    @property
    def can_next(self) -> bool:
        """
        Return true if there is a next track in the queue to skip to
        """
        if self.loop_state != LoopState.NONE or \
                self.shuffle or \
                self.playback_type == PlaybackType.MPRIS:
            return True
        return self._queue.has_next(self._merged_queue(),
                                    uri=self._locator_uri(),
                                    position=self._locator_position())

    # state
    def set_media_state(self, state: MediaState):
        """
        Set self.media_state and emit an event announcing this state change.
        @param state: New MediaState
        """
        if not isinstance(state, MediaState):
            raise TypeError(f"Expected MediaState and got: {state}")
        # the compare-and-set must happen under _state_lock, same as
        # every other read-modify-write of media_state; the emit stays
        # outside the critical section to avoid lock-order risk with any
        # bus handler that re-enters and takes the lock itself.
        with self._state_lock:
            if state == self.media_state:
                return
            self.media_state = state
        self.bus.emit(Message("ovos.common_play.media.state",
                              {"state": state}))

    def set_player_state(self, state: PlayerState):
        """
        Set self.state, update MPRIS (if available), and emit an
        event announcing this state change.
        @param state: New PlayerState
        """
        if not isinstance(state, PlayerState):
            raise TypeError(f"Expected PlayerState and got: {state}")
        # same rationale as set_media_state() — compare-and-set under
        # _state_lock, emit (and the MPRIS/GUI/status side effects below)
        # outside the critical section.
        with self._state_lock:
            if state == self.state:
                return
            self.state = state
        self.bus.emit(Message("ovos.common_play.player.state",
                              {"state": state}))
        state2str = {PlayerState.PLAYING: "Playing",
                     PlayerState.PAUSED: "Paused",
                     PlayerState.STOPPED: "Stopped"}
        if self.mpris:
            self.mpris.update_props({"CanPause": state == PlayerState.PLAYING,
                                     "CanPlay": state == PlayerState.PAUSED,
                                     "PlaybackStatus": state2str[state]})
        self.handle_status(Message("ovos.common_play.status"))  # report full status to ovos-core

    def set_now_playing(self, track: Union[dict, MediaEntry, Playlist]):
        """
        Set `track` as the currently playing media, update the playlist, and
        notify any MPRIS clients. Adds `track` to `playlist`
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
        # remember the exact entry object so _queue_index() can locate it by
        # identity even when its uri is duplicated in the queue or cleared by
        # the END_OF_MEDIA reset.
        self._current_entry = track if isinstance(track, MediaEntry) else None
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
            self._current_entry = self.playlist[0]

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

    def set_external_now_playing(self, data: dict):
        """Reflect an **external** MPRIS player's track as OCP now_playing.

        This is the playback-less path: it updates now_playing + player/media
        state to mirror a player OCP does not itself drive (Spotify, a browser,
        VLC, …), so bus subscribers and voice queries see what's actually
        playing, WITHOUT invoking any OCP backend (``PlaybackType.MPRIS`` is
        external).

        Used both in-process by :class:`~ovos_media.mpris.OcpMprisExporter` and,
        out-of-process, by the standalone ``ovos-media-plugin-mpris`` watcher via
        the ``ovos.common_play.mpris.now_playing`` bus message.

        Args:
            data: external track metadata. Recognised keys: ``external_player``
                (or ``skill_id``) — the MPRIS bus name; ``title``/``artist``/
                ``image``/``length`` (ms); ``state`` — ``"Playing"`` (default),
                ``"Paused"`` or ``"Stopped"``; optional ``skill_icon``.
        """
        player_id = data.get("external_player") or data.get("skill_id")
        if not player_id:
            LOG.warning("external MPRIS now_playing with no player id; ignoring")
            return
        state = data.get("state") or "Playing"

        # an external player taking over playback should stop OCP's own backends
        # first so the two don't overlap. Only on the transition (a different/new
        # external player starting to play), and BEFORE active_skill is switched
        # so stop_skill targets the currently-active skill, not the new player.
        is_new_external = (self.playback_type != PlaybackType.MPRIS or
                           self.active_skill != player_id)
        if state == "Playing" and is_new_external:
            self.handle_MPRIS_takeover()

        self.active_skill = player_id
        self.playback_type = PlaybackType.MPRIS

        data = dict(data)
        data.setdefault("skill_id", player_id)
        data["playback"] = PlaybackType.MPRIS
        data["status"] = TrackState.PLAYING_MPRIS
        data["bg_image"] = (data.get("bg_image") or data.get("image")
                            or data.get("thumbnail"))

        # update metadata first so subscribers see the right track for every state
        self.set_now_playing(data)
        if state == "Paused":
            self.set_player_state(PlayerState.PAUSED)
            self.set_media_state(MediaState.BUFFERED_MEDIA)
        elif state == "Stopped":
            self.set_player_state(PlayerState.STOPPED)
            self.set_media_state(MediaState.END_OF_MEDIA)
        else:  # Playing
            self.set_player_state(PlayerState.PLAYING)
            self.set_media_state(MediaState.BUFFERED_MEDIA)

    def handle_mpris_now_playing(self, message: Message):
        """Bus entry point for :meth:`set_external_now_playing`.

        Lets the out-of-process ``ovos-media-plugin-mpris`` watcher reflect an
        external player into OCP without a direct object reference.
        """
        self.set_external_now_playing(dict(message.data))

    def _resolve_preferred_service(self, media_service):
        """Resolve preferred backend from config and return the matching service instance.

        Reads ``preferred_audio_services`` / ``preferred_video_services`` /
        ``preferred_web_services`` (or the generic ``preferred_audio_services``
        fallback) from ``ocp_config`` and returns the first loaded backend whose
        name or aliases match, or ``None`` if no preference is configured.

        Args:
            media_service: AudioService / VideoService / WebService instance

        Returns:
            MediaBackend | None
        """
        preferred_names = media_service.get_preferred_players() or \
                          self.ocp_config.get("preferred_audio_services", [])
        if not preferred_names:
            return None
        for name in preferred_names:
            for backend in media_service.services:
                try:
                    if backend.name == name or name in getattr(backend, "aliases", []):
                        return backend
                except Exception as e:
                    LOG.exception(f"Failed to check backend {backend} against "
                                  f"preferred name {name!r}: {e}")
                    continue
        return None

    def _playback_mode(self) -> Optional[PlaybackMode]:
        """Read the configured ``playback_mode``, accepting both a
        ``PlaybackMode`` member and its name as a string (eg. config loaded
        from JSON/YAML, where enums round-trip as their name)."""
        mode = self.ocp_config.get("playback_mode")
        if isinstance(mode, str):
            try:
                return PlaybackMode[mode.upper()]
            except KeyError:
                LOG.warning(f"Unknown playback_mode: {mode!r}")
                return None
        if isinstance(mode, PlaybackMode):
            return mode
        return None

    # stream handling
    def validate_stream(self) -> bool:
        """
        Validate that self.now_playing is playable
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
            if self._playback_mode() == PlaybackMode.FORCE_AUDIO:
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
        LOG.warning(f"Failed to play: {self.now_playing}")
        # remember this uri as broken so LoopState.REPEAT cannot restart a
        # queue whose every track has failed (that was an unbounded hot loop:
        # 1-track repeat playlist + a backend that always reports INVALID_MEDIA).
        self._queue.mark_failed(self.now_playing.uri if self.now_playing else None)
        # Use a timer so the bus event loop is not blocked during the pause
        self._schedule_play_next()

    def _schedule_play_next(self):
        """Schedule the delayed skip-to-next-track used after a bad stream.

        Replaces any still-pending retry so a burst of INVALID_MEDIA events
        cannot pile up overlapping timers, and uses a daemon thread so a
        pending retry never keeps the process alive past shutdown.
        """
        with self._state_lock:
            if self._invalid_timer is not None:
                self._invalid_timer.cancel()
            timer = threading.Timer(self.invalid_stream_delay, self.play_next)
            timer.daemon = True
            self._invalid_timer = timer
        timer.start()

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
            try:
                track = MediaEntry.from_dict(track)
            except Exception as e:
                LOG.warning(f"Ignoring play request, track can not be "
                            f"represented as a valid media entry: {e}")
                return
            LOG.debug(f"deserialized: {track}")

        if isinstance(track, Playlist):
            playlist = track
            track = track[0]
        elif not isinstance(track, (MediaEntry, PluginStream)):
            LOG.warning(f"Ignoring play request, track can not be "
                        f"represented as a valid media entry: {track!r}")
            return
        if isinstance(track, PluginStream):
            # set_now_playing/playlist machinery only understand MediaEntry;
            # as_media_entry maps extractor_id/stream onto uri the same way
            # PluginStream.extract_uri does, so extract_stream() resolves it
            # via the same stream_xtract call later on.
            track = track.as_media_entry

        if self.mpris:
            self.mpris.stop()

        # spoken once, at the very first play attempt, when zero backends of
        # any kind are loaded (no.playback.backend). Never repeated after
        # that — an install with no backend plugin fails every play request
        # the same way, so nagging on every subsequent attempt is just noise.
        if not self._no_backend_dialog_spoken and not (
                self.audio_service.services or self.video_service.services or
                self.web_service.services):
            self._no_backend_dialog_spoken = True
            try:
                self.media.speak_dialog("no.playback.backend")
            except Exception as e:
                LOG.exception(f"Failed to speak no.playback.backend dialog: {e}")

        if disambiguation:
            valid_disambiguation = validated_entries(disambiguation)
            if valid_disambiguation:
                self.media.search_playlist.replace([t for t in valid_disambiguation
                                                    if t not in self.media.search_playlist])
                self.media.search_playlist.sort_by_conf()
        if playlist:
            valid_playlist = validated_entries(playlist)
            if valid_playlist:
                self.playlist.replace(valid_playlist)
        if track in self.playlist:
            self.playlist.goto_track(track)
        self.set_now_playing(track)
        self.play()

    def play(self):
        """
        Start playback of the current `now_playing` MediaEntry. Updates
        track history, emits events for any listeners, and updates mpris
        (if configured).
        """
        # stop any external media players
        if self.mpris and not self.mpris.stop_event.is_set():
            self.mpris.stop()

        # handle_player_media_update dedups on `state == self.media_state`
        # (see below). Without resetting here, two unplayable tracks in a
        # row both land on MediaState.INVALID_MEDIA, so the second INVALID_MEDIA
        # is silently swallowed by the dedup guard and the bad-track skip
        # chain (on_invalid_stream -> play_next -> play -> ... ) stops dead,
        # wedging the player mid-PLAYING. Resetting to LOADING_MEDIA at the
        # start of every play() attempt guarantees the next real state
        # (whatever it is) always differs from the just-reset value.
        with self._state_lock:
            self.media_state = MediaState.LOADING_MEDIA
            # a new play attempt supersedes any earlier stop request
            self._stop_requested = False
            # a new play attempt supersedes any pending invalid-stream retry;
            # otherwise the stale timer fires play_next() against this NEW
            # track once the retry window elapses
            if self._invalid_timer is not None:
                self._invalid_timer.cancel()
                self._invalid_timer = None

        # switching playback types must not leave the previously active
        # backend's BaseMediaService.current set — otherwise a later,
        # unrelated global LOADED_MEDIA event revives the stale backend and
        # two backends end up playing at once. Stop/clear every backend that
        # is not the one about to be used.
        for svc, ptype in ((self.audio_service, PlaybackType.AUDIO),
                           (self.video_service, PlaybackType.VIDEO),
                           (self.web_service, PlaybackType.WEBVIEW)):
            if self.playback_type != ptype and svc.current is not None:
                try:
                    svc.current.stop()
                except Exception as e:
                    LOG.exception(f"Failed to stop inactive {svc.namespace} "
                                  f"backend on playback-type switch: {e}")
                svc.current = None

        # track play count - store() does a json.dump that iterates the
        # dict, and handle_like()/handle_unlike() can mutate it concurrently
        # from another bus-dispatch thread, so every mutation+store goes
        # through _liked_songs_lock to serialize access to the dict.
        with self._liked_songs_lock:
            entry = self.media.liked_songs.get(self.now_playing.uri)
            if entry is not None:
                entry["play_count"] = entry.get("play_count", 0) + 1
                self.media.liked_songs.store()

        # validate new stream
        if not self.validate_stream():
            LOG.warning("Stream Validation Failed")
            self.on_invalid_stream()
            return

        self.track_history.setdefault(self.now_playing.uri, 0)
        self.track_history[self.now_playing.uri] += 1

        if self.playback_type == PlaybackType.AUDIO:
            LOG.debug("Requesting playback: PlaybackType.AUDIO")
            preferred = self._resolve_preferred_service(self.audio_service)
            self.audio_service.play(self.now_playing.uri, preferred_service=preferred)

        elif self.playback_type == PlaybackType.SKILL:
            # skill wants to handle playback
            LOG.debug("Requesting playback: PlaybackType.SKILL")
            self.bus.emit(Message(f'ovos.common_play.{self.now_playing.skill_id}.play',
                                  self.now_playing.infocard))
            self.bus.emit(Message("ovos.common_play.track.state",
                                  {"state": TrackState.PLAYING_SKILL}))

        elif self.playback_type in (PlaybackType.VIDEO, PlaybackType.WEBVIEW):
            svc = self.video_service if self.playback_type == PlaybackType.VIDEO else self.web_service
            LOG.debug(f"Requesting playback: {self.playback_type}")
            preferred = self._resolve_preferred_service(svc)
            if svc.can_play(self.now_playing.uri, preferred_service=preferred):
                svc.play(self.now_playing.uri, preferred_service=preferred)
            else:
                # No installed video/web backend claims this uri (eg. a
                # headless install with only audio backends configured).
                # Degrade to audio instead of dead-ending in
                # MediaState.INVALID_MEDIA — the same fallback the old
                # GUI-presence heuristic used to trigger, now driven by
                # actual backend availability instead of GUI detection.
                LOG.warning(f"No {svc.namespace} backend can play "
                           f"{self.now_playing.uri!r}; falling back to audio")
                # The playback-type switch loop above ran while
                # self.playback_type was still VIDEO/WEBVIEW, so it left
                # this service's `current` alone (it was, at that point,
                # the intended backend). Now that we're abandoning it for
                # AUDIO, stop and clear it ourselves — same idiom as that
                # loop — or a still-playing prior track on this service
                # keeps running alongside the new audio stream.
                if svc.current is not None:
                    try:
                        svc.current.stop()
                    except Exception as e:
                        LOG.exception(f"Failed to stop abandoned {svc.namespace} "
                                      f"backend on audio fallback: {e}")
                    svc.current = None
                self.now_playing.playback = PlaybackType.AUDIO
                preferred = self._resolve_preferred_service(self.audio_service)
                if self.audio_service.can_play(self.now_playing.uri, preferred_service=preferred):
                    self.audio_service.play(self.now_playing.uri, preferred_service=preferred)
                else:
                    self.bus.emit(Message("ovos.common_play.media.state",
                                          {"state": MediaState.INVALID_MEDIA}))

        else:
            raise ValueError("invalid playback request")

        if self.mpris:
            self.mpris.update_props({"CanGoNext": self.can_next})
            self.mpris.update_props({"CanGoPrevious": self.can_prev})

        self.set_player_state(PlayerState.PLAYING)

    def play_shuffle(self) -> bool:
        """
        Go to a random position in the merged queue and set that MediaEntry as
        ``now_playing`` (does NOT call ``play``).

        Uses ``_merged_queue()`` so the shuffle pool respects the same
        deduplication and ``merge_search`` config as ``play_next``, and
        excludes any uri already recorded in ``_failed_uris`` so a shuffle
        advance never re-picks a track already known to be broken.

        @return: True if a track was selected (or there is nothing
            meaningful to shuffle to and the current track should just keep
            playing — e.g. an empty/singleton queue, or repeat-on with only
            the current, unfailed track left). False if the queue is
            non-empty but no viable (unfailed, not-currently-playing) track
            remains, or the queue is empty and the current track itself has
            already failed — the caller should treat this like the
            sequential path's "no more tracks" end-of-queue case instead of
            replaying forever.
        """
        pick = self._queue.select_shuffle(
            self._merged_queue(),
            current_uri=self.now_playing.uri if self.now_playing else None,
            repeat=self.loop_state == LoopState.REPEAT)
        if isinstance(pick, QueueEnd):
            return False
        if isinstance(pick, KeepCurrent):
            return True
        self.set_now_playing(pick)
        return True

    def _all_tracks_failed(self, queue: List[MediaEntry]) -> bool:
        """True if every track in *queue* has failed to load since the last
        successful load. Used to break the repeat cycle instead of retrying a
        wholly broken queue forever."""
        return self._queue.all_failed(queue)

    def play_next(self, finished_uri: str = None):
        """
        Play the next track in the merged queue (user playlist + search results).

        Uses ``_merged_queue()`` for O(n) deduplication — no O(n²) scanning.
        Respects repeat, shuffle, loop state, and ``merge_search`` config.

        @param finished_uri: uri of the track that just finished, captured
            before the end-of-media reset; used only as a locator fallback.
        """
        if self.playback_type in [PlaybackType.MPRIS]:
            if self.mpris and self.mpris.manage_players:
                self.mpris.play_next()
            else:
                LOG.warning("MPRIS external player control is disabled; install ovos-media-plugin-mpris to enable it")
            return
        elif self.playback_type in [PlaybackType.SKILL]:
            LOG.debug(f"Defer playing next track to skill")
            self.bus.emit(Message(f'ovos.common_play.{self.now_playing.skill_id}.next'))
            return

        if self.loop_state == LoopState.REPEAT_TRACK:
            uri = self.now_playing.uri if self.now_playing else None
            uri = uri or finished_uri or \
                  (self._current_entry.uri if self._current_entry else None)
            if uri and uri in self._failed_uris:
                LOG.warning("Repeat-track requested, but the track has failed "
                            "to load — stopping instead of retrying forever")
                self.set_player_state(PlayerState.STOPPED)
                return
            LOG.debug("Repeating single track")
            self.play()
            return

        if self.shuffle:
            LOG.debug("Shuffling")
            # A shuffle advance must respect the same termination bounds as
            # the sequential path (_all_tracks_failed(), end-of-queue) —
            # otherwise an all-failing queue with shuffle+REPEAT hot-loops
            # forever, since play_shuffle() would always pick something and
            # self.play() would be called unconditionally.
            queue = self._merged_queue()
            if self._all_tracks_failed(queue):
                LOG.warning("Shuffle requested, but every track in the "
                            "queue failed to load — stopping instead of "
                            "shuffling forever")
                self.set_player_state(PlayerState.STOPPED)
                return
            if self.play_shuffle():
                # play_shuffle only selects the track - actually start it
                self.play()
            else:
                # no unfailed candidate remains to shuffle to (e.g. a
                # single-track queue with repeat off) - this is the
                # shuffle-path equivalent of the sequential "no more
                # tracks" branch below, so mirror its behavior exactly
                LOG.info("Requested next (shuffle), but there are no more "
                         "tracks in the queue")
                self.set_player_state(PlayerState.STOPPED)
                try:
                    self.media.speak_dialog("queue.finished")
                except Exception as e:
                    LOG.exception(f"Failed to speak queue.finished dialog: {e}")
            return

        queue = self._merged_queue()
        selection = self._queue.select_next(
            queue, uri=self._locator_uri(finished_uri),
            position=self._locator_position(),
            repeat=self.loop_state == LoopState.REPEAT)

        if isinstance(selection, AllFailed):
            # never restart a queue in which every track has already failed
            # to load since the last successful one — that is an unbounded hot
            # loop, not a repeat.
            LOG.warning("End of queue with repeat == True, but every track "
                        "failed to load — stopping instead of looping")
            self.set_player_state(PlayerState.STOPPED)
            return
        elif not isinstance(selection, QueueEnd):
            self.set_now_playing(selection)
        else:
            LOG.info("Requested next, but there are no more tracks in the queue")
            # end of queue with repeat off previously left the player
            # state untouched (still PLAYING from the just-ended track) —
            # nothing ever told the GUI/MPRIS/bus that playback stopped.
            self.set_player_state(PlayerState.STOPPED)
            # This is the ONLY place a natural end-of-queue is detected:
            # every track played through in order and none remain. Speak
            # here, not in handle_playback_ended — that call site fires on
            # every autoplay-off track end and on MPRIS-external track
            # ends too, neither of which is really "the queue finished".
            # This always speaks into the default session. play_next() is
            # reached from an END_OF_MEDIA bus event (or the invalid-stream
            # retry timer), neither of which carries the session of whatever
            # 'ovos.common_play.play' request originally started this
            # queue — so a satellite-triggered playback's queue.finished
            # announces on the default/local session instead of the
            # satellite's. Fixing this needs the player to stash the
            # triggering message's session at play time (handle_play_request)
            # and thread it through to speak_dialog here and at the
            # track.failed site below; see the issue tracker.
            try:
                self.media.speak_dialog("queue.finished")
            except Exception as e:
                LOG.exception(f"Failed to speak queue.finished dialog: {e}")
            return
        self.play()

    def play_prev(self):
        """
        Play the previous track in the merged queue.
        If there is no previous track, do nothing.
        """
        if self.playback_type in [PlaybackType.MPRIS]:
            if self.mpris and self.mpris.manage_players:
                self.mpris.play_prev()
            else:
                LOG.warning("MPRIS external player control is disabled; install ovos-media-plugin-mpris to enable it")
            return
        elif self.playback_type in [PlaybackType.SKILL]:
            self.bus.emit(Message(
                f'ovos.common_play.{self.now_playing.skill_id}.prev'))
            return

        if self.shuffle:
            self.play_shuffle()
            # play_shuffle only selects the track - actually start it
            self.play()
            return

        selection = self._queue.select_prev(
            self._merged_queue(), uri=self._locator_uri(),
            position=self._locator_position())

        if isinstance(selection, QueueEnd):
            LOG.debug("Requested previous, but already at the first track")
        else:
            self.set_now_playing(selection)
            self.play()

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
        if self.playback_type in [PlaybackType.MPRIS] and self.mpris and self.mpris.manage_players:
            self.mpris.pause()
        self.set_player_state(PlayerState.PAUSED)
        self._paused_on_duck = False

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

        if self.playback_type in [PlaybackType.MPRIS] and self.mpris and self.mpris.manage_players:
            self.mpris.resume()

        self.set_player_state(PlayerState.PLAYING)

    def seek(self, position: int):
        """
        Request playback to go to a specific position in the current media.
        Only AUDIO, UNDEFINED and VIDEO playback support seeking. SKILL
        playback would require a new bus message to ask the skill to seek,
        and MPRIS players have no seek passthrough - those types (and any
        other unhandled one) are logged and dropped rather than silently
        doing nothing.
        @param position: milliseconds position to seek to
        """
        if self.playback_type in [PlaybackType.AUDIO,
                                  PlaybackType.UNDEFINED]:
            # audio_service.set_track_position expects milliseconds,
            # matching the milliseconds contract documented on this
            # method and on MediaBackend.set_track_position
            self.audio_service.set_track_position(position)
        elif self.playback_type == PlaybackType.VIDEO:
            self.video_service.set_track_position(position)
        else:
            LOG.warning(f"seek() is not supported for playback_type "
                        f"{self.playback_type}, ignoring")

    def stop(self):
        """
        Request stopping current playback and searching
        """
        # flag BEFORE asking the backends to stop. OPM backends emit
        # END_OF_MEDIA from ocp_stop(), which is indistinguishable from a track
        # ending naturally; without this flag an explicit stop advanced the
        # queue, contradicting the documented "stop must not advance" semantics.
        self._mark_stop_requested()

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
        if self._paused_on_duck:
            # _paused_on_duck is shared by cork (pause-based) and duck
            # (volume-lowered, player stays PLAYING). A stop mid-duck must
            # still restore volume - the backend's restore_volume() is
            # guarded by its own "volume is low" check, so calling both
            # services unconditionally is a no-op for whichever one wasn't
            # ducking (or for the cork path, where volume was never
            # touched in the first place).
            self.video_service.restore_volume()
            self.audio_service.restore_volume()
        self._paused_on_duck = False

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
        self._current_entry = None
        self._failed_uris.clear()
        self._track_failed_spoken = False
        with self._state_lock:
            if self._invalid_timer is not None:
                self._invalid_timer.cancel()
                self._invalid_timer = None
        self.playlist.clear()
        self.media.clear()
        if self.playback_type != PlaybackType.MPRIS:
            self.set_media_state(MediaState.NO_MEDIA)
        self.shuffle = False
        self.loop_state = LoopState.NONE
        # use the authoritative setter (emits ovos.common_play.player.state,
        # updates MPRIS) instead of a bare attribute assignment that bypassed it.
        self.set_player_state(PlayerState.STOPPED)

    def shutdown(self):
        """
        Shutdown this instance and its spawned objects. Remove events.
        """
        self.stop()
        with self._state_lock:
            if self._invalid_timer is not None:
                self._invalid_timer.cancel()
                self._invalid_timer = None
        if self.mpris:
            self.mpris.shutdown()
        # self.media.shutdown() is the no-op OVOSSkill.shutdown() hook — it
        # does NOT remove what ovos-workshop registered for the catalog (the
        # OCP intents, the keyword/announce plumbing of the skill base class).
        # default_shutdown() is the real OVOSSkill teardown that removes
        # them; without it a "shut down" service kept answering intents.
        # The catalog's own announce/skills.detach topics belong to
        # self.bus_api, torn down below.
        self.media.default_shutdown()
        self.audio_service.shutdown()
        self.video_service.shutdown()
        self.web_service.shutdown()
        # a shut-down player must stop answering status/like/duck/etc
        self.bus_api.shutdown()

    def handle_player_media_update(self, message):
        """
        Handles 'ovos.common_play.media.state' messages with media state updates
        @param message: Message providing new "state" data
        """
        state = decode_media_state(message.data)

        # this is the sole consumer of END_OF_MEDIA on
        # 'ovos.common_play.media.state' (the backend services subscribe too,
        # but act on LOADED_MEDIA only).
        # The compare-and-set below plus the end-of-media capture happen under
        # one lock, so two concurrent END_OF_MEDIA events can only ever advance
        # the queue once. Nothing that can re-enter the player (autoplay, GUI
        # pushes) runs while the lock is held.
        with self._state_lock:
            if state == self.media_state:
                return
            LOG.info(f"MediaState changed: {repr(self.media_state)} -> {repr(state)}")
            self.media_state = state
            ended = state == MediaState.END_OF_MEDIA
            invalid = state == MediaState.INVALID_MEDIA
            playback_type = playback_uri = None
            stop_requested = False
            if ended:
                # capture BEFORE the reset that clears both values
                playback_type = self.now_playing.playback
                playback_uri = self.now_playing.uri
                stop_requested = self._stop_requested
                self.now_playing.on_end_of_media()
            # NOTE: _failed_uris/_track_failed_spoken are reset on evidence
            # of PLAYBACK (NowPlaying.handle_track_state_change's
            # TrackState.PLAYING_* branch), not here on LOADED_MEDIA/
            # BUFFERED_MEDIA — a track that loads fine but fails to play
            # emits LOADED_MEDIA then INVALID_MEDIA (see base.py's
            # handle_media_state_change), and resetting on LOADED_MEDIA
            # cleared both guards on every single failing track.

        if ended:
            self.handle_playback_ended(message, playback_type=playback_type,
                                       playback_uri=playback_uri,
                                       stop_requested=stop_requested)
        elif invalid:
            self.handle_invalid_media(message)
            if self.ocp_config.get("autoplay", True):
                # go through the delayed on_invalid_stream() path rather
                # than calling play_next() inline — an inline call recursed
                # straight back into play() and, with a permanently failing
                # backend, spun without bound.
                self.on_invalid_stream()

    def handle_invalid_media(self, message=None):
        # rate-limited to once per queue (see _track_failed_spoken), not once
        # per skipped track, so a queue of several broken tracks in a row
        # does not talk over itself
        if not self._track_failed_spoken:
            self._track_failed_spoken = True
            # Same default-session gap as queue.finished above — see that
            # comment. INVALID_MEDIA carries no session either.
            try:
                self.media.speak_dialog("track.failed")
            except Exception as e:
                LOG.exception(f"Failed to speak track.failed dialog: {e}")

    def handle_playback_ended(self, message, playback_type: PlaybackType = None,
                              playback_uri: str = None,
                              stop_requested: bool = None):
        """Decide whether the end of a track should advance the queue.

        @param playback_type: the PlaybackType captured by
            handle_player_media_update BEFORE the end-of-media reset wiped it.
            Defaults to the live value for direct callers.
        @param playback_uri: likewise for the finished track's uri.
        @param stop_requested: whether this end-of-media was caused by an
            explicit stop request rather than a track finishing.
        """
        if playback_type is None:
            playback_type = self.playback_type
        if stop_requested is None:
            stop_requested = self._stop_requested

        if stop_requested:
            # an explicit stop must never advance the queue. OPM backends
            # emit END_OF_MEDIA from ocp_stop(), so a stop reaches here
            # regardless of which BaseMediaService instance it was called on.
            LOG.debug("Playback ended by an explicit stop request; not advancing")
            return

        if len(self.playlist) and self.ocp_config.get("autoplay", True) and \
                playback_type not in [PlaybackType.MPRIS, PlaybackType.UNDEFINED]:
            # PlaybackType.UNDEFINED -> no media loaded, eg stop called explicitly
            # PlaybackType.MPRIS -> can't load media in MPRIS players
            LOG.debug(f"Playing next track")
            self.play_next(finished_uri=playback_uri)
            return

        LOG.info("Playback ended")
        # NOTE: no queue.finished speak here. This branch is reached
        # whenever play_next() is NOT invoked automatically — autoplay
        # disabled after any track, or an MPRIS-external player's track
        # ending — neither of which is the queue actually finishing.
        # The real "no more tracks" detection, and the single speak site
        # for it, lives in play_next()'s end-of-queue branch.

    # ovos common play bus api requests
    def handle_play_request(self, message):
        LOG.debug("Received OCP playback request")
        repeat = message.data.get("repeat", False)
        if repeat:
            self.loop_state = LoopState.REPEAT

        media = message.data.get("media")
        if not media:
            LOG.warning("handle_play_request: message.data missing 'media' — ignoring")
            return
        playlist = message.data.get("playlist") or [media]
        disambiguation = message.data.get("disambiguation") or playlist

        self.play_media(media, disambiguation, playlist)

    def handle_pause_request(self, message):
        self.pause()

    def handle_stop_request(self, message):
        self.stop()
        self.reset()

    def handle_resume_request(self, message):
        self.resume()

    def handle_pause_toggle_request(self, message):
        if self.state == PlayerState.PAUSED:
            self.handle_resume_request(message)
        else:
            self.handle_pause_request(message)

    def handle_seek_request(self, message):
        seek = decode_seek(message.data)
        if seek is None:
            return
        if "seekValue" in seek:
            # absolute position, from the audio player GUI seekbar
            self.seek(seek["seekValue"])
            return
        # relative offset, from the bus api
        position = self.now_playing.position or 0
        if self.playback_type in [PlaybackType.AUDIO,
                                  PlaybackType.UNDEFINED]:
            position = self.audio_service.get_track_position() or position
        self.seek(position + seek["seconds"] * 1000)

    def handle_next_request(self, message):
        self.play_next()

    def handle_prev_request(self, message):
        self.play_prev()

    def handle_set_shuffle(self, message):
        self.shuffle = True

    def handle_unset_shuffle(self, message):
        self.shuffle = False

    def handle_set_repeat(self, message):
        self.loop_state = LoopState.REPEAT

    def handle_unset_repeat(self, message):
        self.loop_state = LoopState.NONE

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

    def handle_shuffle_toggle_request(self, message):
        self.shuffle = not self.shuffle
        LOG.info(f"Shuffle: {self.shuffle}")
        if self.mpris and self.playback_type == PlaybackType.MPRIS:
            self.mpris.toggle_shuffle()

    def handle_playlist_set_request(self, message):
        # decode BEFORE clearing the existing playlist, so a malformed
        # payload never costs the user the playlist they had.
        tracks = decode_playlist_tracks(message.data)
        if tracks is None:
            return
        entries = validated_entries(tracks)
        self.playlist.clear()
        for track in entries:
            self.playlist.add_entry(track)

    def handle_playlist_queue_request(self, message):
        for track in validated_entries(decode_playlist_tracks(message.data) or []):
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
        Lower volume on 'ovos.common_play.duck'
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
        Restore volume on 'ovos.common_play.unduck'.

        Restores audio service volume whenever ``_paused_on_duck`` is True,
        regardless of player state.  This covers both the duck path (player
        stays PLAYING while volume is lowered) and the cork path (player is
        PAUSED; restore_volume is a no-op on most backends but kept for
        symmetry).

        @param message: Message associated with event
        """
        if self._paused_on_duck:
            if self.playback_type in [PlaybackType.VIDEO]:
                self.video_service.restore_volume()
            elif self.playback_type in [PlaybackType.AUDIO]:
                self.audio_service.restore_volume()
            self._paused_on_duck = False

    def handle_record_end(self, message):
        """
        Handle 'recognizer_loop:record_end'.

        Mirror ovos-audio behaviour: wait up to 8 seconds for a 'speak'
        message.  If none arrives, resume (uncork) immediately.  This prevents
        the media from staying paused when the user's utterance is not
        recognised or triggers no speech response.

        @param message: Message associated with event
        """
        if not self._paused_on_duck:
            return
        speak_detected = self.bus.wait_for_message('speak', timeout=8.0)
        if not speak_detected:
            self.handle_uncork_request(message)

    def handle_utterance_handled(self, message):
        """
        Handle 'ovos.utterance.handled'.

        Restore volume (duck path) or resume (cork path) if the player was
        ducked or corked and speech has now finished.  Mirrors the ovos-audio
        ``_restore_volume_on_handled`` behaviour.

        Covers both cases:
        - Duck (PLAYING): ``_paused_on_duck`` is True, player is PLAYING →
          ``handle_unduck_request`` restores volume.
        - Cork (PAUSED): ``_paused_on_duck`` is True, player is PAUSED →
          ``handle_uncork_request`` resumes playback and restores state.

        @param message: Message associated with event
        """
        if self._paused_on_duck and self.state == PlayerState.PAUSED:
            # The intent has been handled; resume playback that was paused
            # for the cork path, since 'recognizer_loop:record_end' already
            # no-op'd while the 'speak' was in flight.
            self.handle_uncork_request(message)
        elif self._paused_on_duck:
            # The intent has been handled; restore volume for the duck path.
            self.handle_unduck_request(message)

    def handle_mycroft_stop(self, message):
        """
        Handle global 'mycroft.stop' — stop any active playback and emit
        a 'mycroft.stop.handled' acknowledgement.

        @param message: Message associated with event
        """
        if self.state != PlayerState.STOPPED:
            self.stop()
            self.reset()
            self.bus.emit(message.forward("mycroft.stop.handled",
                                          {"by": "ovos-media"}))

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
        miliseconds = decode_track_position(message.data)
        if miliseconds is not None:
            self.seek(miliseconds)

    def handle_track_info_request(self, message):
        data = self.now_playing.as_dict
        self.bus.emit(message.response(data))

    # internal info
    def handle_list_backends_request(self, message):
        data = self.audio_service.available_backends()
        self.bus.emit(message.response(data))

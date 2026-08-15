import dataclasses
import random
import threading
import time
from os.path import join, dirname
from threading import RLock
from typing import List, Optional, Union
from collections.abc import Iterable

from json_database import JsonStorageXDG

from ovos_bus_client import MessageBusClient
from ovos_config import Configuration
from ovos_config.meta import get_xdg_base
from ovos_gui_api_client import GUIInterface
from ovos_media.media_backends import AudioService, VideoService, WebService
from ovos_media.mpris import OcpMprisExporter
from ovos_media.utils import require_default_session, is_real_number, is_injection_char
from ovos_plugin_manager.ocp import load_stream_extractors
from ovos_plugin_manager.templates.media import MediaBackend
from ovos_utils.gui import is_gui_connected, is_gui_running
from ovos_utils.log import LOG
from ovos_bus_client.message import Message
from ovos_bus_client.session import SessionManager
from ovos_utils.ocp import MediaType, Playlist
from ovos_utils.ocp import OCP_ID, PlayerState, LoopState, PlaybackType, PlaybackMode, TrackState, MediaState, \
    MediaEntry, PluginStream
from ovos_workshop.decorators.ocp import ocp_search
from ovos_workshop.skills.common_play import OVOSCommonPlaybackSkill


class OCPMediaCatalog(OVOSCommonPlaybackSkill):
    def __init__(self, *args, validate_source: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        # mirrors NowPlaying.handle_external_play / require_default_session:
        # gates playback-affecting intent handlers (shuffle on/off) to the
        # local/"default" session, unless the owning service was configured
        # with media.validate_source: false (satellite acting on everything)
        self.validate_source = validate_source
        self.skill_icon = f"{dirname(__file__)}/qt5/images/liked.svg"

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
        self.add_event("ovos.common_play.skills.detach", self.handle_ocp_skill_detach)
        self.add_event("ovos.common_play.announce", self.handle_skill_announce)

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
        if not samples:
            return
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
    # player's state (it is not decorated with @require_default_session()).
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
        """Mirror ovos_media.utils.require_default_session in full, including
        the validate_source short-circuit (utils.py ~50-52): only the
        local/"default" session may trigger playback-affecting actions here,
        UNLESS this catalog's owning service was configured with
        media.validate_source: false (satellite acting on every session).
        OCPMediaPlayer.handle_set_shuffle/handle_unset_shuffle are gated by
        that same decorator/config value, so on a non-default (e.g. HiveMind
        satellite) session with validate_source left True, the emitted
        shuffle.set/unset message is silently dropped by the player - this
        must not be reported back as a success. When validate_source is
        False the player WILL act on it, so this front-end must agree."""
        if not self.validate_source:
            return True
        return SessionManager.get(message).session_id == "default"

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

    @staticmethod
    def _flatten_media_types(value) -> list:
        # a skill can announce media_types as a set/tuple/dict_keys/generator,
        # or nest any of those inside a list - wrapping such a value as
        # [value] would make membership checks like "MediaType.ADULT in
        # media_types" always False regardless of contents, so flatten any
        # non-scalar iterable recursively into one flat list of members
        if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
            return [value]
        flat = []
        for item in value:
            flat.extend(OCPMediaCatalog._flatten_media_types(item))
        return flat

    def handle_skill_announce(self, message):
        skill_id = message.data.get("skill_id")
        skill_name = message.data.get("skill_name") or skill_id
        img = message.data.get("image") or message.data.get("thumbnail")
        has_featured = bool(message.data.get("featured_tracks"))
        media_types = message.data.get("media_types") or \
                      message.data.get("media_type") or \
                      [MediaType.GENERIC]
        media_types = self._flatten_media_types(media_types)

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


class NowPlaying(MediaEntry):
    """ Live Tracking of currently playing media via bus events """

    def __init__(self, bus, player: "Optional[OCPMediaPlayer]" = None, *args, **kwargs):
        self.bus = bus
        self._player: "Optional[OCPMediaPlayer]" = player
        self.stream_xtract = load_stream_extractors()
        self.position = 0
        super().__init__(*args, **kwargs)
        self.original_uri = self.uri
        self.bus.on("ovos.common_play.track.state", self.handle_track_state_change)
        # NOTE: NowPlaying deliberately does NOT subscribe to
        # 'ovos.common_play.media.state'. OCPMediaPlayer.handle_player_media_update
        # is the SINGLE subscriber to that topic and calls
        # NowPlaying.on_end_of_media() as a plain method call, in order, after it
        # has captured the pre-reset playback type / uri. Two independent
        # subscribers had no ordering guarantee (pyee's ExecutorEventEmitter
        # submits every handler to a thread pool independently), which raced the
        # end-of-track reset against the autoplay decision that reads it.
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

    def shutdown(self):
        """
        Remove NowPlaying events from the MessageBusClient
        """
        self.bus.remove("ovos.common_play.track.state", self.handle_track_state_change)
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
        # validate the extractor-returned uri BEFORE mutating any state -
        # a non-string uri (int/list/dict) or a whitespace-only string must
        # never poison self.uri/title, it must refuse the same way a
        # missing uri does. An empty string is left alone: it is falsy, so
        # update(newonly=True) below does not overwrite the existing uri
        # with it anyway.
        if meta:
            extracted_uri = meta.get("uri")
            if extracted_uri is not None and not isinstance(extracted_uri, str):
                raise ValueError(f"invalid stream: {extracted_uri!r}")
            if isinstance(extracted_uri, str) and extracted_uri and not extracted_uri.strip():
                raise ValueError(f"invalid stream: {extracted_uri!r}")
            if isinstance(extracted_uri, str) and any(
                    is_injection_char(c) for c in extracted_uri):
                # a uri that otherwise looks fine (eg. a valid http prefix)
                # may still smuggle control characters/newlines - refuse
                # before mutating state, same as the non-string/whitespace
                # checks above (log/header-injection surface downstream)
                raise ValueError(f"invalid stream: {extracted_uri!r}")
            LOG.info(f"OCP plugins metadata: {meta}")
            self.update(meta, newonly=True)
            self.original_uri = uri

        # validate extracted uri
        if not any((self.uri.startswith(s) for s in ["http", "file", "/"])):
            raise ValueError(f"invalid stream: {self.uri!r}")
        # a uri passing the prefix check above may still smuggle control
        # characters (eg. embedded newlines for header/log injection) -
        # refuse those too
        if any(is_injection_char(c) for c in self.uri):
            raise ValueError(f"invalid stream: {self.uri!r}")

    # bus api
    def handle_external_play(self, message):
        """
        Handle 'ovos.common_play.play' Messages. Update the metadata with new
        data received unconditionally, otherwise previous song keys might
        bleed into the new track
        @param message: Message associated with request
        """
        # NowPlaying is part of the single local player; a play command from a
        # non-default session (e.g. a HiveMind satellite, routed by the
        # server-side OCP pipeline) must not bleed its metadata into the local
        # now_playing. Mirror require_default_session() using the owning
        # player's validate_source flag.
        validate = self._player.validate_source if self._player else True
        if validate and SessionManager.get(message).session_id != "default":
            LOG.debug(f"ignoring '{message.msg_type}' now_playing update, "
                      f"not from the default/local session")
            return
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

        if state in (TrackState.PLAYING_AUDIO, TrackState.PLAYING_VIDEO,
                     TrackState.PLAYING_WEBVIEW, TrackState.PLAYING_SKILL,
                     TrackState.PLAYING_AUDIOSERVICE, TrackState.PLAYING_MPRIS):
            # backend confirmed playback started — mark player as PLAYING
            if hasattr(self, '_player') and self._player is not None:
                self._player.set_player_state(PlayerState.PLAYING)
                # W4-followup: reset the per-queue failure guards on evidence
                # of PLAYBACK, not on LOADED_MEDIA. base.py's
                # handle_media_state_change emits LOADED_MEDIA and THEN, for
                # a track that loads fine but raises out of current.play(),
                # INVALID_MEDIA — with the guard reset on LOADED_MEDIA that
                # sequence reset both _failed_uris and _track_failed_spoken
                # on every single failing track (rate limit degraded to
                # per-track chatter, and a REPEAT queue where every track
                # loads-ok-but-fails-to-play never accumulated enough of
                # _failed_uris to trip _all_tracks_failed(), looping without
                # bound). This TrackState.PLAYING_* branch only fires after
                # current.play() returns without raising, ie. real evidence
                # of playback — see base.py's handle_media_state_change.
                self._player._failed_uris.clear()
                self._player._track_failed_spoken = False
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
        OCPMediaPlayer.handle_player_media_update (the single subscriber to
        'ovos.common_play.media.state') AFTER it has captured the pre-reset
        playback type and uri. Playback ended, so allow the next track to
        change metadata again.
        """
        self.reset()

    def handle_media_state_change(self, message):
        """
        Handle 'ovos.common_play.media.state' Messages. If ended, reset.

        NOT registered on the bus (see __init__): kept as a callable entry
        point for out-of-tree callers that drive NowPlaying directly.
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
            self.on_end_of_media()

    def handle_sync_seekbar(self, message):
        """
        Handle 'ovos.common_play.playback_time' Messages sent by audio backend
        @param message: Message with 'length' and 'position' data
        """
        # validate BOTH fields before applying either - a valid 'length'
        # must not commit before an invalid 'position' is discovered,
        # which would otherwise leave partial state from a single message
        updates = {}
        for field in ("length", "position"):
            value = message.data.get(field)
            # real numbers only; bool is a subclass of int but is never a
            # legitimate ms value, a str would otherwise silently coerce
            # (eg. int("5000" * 1000) overflowing the MPRIS int64 wire
            # type) instead of being rejected outright, and NaN/inf/-inf
            # are valid floats that would otherwise raise out of int()
            # below (ValueError/OverflowError) instead of being ignored
            if not is_real_number(value) or value < 0:
                LOG.debug(f"ignoring invalid '{field}' in playback_time "
                          f"message: {value!r}")
                continue
            updates[field] = int(value)
        for field, value in updates.items():
            setattr(self, field, value)
        if self._player is not None:
            self._player._update_gui()

    def handle_sync_trackinfo(self, message):
        """
        Handle 'mycroft.audio.service.track_info_reply' Messages with current
        media defined in message.data
        @param message: Message with dict MediaEntry data
        """
        self.update(message.data)


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
        # session (see ovos_media.utils.require_default_session). A satellite
        # whose sessions are not NAT'd to "default" by hivemind-core should set
        # this False so its embedded ovos-media acts on all sessions.
        self.validate_source = validate_source

        self.state: PlayerState = PlayerState.STOPPED
        self.loop_state: LoopState = LoopState.NONE
        self.media_state: MediaState = MediaState.NO_MEDIA
        self.playlist: Playlist = Playlist(title="Search Results")
        self.shuffle: bool = False
        self.track_history: dict = {}  # Dict of track URI to play count
        self.media: OCPMediaCatalog = OCPMediaCatalog(bus=bus, skill_id=OCP_ID + ".favorites",
                                                       validate_source=self.validate_source)
        self._init_runtime_state()

        self.now_playing: NowPlaying = NowPlaying(bus, player=self)
        # The per-namespace 'ovos.{ns}.service.stop' bus handler lives on
        # BaseMediaService; this callback lets it flag the stop on the player
        # BEFORE the backend's ocp_stop() emits END_OF_MEDIA, in the same
        # thread. A second bus subscription would have had no such ordering
        # guarantee against the END_OF_MEDIA it causes.
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

        self.gui = GUIInterface("ovos.common_play", bus=bus)
        # tracks every (event, handler) pair registered via self._register()
        # below so unregister_bus_handlers() can remove exactly what was
        # added, without hand-maintaining a second list that can drift out
        # of sync with register_bus_handlers().
        self._bus_events: List[tuple] = []
        self.register_bus_handlers()

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
        # The exact MediaEntry object currently selected. Located by
        # identity in _queue_index(), which makes duplicate uris in a playlist
        # (eg. [a, b, a]) advance correctly instead of ping-ponging, and
        # survives the END_OF_MEDIA reset that clears now_playing.uri.
        self._current_entry: Optional[MediaEntry] = None
        # Uris that failed to load since the last successful load. Used to
        # stop LoopState.REPEAT from restarting a queue in which every track is
        # broken (unbounded hot loop).
        self._failed_uris: set = set()
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

    def _register(self, event: str, handler) -> None:
        """Register a bus handler and remember it for unregister_bus_handlers().

        Every handler added by register_bus_handlers() must go through this
        method — it is the single source of truth for what needs to be torn
        down again, so add/remove can never drift out of sync.
        """
        self.bus.on(event, handler)
        self._bus_events.append((event, handler))

    def unregister_bus_handlers(self) -> None:
        """Remove every bus handler registered via self._register()."""
        for event, handler in self._bus_events:
            self.bus.remove(event, handler)
        self._bus_events.clear()

    def register_bus_handlers(self) -> None:
        """Register all OCP bus event handlers."""
        # ovos common play bus api
        # NOTE: OCPMediaPlayer does NOT subscribe to its own
        # 'ovos.common_play.player.state' event — set_player_state() is the
        # single authoritative writer of self.state; external subscribers
        # (MPRIS, GUI clients) may still listen on the bus.
        self._register('ovos.common_play.media.state', self.handle_player_media_update)
        self._register('ovos.common_play.play', self.handle_play_request)
        self._register('ovos.common_play.pause', self.handle_pause_request)
        self._register('ovos.common_play.play_pause', self.handle_pause_toggle_request)
        self._register('ovos.common_play.resume', self.handle_resume_request)
        self._register('ovos.common_play.stop', self.handle_stop_request)
        self._register('ovos.common_play.next', self.handle_next_request)
        self._register('ovos.common_play.previous', self.handle_prev_request)
        self._register('ovos.common_play.seek', self.handle_seek_request)
        self._register('ovos.common_play.get_track_length', self.handle_track_length_request)
        self._register('ovos.common_play.set_track_position', self.handle_set_track_position_request)
        self._register('ovos.common_play.get_track_position', self.handle_track_position_request)
        self._register('ovos.common_play.track_info', self.handle_track_info_request)
        self._register('ovos.common_play.list_backends', self.handle_list_backends_request)
        self._register('ovos.common_play.playlist.set', self.handle_playlist_set_request)
        self._register('ovos.common_play.playlist.clear', self.handle_playlist_clear_request)
        self._register('ovos.common_play.playlist.queue', self.handle_playlist_queue_request)
        self._register('ovos.common_play.duck', self.handle_duck_request)
        self._register('ovos.common_play.unduck', self.handle_unduck_request)
        self._register('ovos.common_play.cork', self.handle_cork_request)
        self._register('ovos.common_play.uncork', self.handle_uncork_request)
        # legacy recognizer_loop ducking — same semantics as the ocp equivalents.
        #
        # NOTE: these are bound through distinct per-topic wrapper functions
        # rather than the bare self.handle_*_request methods, on purpose.
        # 'recognizer_loop:audio_output_start'/'_end'/'record_begin' are
        # migrated legacy topics (ovos_spec_tools.messages.MIGRATION_MAP), so
        # ovos-bus-client's MessageBusClient.on() (client.py) wraps them in a
        # per-HANDLER dedup guard and tracks that wrapper in
        # self._dedup_registrations[func]. Because a bound method compares
        # equal (and hashes the same) across separate attribute accesses,
        # self.handle_duck_request used here is "the same" func as the one
        # passed to _register('ovos.common_play.duck', ...) above. remove()
        # branches on `if target in self._dedup_registrations`, and once ANY
        # topic put that func in the dedup table, remove() takes the dedup
        # path for EVERY topic registered under it — including the plain,
        # non-migrated 'ovos.common_play.duck' registration, whose entry
        # never lived in that table. The dedup path then filters
        # self._dedup_registrations[target] for the given event_name, finds
        # nothing (the plain topic was never recorded there), and silently
        # removes zero listeners instead of falling through to
        # _remove_normal() — so a shut-down OCPMediaPlayer keeps answering
        # 'ovos.common_play.duck'/'unduck'/'cork' forever. Binding the
        # bridged topic to its own wrapper object keeps it out of the plain
        # topic's identity entirely, so each remove() call resolves cleanly.
        self._register('recognizer_loop:audio_output_start', lambda message: self.handle_duck_request(message))
        self._register('recognizer_loop:audio_output_end', lambda message: self.handle_unduck_request(message))
        self._register('recognizer_loop:record_begin', lambda message: self.handle_cork_request(message))
        self._register('recognizer_loop:record_end', self.handle_record_end)
        self._register('ovos.utterance.handled', self.handle_utterance_handled)
        # global stop
        self._register('mycroft.stop', self.handle_mycroft_stop)
        self._register('ovos.common_play.shuffle.toggle', self.handle_shuffle_toggle_request)
        self._register('ovos.common_play.shuffle.set', self.handle_set_shuffle)
        self._register('ovos.common_play.shuffle.unset', self.handle_unset_shuffle)
        self._register('ovos.common_play.repeat.toggle', self.handle_repeat_toggle_request)
        self._register('ovos.common_play.repeat.set', self.handle_set_repeat)
        self._register('ovos.common_play.repeat.unset', self.handle_unset_repeat)
        self._register('ovos.common_play.SEI.get', self.handle_get_SEIs)
        self._register('ovos.common_play.search.start', self.handle_search_start)
        self._register("ovos.common_play.like", self.handle_like)
        self._register("ovos.common_play.unlike", self.handle_unlike)
        self._register("ovos.common_play.status", self.handle_status)
        # external MPRIS player → reflect as OCP now_playing (no local backend)
        self._register("ovos.common_play.mpris.now_playing", self.handle_mpris_now_playing)
        self.handle_get_SEIs(Message("ovos.common_play.SEI.get"))  # report to ovos-core
        self.handle_status(Message("ovos.common_play.status"))  # report to ovos-core

    def _update_gui(self) -> None:
        """Push current OCP state to GUI adapters via show_media_player."""
        state_map = {
            PlayerState.PLAYING: "playing",
            PlayerState.PAUSED: "paused",
            PlayerState.STOPPED: "stopped",
        }
        np = self.now_playing
        now_playing = None
        if np and np.uri:
            # MediaEntry.as_dict has no 'position' field (it is a
            # plain attribute, not a dataclass field) and reports 'length'
            # rather than 'duration' — the GUI seekbar contract needs both
            # 'position' and 'duration' (milliseconds), so merge them in.
            now_playing = {**np.as_dict, "position": np.position, "duration": np.length}
        self.gui.show_media_player(
            now_playing=now_playing,
            playlist=[e.as_dict for e in self.playlist.entries] if self.playlist else [],
            search_results=[e.as_dict for e in self.search_results],
            state=state_map.get(self.state, "stopped"),
        )

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

    @require_default_session()
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
        self._update_gui()
        self.bus.emit(message.forward("mycroft.audio.play_sound",
                                      {"uri": "snd/acknowledge.mp3"}))

    @require_default_session()
    def handle_unlike(self, message):
        # sent from GUI or intent
        uri = message.data.get("uri") or self.now_playing.original_uri
        with self._liked_songs_lock:
            if uri in self.media.liked_songs:
                self.media.liked_songs.pop(uri)
                self.media.liked_songs.store()
                LOG.info(f"unliked song: {uri}")

    # This and service.py's MediaService.handle_search_start both
    # registered on 'ovos.common_play.search.start' unconditionally, so a
    # satellite/non-default session's search double-pushed the "loading" GUI
    # state (once from each handler). Gate this one to the default session
    # (mirrors every other playback-affecting handler in this class) and the
    # redundant registration in service.py was deleted outright.
    @require_default_session()
    def handle_search_start(self, message):
        self.gui.show_media_player(
            now_playing=None,
            playlist=[],
            search_results=[],
            state="loading",
        )

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
        """Return the merged, deduplicated playback queue.

        User-playlist entries come first (strict priority).  Search results
        are appended afterwards, skipping any URI already present in the user
        playlist.  Deduplication is O(n) via a URI set — no O(n²) scanning.

        If ``merge_search`` is disabled in config only the user playlist is
        returned.
        """
        user_entries = list(self.playlist.entries)
        if not self.ocp_config.get("merge_search", True):
            return user_entries
        seen: set = {e.uri for e in user_entries}
        extra = [e for e in self.media.search_playlist.entries if e.uri not in seen]
        return user_entries + extra

    def _mark_stop_requested(self):
        """Flag that the current playback is ending because it was stopped.

        Called from OCPMediaPlayer.stop() and, via the ``on_stop`` callback,
        from BaseMediaService.stop() (the 'ovos.{ns}.service.stop' bus handler)
        before the backend's ocp_stop() emits END_OF_MEDIA.

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

    def _queue_index(self, queue: List[MediaEntry], uri: str = None) -> int:
        """Return the index of the currently selected track in *queue*, or -1.

        Resolution order:

        1. **Entry identity** — the exact MediaEntry object last handed to
           ``set_now_playing``. This is the only reliable locator when the same
           uri appears more than once in a queue (``[a, b, a]``), and it
           survives the END_OF_MEDIA reset that clears ``now_playing.uri``.
        2. **Playlist position** — ``self.playlist.position``, accepted only if
           it points at an entry whose uri matches the one we are looking for.
        3. **URI lookup** — first entry with a matching uri.
        """
        cur = self._current_entry
        if cur is not None:
            for i, entry in enumerate(queue):
                if entry is cur:
                    return i

        uri = uri or (self.now_playing.uri if self.now_playing else None) or \
              (cur.uri if cur is not None else None)
        if not uri:
            return -1

        pos = getattr(self.playlist, "position", None)
        if isinstance(pos, int) and 0 <= pos < len(queue) and queue[pos].uri == uri:
            return pos

        for i, entry in enumerate(queue):
            if entry.uri == uri:
                return i
        return -1

    @property
    def can_prev(self) -> bool:
        """
        Return true if there is a previous track in the queue to skip to
        """
        if self.playback_type == PlaybackType.MPRIS:
            return True
        queue = self._merged_queue()
        idx = self._queue_index(queue)
        return idx > 0

    @property
    def can_next(self) -> bool:
        """
        Return true if there is a next track in the queue to skip to
        """
        if self.loop_state != LoopState.NONE or \
                self.shuffle or \
                self.playback_type == PlaybackType.MPRIS:
            return True
        queue = self._merged_queue()
        idx = self._queue_index(queue)
        return idx >= 0 and idx + 1 < len(queue)

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
        Set self.state, update the GUI and MPRIS (if available), and emit an
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
        self._update_gui()
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
        VLC, …), so the GUI / voice queries see what's actually playing, WITHOUT
        invoking any OCP backend (``PlaybackType.MPRIS`` is external).

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

        # update metadata first so the GUI shows the right track for every state
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
        self._update_gui()

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
        self.gui.show_media_player(
            now_playing=None,
            playlist=[e.as_dict for e in self.playlist.entries] if self.playlist else [],
            search_results=[e.as_dict for e in self.search_results],
            state="error",
        )
        LOG.warning(f"Failed to play: {self.now_playing}")
        # remember this uri as broken so LoopState.REPEAT cannot restart a
        # queue whose every track has failed (that was an unbounded hot loop:
        # 1-track repeat playlist + a backend that always reports INVALID_MEDIA).
        uri = self.now_playing.uri if self.now_playing else None
        if uri:
            self._failed_uris.add(uri)
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
        elif not isinstance(track, MediaEntry):
            raise TypeError(f"Expected MediaEntry, got: {track}")

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
            valid_disambiguation = self._validated_entries(disambiguation)
            self.media.search_playlist.replace([t for t in valid_disambiguation
                                                if t not in self.media.search_playlist])
            self.media.search_playlist.sort_by_conf()
        if playlist:
            valid_playlist = self._validated_entries(playlist)
            if valid_playlist:
                self.playlist.replace(valid_playlist)
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

        elif self.playback_type == PlaybackType.VIDEO:
            LOG.debug("Requesting playback: PlaybackType.VIDEO")
            preferred = self._resolve_preferred_service(self.video_service)
            self.video_service.play(self.now_playing.uri, preferred_service=preferred)

        elif self.playback_type == PlaybackType.WEBVIEW:
            LOG.debug("Requesting playback: PlaybackType.WEBVIEW")
            preferred = self._resolve_preferred_service(self.web_service)
            self.web_service.play(self.now_playing.uri, preferred_service=preferred)

        else:
            raise ValueError("invalid playback request")

        if self.mpris:
            self.mpris.update_props({"CanGoNext": self.can_next})
            self.mpris.update_props({"CanGoPrevious": self.can_prev})

        self.set_player_state(PlayerState.PLAYING)
        self._update_gui()

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
        queue = self._merged_queue()
        if not queue:
            current_uri = self.now_playing.uri if self.now_playing else None
            if current_uri is not None and current_uri in self._failed_uris:
                LOG.debug("Shuffle: queue is empty and current track failed")
                return False
            LOG.debug("Shuffle: queue is empty, replaying current track")
            return True
        current_uri = self.now_playing.uri if self.now_playing else None
        candidates = [e for e in queue
                      if e.uri != current_uri and e.uri not in self._failed_uris]
        if not candidates:
            if self.loop_state == LoopState.REPEAT and current_uri and \
                    current_uri not in self._failed_uris:
                # nothing else to shuffle to, but repeat is on and the
                # current track itself hasn't failed - keep it playing
                # instead of stopping (mirrors play_next's sequential
                # end-of-queue-with-repeat restart)
                LOG.debug("Shuffle: no other viable tracks, repeat is on "
                          "— keeping current track")
                return True
            return False
        pick = random.choice(candidates)
        LOG.debug(f"Shuffle pick: {pick.title!r}")
        self.set_now_playing(pick)
        return True

    def _all_tracks_failed(self, queue: List[MediaEntry]) -> bool:
        """True if every track in *queue* has failed to load since the last
        successful load. Used to break the repeat cycle instead of retrying a
        wholly broken queue forever."""
        return bool(queue) and all(e.uri in self._failed_uris for e in queue)

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
        idx = self._queue_index(queue, uri=finished_uri)

        if idx >= 0 and idx + 1 < len(queue):
            next_track = queue[idx + 1]
            LOG.info(f"Next track: {next_track.title!r} (queue index {idx + 1}/{len(queue) - 1})")
            self.set_now_playing(next_track)
        elif self.loop_state == LoopState.REPEAT and queue:
            # never restart a queue in which every track has already failed
            # to load since the last successful one — that is an unbounded hot
            # loop, not a repeat.
            if self._all_tracks_failed(queue):
                LOG.warning("End of queue with repeat == True, but every track "
                            "failed to load — stopping instead of looping")
                self.set_player_state(PlayerState.STOPPED)
                return
            LOG.info("End of queue, repeat == True — restarting from beginning")
            self.set_now_playing(queue[0])
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

        queue = self._merged_queue()
        idx = self._queue_index(queue)

        if idx > 0:
            prev_track = queue[idx - 1]
            LOG.debug(f"Previous track: {prev_track.title!r} (queue index {idx - 1}/{len(queue) - 1})")
            self.set_now_playing(prev_track)
            self.play()
        else:
            LOG.debug("Requested previous, but already at the first track")

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
        self._update_gui()

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
        self._update_gui()

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
        self._update_gui()

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
        # stop -> home (reset) previously left a zombie now_playing entry
        # on the GUI — now_playing.reset() above already clears uri/original_uri,
        # but set_player_state() above is a no-op (incl. no _update_gui) when
        # the player was already STOPPED, so push explicitly here too.
        self._update_gui()

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
        self.now_playing.shutdown()
        # self.media.shutdown() is the no-op OVOSSkill.shutdown() hook —
        # it does NOT remove the bus handlers add_event() registered
        # (ovos.common_play.skills.detach, .announce, the OCP intents, ...).
        # default_shutdown() is the real OVOSSkill teardown that removes
        # them; without it a "shut down" service kept answering intents.
        self.media.default_shutdown()
        self.audio_service.shutdown()
        self.video_service.shutdown()
        self.web_service.shutdown()
        # self.gui is a GUIInterface("ovos.common_play", bus=bus) — its own
        # shutdown() removes the 'ovos.common_play.set' listener it
        # registers on construction. Without this call, every OCPMediaPlayer
        # created (eg. in a test loop, or a real service restart) leaked one
        # such bus listener for the lifetime of the process.
        self.gui.shutdown()
        # register_bus_handlers() above bound ~35 handlers directly via
        # self.bus.on() (not add_event(), since OCPMediaPlayer is not an
        # OVOSSkill) — remove exactly what was registered so a shut-down
        # player stops answering ping/status/search/like/etc.
        self.unregister_bus_handlers()

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

        # this is the SOLE subscriber to 'ovos.common_play.media.state'.
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
            # handle_playback_ended manages its own _update_gui() call
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
            # on_invalid_stream defers; nothing else pushed the GUI
            else:
                self._update_gui()
        else:
            self._update_gui()

    def handle_invalid_media(self, message=None):
        self.gui.show_media_player(
            now_playing=None,
            playlist=[e.as_dict for e in self.playlist.entries] if self.playlist else [],
            search_results=[e.as_dict for e in self.search_results],
            state="error",
        )
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
            # emit END_OF_MEDIA from ocp_stop(), so both the API path
            # (OCPMediaPlayer.stop) and the external path
            # ('ovos.{ns}.service.stop') land here.
            LOG.debug("Playback ended by an explicit stop request; not advancing")
            self._update_gui()
            return

        if len(self.playlist) and self.ocp_config.get("autoplay", True) and \
                playback_type not in [PlaybackType.MPRIS, PlaybackType.UNDEFINED]:
            # PlaybackType.UNDEFINED -> no media loaded, eg stop called explicitly
            # PlaybackType.MPRIS -> can't load media in MPRIS players
            LOG.debug(f"Playing next track")
            self.play_next(finished_uri=playback_uri)
            return

        LOG.info("Playback ended")
        self._update_gui()
        # NOTE: no queue.finished speak here. This branch is reached
        # whenever play_next() is NOT invoked automatically — autoplay
        # disabled after any track, or an MPRIS-external player's track
        # ending — neither of which is the queue actually finishing.
        # The real "no more tracks" detection, and the single speak site
        # for it, lives in play_next()'s end-of-queue branch.

    # ovos common play bus api requests
    @require_default_session()
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

    @require_default_session()
    def handle_pause_request(self, message):
        self.pause()

    @require_default_session()
    def handle_stop_request(self, message):
        self.stop()
        self.reset()

    @require_default_session()
    def handle_resume_request(self, message):
        self.resume()

    @require_default_session()
    def handle_pause_toggle_request(self, message):
        if self.state == PlayerState.PAUSED:
            self.handle_resume_request(message)
        else:
            self.handle_pause_request(message)

    @require_default_session()
    def handle_seek_request(self, message):
        # from bus api
        seconds = message.data.get("seconds", 0)
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
            LOG.warning(f"Ignoring seek request with non-numeric 'seconds': "
                        f"{seconds!r}")
            return
        miliseconds = seconds * 1000

        # from audio player GUI
        position = message.data.get("seekValue")
        # `seekValue: 0` (seek to the very start) is falsy, so `if not
        # position` misread it as "no seekValue given" and fell through to
        # the relative-seek path instead of seeking to 0.
        if position is None:
            position = self.now_playing.position or 0
            if self.playback_type in [PlaybackType.AUDIO,
                                      PlaybackType.UNDEFINED]:
                position = self.audio_service.get_track_position() or position
            position += miliseconds
        self.seek(position)

    @require_default_session()
    def handle_next_request(self, message):
        self.play_next()

    @require_default_session()
    def handle_prev_request(self, message):
        self.play_prev()

    @require_default_session()
    def handle_set_shuffle(self, message):
        self.shuffle = True
        self._update_gui()

    @require_default_session()
    def handle_unset_shuffle(self, message):
        self.shuffle = False
        self._update_gui()

    @require_default_session()
    def handle_set_repeat(self, message):
        self.loop_state = LoopState.REPEAT
        self._update_gui()

    @require_default_session()
    def handle_unset_repeat(self, message):
        self.loop_state = LoopState.NONE
        self._update_gui()

    # playlist control bus api
    @require_default_session()
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
        self._update_gui()

    @require_default_session()
    def handle_shuffle_toggle_request(self, message):
        self.shuffle = not self.shuffle
        LOG.info(f"Shuffle: {self.shuffle}")
        if self.mpris and self.playback_type == PlaybackType.MPRIS:
            self.mpris.toggle_shuffle()
        self._update_gui()

    @require_default_session()
    def handle_playlist_set_request(self, message):
        # validate (and default to []) BEFORE clearing the existing
        # playlist — the old code cleared first and then KeyError'd on a
        # missing 'tracks' key inside handle_playlist_queue_request, leaving
        # the player with an empty playlist for no reason on a malformed
        # request.
        tracks = message.data.get("tracks") or []
        if not isinstance(tracks, (list, tuple)):
            LOG.warning(f"ignoring playlist.set payload of type "
                        f"{type(tracks).__name__} - keeping current playlist")
            return
        entries = self._validated_entries(tracks)
        self.playlist.clear()
        for track in entries:
            self.playlist.add_entry(track)

    @staticmethod
    def _validated_entries(tracks):
        """Coerce a playlist payload into valid entries, skipping the bad
        ones with a warning instead of aborting mid-mutation. A non-list
        payload (eg. a bare string, which would iterate character-wise)
        yields no entries."""
        if not isinstance(tracks, (list, tuple)):
            LOG.warning(f"ignoring playlist payload of type "
                        f"{type(tracks).__name__}, expected a list of tracks")
            return []
        entries = []
        for track in tracks:
            try:
                if isinstance(track, dict):
                    track = MediaEntry.from_dict(track)
                if not isinstance(track, (MediaEntry, Playlist, PluginStream)):
                    raise ValueError(f"not a valid track: {track!r}")
                # a non-numeric length/position (eg. bus-fed "garbage")
                # must not poison Playlist.length's later sum() over all
                # entries - sanitize to 0 rather than reject the whole entry.
                # Playlist.length is a read-only computed property (sum of
                # its own entries), so only individual tracks are sanitized.
                if isinstance(track, (MediaEntry, PluginStream)):
                    for field in ("length", "position"):
                        value = getattr(track, field, None)
                        if not is_real_number(value):
                            LOG.debug(f"coercing invalid '{field}' on "
                                      f"playlist entry to 0: {value!r}")
                            setattr(track, field, 0)
                elif isinstance(track, Playlist):
                    # a nested Playlist's own tracks are entries too - bad
                    # values inside them would otherwise reach the outer
                    # Playlist.length (a sum over all contained entries)
                    # unsanitized. Recurse arbitrarily deep.
                    OCPMediaPlayer._sanitize_nested_playlist(track, set())
                entries.append(track)
            except Exception as e:
                LOG.warning(f"skipping invalid playlist entry: {e}")
        return entries

    @staticmethod
    def _sanitize_nested_playlist(playlist, visited):
        """Sanitize length/position on every MediaEntry/PluginStream
        reachable inside a (possibly self-referential) tree of nested
        Playlists, mutating the shared objects in place.

        `Playlist.entries` is a computed property that returns a filtered
        copy of the list and drops nested Playlist members entirely, so it
        cannot be used to reach or fix them. Iterating the Playlist itself
        (it subclasses list) reaches every raw member, including nested
        Playlists. Dict members are *not* sanitized by reading them either:
        `Playlist.entries` calls `dict2entry`/`MediaEntry.from_dict` fresh
        on every read, and neither applies any numeric coercion - so a raw
        dict member must be sanitized here, in place, or it stays a live
        landmine for `Playlist.length`'s sum().
        """
        if id(playlist) in visited:
            return
        visited.add(id(playlist))
        for member in list.__iter__(playlist):
            if isinstance(member, (MediaEntry, PluginStream)):
                for field in ("length", "position"):
                    value = getattr(member, field, None)
                    if not is_real_number(value):
                        LOG.debug(f"coercing invalid '{field}' on "
                                  f"playlist entry to 0: {value!r}")
                        setattr(member, field, 0)
            elif isinstance(member, Playlist):
                OCPMediaPlayer._sanitize_nested_playlist(member, visited)
            elif isinstance(member, dict):
                for field in ("length", "position"):
                    if field in member and not is_real_number(member[field]):
                        LOG.debug(f"coercing invalid '{field}' on raw "
                                  f"playlist entry to 0: {member[field]!r}")
                        member[field] = 0
                # dict2entry() only turns a dict into a nested Playlist when
                # it carries a truthy "playlist" key - recurse into that
                # list of raw (also unsanitized) member dicts too.
                if isinstance(member.get("playlist"), list):
                    OCPMediaPlayer._sanitize_raw_playlist_dicts(
                        member["playlist"], visited)

    @staticmethod
    def _sanitize_raw_playlist_dicts(members, visited):
        """Sanitize length/position in place across a raw list of playlist
        member dicts/objects, as found under a dict's "playlist" key
        (see `_sanitize_nested_playlist`)."""
        if id(members) in visited:
            return
        visited.add(id(members))
        for member in members:
            if isinstance(member, (MediaEntry, PluginStream)):
                for field in ("length", "position"):
                    value = getattr(member, field, None)
                    if not is_real_number(value):
                        setattr(member, field, 0)
            elif isinstance(member, Playlist):
                OCPMediaPlayer._sanitize_nested_playlist(member, visited)
            elif isinstance(member, dict):
                for field in ("length", "position"):
                    if field in member and not is_real_number(member[field]):
                        member[field] = 0
                if isinstance(member.get("playlist"), list):
                    OCPMediaPlayer._sanitize_raw_playlist_dicts(
                        member["playlist"], visited)

    @require_default_session()
    def handle_playlist_queue_request(self, message):
        for track in self._validated_entries(message.data.get("tracks") or []):
            self.playlist.add_entry(track)

    @require_default_session()
    def handle_playlist_clear_request(self, message):
        self.playlist.clear()

    # audio ducking - NB: we distinguish ducking vs corking  (lower volume vs pause)
    @require_default_session()
    def handle_cork_request(self, message):
        """
        Pause audio on 'recognizer_loop:record_begin'
        @param message: Message associated with event
        """
        if self.state == PlayerState.PLAYING:
            self.pause()
            self._paused_on_duck = True

    @require_default_session()
    def handle_uncork_request(self, message):
        """
        Resume paused audio on 'recognizer_loop:record_begin'
        @param message: Message associated with event
        """
        if self.state == PlayerState.PAUSED and self._paused_on_duck:
            self.resume()
            self._paused_on_duck = False

    @require_default_session()
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

    @require_default_session()
    def handle_unduck_request(self, message):
        """
        Restore volume on 'recognizer_loop:audio_output_end'.

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

    @require_default_session()
    def handle_set_track_position_request(self, message):
        miliseconds = message.data.get("position")
        if isinstance(miliseconds, (int, float)) and not isinstance(miliseconds, bool):
            self.seek(miliseconds)
        elif miliseconds is not None:
            LOG.warning(f"Ignoring set_track_position request with "
                        f"non-numeric 'position': {miliseconds!r}")

    def handle_track_info_request(self, message):
        data = self.now_playing.as_dict
        self.bus.emit(message.response(data))

    # internal info
    def handle_list_backends_request(self, message):
        data = self.audio_service.available_backends()
        self.bus.emit(message.response(data))

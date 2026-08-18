from typing import List, Optional, Union

from ovos_bus_client import MessageBusClient
from ovos_config import Configuration
from ovos_media.media_backends import AudioService, VideoService, WebService
from ovos_media.mpris import OcpMprisExporter
from ovos_media.bus.api import OCPBusApi
from ovos_media.catalog import LikedSongsStore, MediaCatalog
from ovos_media.bus.schemas import (decode_media_state, decode_playlist_tracks,
                                    decode_seek, decode_track_position,
                                    validated_entries)
from ovos_media.player.queue import (AllFailed, KeepCurrent, PlayQueue,
                                     QueueEnd)
from ovos_media.player.now_playing import NowPlaying
from ovos_media.player.adapters import OPMBackendAdapter, SkillPlayerAdapter
from ovos_media.player.roster import PlayerRoster
from ovos_media.player.dispatcher import Dispatcher, PlayerSnapshot
from ovos_plugin_manager.ocp import load_stream_extractors
from ovos_plugin_manager.templates.media import MediaBackend
from ovos_utils.log import LOG
from ovos_bus_client.message import Message
from ovos_utils.ocp import Playlist
from ovos_utils.ocp import PlayerState, LoopState, PlaybackType, PlaybackMode, TrackState, MediaState, \
    MediaEntry, PluginStream

# The catalog is constructed through this module-level name and nothing
# else: ovoscope's OCPPlayerHarness patches ``ovos_media.player.
# OCPMediaCatalog`` with a MagicMock to build a real player without a real
# catalog, so the name has to stay both importable and the one the player
# calls.
OCPMediaCatalog = MediaCatalog


class OCPMediaPlayer:
    """OCP Virtual Media Player

    for OVOS this is all that exists and represents all loaded and currently playing media

    "now playing" is tracked and managed by this interface
    """

    def __init__(self, bus: MessageBusClient, config: Optional[dict] = None,
                 validate_source: bool = True, likes=None) -> None:
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
        # MediaService injects the store it also gives the voice skill; a
        # player built on its own has no one to share with, so it opens the
        # store itself.
        self.media: MediaCatalog = OCPMediaCatalog(
            bus=bus, likes=likes if likes is not None else LikedSongsStore())
        self._init_runtime_state()
        # the owned queue, also the container the rest of the world reads as
        # "the playlist" (bus status, MPRIS track list)
        self.playlist: PlayQueue = self._queue

        self.now_playing: NowPlaying = NowPlaying(bus, player=self)
        # A stop the player itself asked for needs no callback any more:
        # stop() runs to completion on the dispatcher, so the END_OF_MEDIA
        # the backend's ocp_stop() emits is a later queued command and
        # already sees _stop_requested. on_stop covers the other caller —
        # a skill stopping a backend service directly — whose stop the
        # player would otherwise mistake for a track ending.
        self.audio_service = AudioService(bus, on_stop=self._on_backend_stop)
        self.video_service = VideoService(bus, on_stop=self._on_backend_stop)
        self.web_service = WebService(bus, on_stop=self._on_backend_stop)
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
        self.publish_snapshot()
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
        # Every command that mutates this player runs here, one at a time,
        # in submission order: the bus edge, the MPRIS thread and the
        # delayed invalid-stream retry all submit instead of calling in
        # from their own threads. Two END_OF_MEDIA events are two queued
        # commands, so the second sees what the first did.
        self.dispatcher: Dispatcher = Dispatcher()
        self.dispatcher.post_hook = self.publish_snapshot
        # what queries answer from; replaced wholesale after every command
        self._snapshot: PlayerSnapshot = PlayerSnapshot()
        # True between a stop request and the next play(). An explicit stop
        # must NOT advance the queue, but OPM backends emit END_OF_MEDIA from
        # ocp_stop(), so a stop is indistinguishable from a natural track end at
        # the media.state level without this flag.
        self._stop_requested: bool = False
        # owns the user queue, the identity of the selected entry and the
        # failed-uri bookkeeping, and answers every "which track is next"
        # question; what to DO with the answer stays player policy
        self._queue: PlayQueue = PlayQueue(title="Search Results")
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
        # every concrete player this virtual player drives, behind one
        # interface. The backend services stay exactly what they were; the
        # adapters wrap them, they do not replace them, and they resolve
        # their service through this player so a player assembled piecemeal
        # (services supplied after the fact) gets a working roster too.
        self.roster: PlayerRoster = PlayerRoster([
            OPMBackendAdapter("opm:audio", self, "audio_service"),
            OPMBackendAdapter("opm:video", self, "video_service"),
            OPMBackendAdapter("opm:web", self, "web_service"),
            SkillPlayerAdapter(self),
        ])

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

    def publish_snapshot(self) -> PlayerSnapshot:
        """Republish the query view of this player. Called after every
        dispatched command."""
        self._snapshot = PlayerSnapshot.of(self)
        return self._snapshot

    @property
    def snapshot(self) -> PlayerSnapshot:
        """The view queries answer from.

        A command already running on the dispatcher reads live state — the
        published snapshot still describes the world before the command
        started, and set_player_state() reports status from inside one.
        """
        if self.dispatcher.on_thread():
            return PlayerSnapshot.of(self)
        return self._snapshot

    def handle_status(self, message):
        self.bus.emit(message.response(self.snapshot.as_status_dict))

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
            self.media.notify_dialog("nothing.playing")
            return
        title = message.data.get("title") or self.now_playing.title
        image = message.data.get("image") or message.data.get("thumbnail") or self.now_playing.image
        artist = message.data.get("artist") or self.now_playing.artist
        self.media.likes.like(uri, title=title, artist=artist, image=image)
        self.bus.emit(message.forward("mycroft.audio.play_sound",
                                      {"uri": "snd/acknowledge.mp3"}))

    def handle_unlike(self, message):
        # sent from GUI or intent
        uri = message.data.get("uri") or self.now_playing.original_uri
        self.media.likes.unlike(uri)

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

        Called from OCPMediaPlayer.stop(), before the adapters are asked to
        stop and therefore before the backend's ocp_stop() emits
        END_OF_MEDIA.

        Also supersedes any pending invalid-stream retry. Without this, a
        stop arriving during the post-INVALID_MEDIA retry window let the
        retry run play_next() after the stop had already settled the player
        into STOPPED — spontaneously resuming playback.
        """
        self._stop_requested = True
        self.dispatcher.bump_epoch()

    def _on_backend_stop(self):
        """A backend service was stopped by someone other than this player.

        A stop this player itself commanded reaches here from the
        dispatcher, having already flagged it — submitting a second write
        would land after whatever command came next (a play arriving while
        the stop ran), re-flagging a stop the user has already replaced.
        Only a stop from another thread, a skill calling a backend
        service's stop() directly, still has to be recorded.
        """
        if self.dispatcher.in_command():
            return
        self.dispatcher.submit(self._mark_stop_requested)

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

        Tagged with the current epoch: a later play(), stop() or reset()
        bumps it and this retry is dropped when it comes up, so a burst of
        INVALID_MEDIA events cannot pile up overlapping skips and a stale
        retry can never fire against a track it was not scheduled for.
        """
        self.dispatcher.call_later(self.invalid_stream_delay, self.play_next,
                                   self.dispatcher.epoch)

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
            self.media.notify_dialog("no.playback.backend")

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
        self.media_state = MediaState.LOADING_MEDIA
        # a new play attempt supersedes any earlier stop request
        self._stop_requested = False
        # ...and any pending invalid-stream retry; otherwise a stale retry
        # runs play_next() against this NEW track once its window elapses
        self.dispatcher.bump_epoch()

        # switching playback types must not leave the previously active
        # backend's BaseMediaService.current set — otherwise a later,
        # unrelated global LOADED_MEDIA event revives the stale backend and
        # two backends end up playing at once.
        self.roster.deactivate_others(self.playback_type)

        self.media.likes.increment_play_count(self.now_playing.uri)

        # validate new stream
        if not self.validate_stream():
            LOG.warning("Stream Validation Failed")
            self.on_invalid_stream()
            return

        self.track_history.setdefault(self.now_playing.uri, 0)
        self.track_history[self.now_playing.uri] += 1

        LOG.debug(f"Requesting playback: {self.playback_type}")
        adapter, playback_type = self.roster.select(self.playback_type,
                                                    self.now_playing.uri)
        if playback_type != self.playback_type:
            # demoted to audio because no video/web backend claimed the uri
            self.now_playing.playback = playback_type
        if adapter is None:
            self.bus.emit(Message("ovos.common_play.media.state",
                                  {"state": MediaState.INVALID_MEDIA}))
        else:
            adapter.play(self.now_playing.uri)

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
            self.roster.get("skill").next()
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
                self.media.notify_dialog("queue.finished")
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
            self.media.notify_dialog("queue.finished")
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
            self.roster.get("skill").prev()
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
        if self.playback_type in [PlaybackType.MPRIS]:
            if self.mpris and self.mpris.manage_players:
                self.mpris.pause()
        else:
            for adapter in self.roster.route("pause", self.playback_type):
                adapter.pause()
        self.set_player_state(PlayerState.PAUSED)
        self._paused_on_duck = False

    def resume(self):
        """
        Ask any paused or stopped playback to resume.
        """
        LOG.debug(f"Resuming playback: {self.playback_type}")
        if self.playback_type in [PlaybackType.MPRIS]:
            if self.mpris and self.mpris.manage_players:
                self.mpris.resume()
        else:
            for adapter in self.roster.route("resume", self.playback_type):
                adapter.resume()

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
        # adapters take milliseconds, matching the contract documented on
        # this method and on MediaBackend.set_track_position
        adapters = self.roster.route("seek", self.playback_type)
        if not adapters:
            LOG.warning(f"seek() is not supported for playback_type "
                        f"{self.playback_type}, ignoring")
        for adapter in adapters:
            adapter.seek(position)

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
        if self.playback_type in [PlaybackType.MPRIS]:
            if self.mpris:
                self.mpris.pause()
        else:
            for adapter in self.roster.route("stop", self.playback_type):
                adapter.stop()
        self.set_player_state(PlayerState.STOPPED)
        if self._paused_on_duck:
            # _paused_on_duck is shared by cork (pause-based) and duck
            # (volume-lowered, player stays PLAYING). A stop mid-duck must
            # still restore volume - the backend's restore_volume() is
            # guarded by its own "volume is low" check, so calling both
            # services unconditionally is a no-op for whichever one wasn't
            # ducking (or for the cork path, where volume was never
            # touched in the first place).
            self.roster.get("opm:video").restore_volume()
            self.roster.get("opm:audio").restore_volume()
        self._paused_on_duck = False

    def handle_MPRIS_takeover(self):
        """ Called when a MPRIS external player becomes active"""
        for adapter in self.roster.adapters:
            adapter.stop()
        self.now_playing.original_uri = ""

    def stop_skill(self):
        """
        Emit a Message notifying self.active_skill to stop
        """
        self.roster.get("skill").stop()

    def reset(self):
        """
        Reset this instance to clear any media or settings
        """
        self.now_playing.reset()
        self._current_entry = None
        self._failed_uris.clear()
        self._track_failed_spoken = False
        self.dispatcher.bump_epoch()
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
        if self.mpris:
            self.mpris.shutdown()
        # the catalog's own announce/skills.detach topics belong to
        # self.bus_api, torn down below; this only drops the dialog
        # listeners a voice front-end registered
        self.media.shutdown()
        self.audio_service.shutdown()
        self.video_service.shutdown()
        self.web_service.shutdown()
        # a shut-down player must stop answering status/like/duck/etc
        self.bus_api.shutdown()
        # last: no command may outlive the objects it drives
        self.dispatcher.shutdown()

    def handle_player_media_update(self, message):
        """
        Handles 'ovos.common_play.media.state' messages with media state updates
        @param message: Message providing new "state" data
        """
        state = decode_media_state(message.data)

        # this is the sole consumer of END_OF_MEDIA on
        # 'ovos.common_play.media.state' (the backend services subscribe too,
        # but act on LOADED_MEDIA only). Two END_OF_MEDIA events arrive as two
        # queued commands, so the second one sees the media_state the first
        # one set and returns here — the queue can only advance once.
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
            self.media.notify_dialog("track.failed")

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
        for adapter in self.roster.route("position_offset", self.playback_type):
            position = adapter.position() or position
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
            for adapter in self.roster.route("volume", self.playback_type):
                adapter.lower_volume()
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
            for adapter in self.roster.route("volume", self.playback_type):
                adapter.restore_volume()
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
        # runs at the bus edge, never on the dispatcher: an 8s wait there
        # would stall every other command. Only the resume is dispatched.
        speak_detected = self.bus.wait_for_message('speak', timeout=8.0)
        if not speak_detected:
            self.dispatcher.submit(lambda: self.handle_uncork_request(message))

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
        # answered off the dispatcher. The backend adapters below are the
        # sanctioned off-thread read: only the plugin knows the live
        # length/position, and a queued round-trip would return a value
        # already stale by the time it was emitted.
        l = self.snapshot.track_info.get("length") or self.now_playing.length
        for adapter in self.roster.route("position", self.playback_type):
            l = adapter.length() or l
        data = {"length": l}
        self.bus.emit(message.response(data))

    def handle_track_position_request(self, message):
        # live read, see handle_track_length_request
        pos = self.snapshot.track_info.get("position") or self.now_playing.position
        for adapter in self.roster.route("position", self.playback_type):
            pos = adapter.position() or pos
        data = {"position": pos}
        self.bus.emit(message.response(data))

    def handle_set_track_position_request(self, message):
        miliseconds = decode_track_position(message.data)
        if miliseconds is not None:
            self.seek(miliseconds)

    def handle_track_info_request(self, message):
        self.bus.emit(message.response(dict(self.snapshot.track_info)))

    # internal info
    def handle_list_backends_request(self, message):
        data = self.audio_service.available_backends()
        self.bus.emit(message.response(data))

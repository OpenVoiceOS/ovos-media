import abc
import threading
import time
from threading import Lock
from typing import Callable, Optional

from ovos_plugin_manager.templates.media import MediaBackend, RemoteAudioPlayerBackend, RemoteVideoPlayerBackend, \
    RemoteWebPlayerBackend
from ovos_utils.ocp import MediaState, TrackState

from ovos_bus_client.message import Message
from ovos_config.config import Configuration
from ovos_media.utils import validate_message_context
from ovos_utils.log import LOG
from ovos_utils.process_utils import MonotonicEvent


class BaseMediaService:

    def __init__(self, bus, namespace: str, plugin_loader: Callable,
                 config=None, autoload=True, validate_source=True):
        """
            Args:
                bus: OVOS messagebus
        """
        self.bus = bus
        self.namespace = namespace
        self.plugin_loader = plugin_loader
        self.config = config or Configuration().get("media") or {}
        self.service_lock = Lock()

        self.default = None
        self.services = []
        self.current = None
        self.play_start_time = 0
        self.volume_is_low = False
        self.validate_source = validate_source
        # C6: queued-but-not-yet-loaded tracks from the last handle_play(),
        # and whether that request asked to repeat once exhausted. Consumed
        # incrementally from track_start() as each track finishes.
        self._pending_playlist: list = []
        self._pending_repeat: bool = False
        self._last_full_playlist: list = []

        self._loaded = MonotonicEvent()
        if autoload:
            self.load_services()
        self.bus.on("ovos.common_play.media.state", self.handle_media_state_change)

    def available_backends(self):
        """Return available media backends.

        Returns:
            dict with backend names as keys
        """
        data = {}
        for s in self.services:
            info = {
                'supported_uris': s.supported_uris(),
                'remote': isinstance(s, RemoteAudioPlayerBackend) or
                          isinstance(s, RemoteWebPlayerBackend) or
                          isinstance(s, RemoteVideoPlayerBackend)
            }
            data[s.name] = info
        return data

    def track_start(self, track):
        """Callback method called from the services to indicate start of
        playback of a track or end of playlist.
        """
        if track:
            # Inform about the track about to start.
            LOG.info(f'New {self.namespace} track coming up!')
            self.bus.emit(Message(f'ovos.{self.namespace}.playing_track',
                                  data={'track': track}))
            if self.namespace == "audio":
                # legacy ovos-audio twin — some skills still block on this
                # exact message type waiting for playback to start
                self.bus.emit(Message('mycroft.audio.playing_track',
                                      data={'track': track}))
        elif self._pending_playlist:
            # C6: handle_play() was given more than one track — advance to
            # the next queued uri instead of reporting end-of-playlist after
            # only the first track ever played.
            next_uri = self._pending_playlist.pop(0)
            LOG.debug(f'Advancing to next queued {self.namespace} track')
            self.play(next_uri)
        elif self._pending_repeat and self._last_full_playlist:
            # C6: the exhausted playlist was requested with repeat=True —
            # mirrors ovos-audio's PlaybackService repeat semantics.
            LOG.debug(f'Repeating {self.namespace} playlist')
            self._pending_playlist = list(self._last_full_playlist[1:])
            self.play(self._last_full_playlist[0])
        else:
            # If no track is about to start last track of the queue has been
            # played.
            LOG.debug('End of playlist!')
            self.bus.emit(Message(f'ovos.{self.namespace}.queue_end'))
            if self.namespace == "audio":
                # legacy ovos-audio twin — some skills block forever waiting
                # on this exact message type at the end of playback
                self.bus.emit(Message('mycroft.audio.queue_end'))

    def load_services(self):
        """Method for loading services.

        Sets up the global service, default and registers the event handlers
        for the subsystem.
        """
        local = []
        remote = []

        plugs = self.plugin_loader()
        players_cfg = self.config.get(f"{self.namespace}_players", {})
        if not isinstance(players_cfg, dict):
            LOG.error(f"Expected a dict for '{self.namespace}_players' "
                      f"config, got {type(players_cfg).__name__}: "
                      f"{players_cfg!r} - ignoring")
            players_cfg = {}
        for player_name, plug_cfg in players_cfg.items():
            if not isinstance(plug_cfg, dict):
                LOG.error(f"Expected a dict for '{self.namespace}_players' "
                          f"entry '{player_name}', got "
                          f"{type(plug_cfg).__name__}: {plug_cfg!r} - "
                          f"ignoring")
                continue
            if "module" not in plug_cfg:
                LOG.error(f"'{self.namespace}_players' entry '{player_name}' "
                          f"is missing the required 'module' key - ignoring")
                continue
            plug_name = plug_cfg["module"]
            if plug_name not in plugs:
                LOG.error(f"{plug_name} configured but not installed")
                continue
            if not plug_cfg.get("active", True):
                LOG.info(f"{plug_name} is disabled in configuration")
                continue
            try:
                service = plugs[plug_name](plug_cfg, self.bus)
                service.aliases = plug_cfg.get("aliases", []) or [plug_name]
                service.name = player_name
                if isinstance(service, RemoteAudioPlayerBackend):
                    remote.append(service)
                else:
                    local.append(service)
                LOG.info(f"Loaded {self.__class__.__name__} plugin: {plug_name}")
            except:
                LOG.exception(f"Failed to load {plug_name}")

        # Sort services so local services are checked first
        self.services = local + remote

        if not self.services:
            LOG.error(
                f"No {self.namespace} backends loaded — all {self.namespace} playback will fail. "
                f"Install at least one backend plugin (e.g. ovos-vlc-plugin, ovos-mplayer-plugin)."
            )
            self.bus.emit(Message("ovos.common_play.media.state",
                                  {"state": MediaState.NO_MEDIA}))

        # Register end of track callback
        for s in self.services:
            s.set_track_start_callback(self.track_start)

        # Setup event handlers
        self.bus.on(f'ovos.{self.namespace}.service.play', self.handle_play)
        self.bus.on(f'ovos.{self.namespace}.service.pause', self.pause)
        self.bus.on(f'ovos.{self.namespace}.service.resume', self.resume)
        self.bus.on(f'ovos.{self.namespace}.service.stop', self.stop)
        self.bus.on(f'ovos.{self.namespace}.service.track_info', self.handle_track_info)
        self.bus.on(f'ovos.{self.namespace}.service.list_backends', self.handle_list_backends)
        self.bus.on(f'ovos.{self.namespace}.service.set_track_position', self.handle_set_track_position)
        self.bus.on(f'ovos.{self.namespace}.service.get_track_position', self.handle_get_track_position)
        self.bus.on(f'ovos.{self.namespace}.service.get_track_length', self.handle_get_track_length)
        self.bus.on(f'ovos.{self.namespace}.service.seek_forward', self.handle_seek_forward)
        self.bus.on(f'ovos.{self.namespace}.service.seek_backward', self.handle_seek_backward)
        self.bus.on(f'ovos.{self.namespace}.service.duck', self.lower_volume)
        self.bus.on(f'ovos.{self.namespace}.service.unduck', self.restore_volume)

        self._loaded.set()  # Report services loaded
        return self.services

    def get_preferred_players(self):
        """Return the ordered list of preferred backend names for this service.

        Reads the per-namespace preference key from config
        (``preferred_audio_services`` / ``preferred_video_services`` /
        ``preferred_web_services``). When no preference is configured the full
        list of loaded backend names is returned so that callers always receive
        a usable, ordered selection list rather than an empty one.

        Returns:
            list[str]: ordered preferred backend names (most-preferred first),
                falling back to every loaded backend's name when no preference
                is configured.
        """
        key = f"preferred_{self.namespace}_services"
        preferred = self.config.get(key)
        if preferred:
            return list(preferred)
        # no preference configured -> fall back to all loaded backends
        return [s.name for s in self.services]

    def handle_media_state_change(self, message: Message):
        """
        if self.current and state == MediaState.LOADED_MEDIA:
            self.current.play()
            self.bus.emit(Message("ovos.common_play.track.state",
                                  {"state": TrackState.PLAYING_AUDIO}))
        """
        state = message.data["state"]
        if self.current and state == MediaState.LOADED_MEDIA:
            try:
                self.current.play()
            except Exception as e:
                LOG.exception(f"Failed to start playback on "
                              f"{self.current.__class__.__name__}: {e}")
                self.bus.emit(Message("ovos.common_play.media.state",
                                      {"state": MediaState.INVALID_MEDIA}))
                self.current = None
                return
            track_state = {
                "audio": TrackState.PLAYING_AUDIO,
                "video": TrackState.PLAYING_VIDEO,
                "web": TrackState.PLAYING_WEBVIEW,
            }.get(self.namespace)
            if track_state is None:
                # custom-namespace backend: no typed PLAYING_* state maps to
                # it, but we must NOT silently drop the event. Normalize to
                # PLAYING_AUDIO (the OCP default playback state) and forward it
                # so downstream consumers (GUI, MPRIS, NowPlaying) still learn
                # that playback has started.
                LOG.warning(
                    f"handle_media_state_change: unknown namespace "
                    f"'{self.namespace}' — normalizing to PLAYING_AUDIO "
                    f"TrackState for LOADED_MEDIA event"
                )
                track_state = TrackState.PLAYING_AUDIO
            self.bus.emit(Message("ovos.common_play.track.state",
                                  {"state": track_state}))

    def wait_for_load(self, timeout=3 * 60):
        """Wait for services to be loaded.

        Args:
            timeout (float): Seconds to wait (default 3 minutes)
        Returns:
            (bool) True if loading completed within timeout, else False.
        """
        return self._loaded.wait(timeout)

    def pause(self, message: Message = None):
        """
            Handler for ovos.media.service.pause. Pauses the current media
            service.

            Args:
                message: message bus message, not used but required
        """
        if not self._is_message_for_service(message):
            return
        if self.current:
            self.current.pause()
            self.current.ocp_pause()

    def resume(self, message: Message = None):
        """
            Handler for ovos.media.service.resume.

            Args:
                message: message bus message, not used but required
        """
        if not self._is_message_for_service(message):
            return
        if self.current:
            self.current.resume()
            self.current.ocp_resume()

    def _perform_stop(self, message: Message = None):
        """Stop mediaservice if active."""
        if not self._is_message_for_service(message):
            return
        if self.current:
            LOG.debug(f'stopping playing service: {self.current}')
            if self.current.stop():
                self.current.ocp_stop()  # emit ocp state events
                if message:
                    msg = message.reply("mycroft.stop.handled",
                                        {"by": "OCP"})
                else:
                    msg = Message("mycroft.stop.handled",
                                  {"by": "OCP"})
                self.bus.emit(msg)

        self.current = None

    def stop(self, message: Message = None):
        """
            Handler for mycroft.stop. Stops any playing service.

            Args:
                message: message bus message, not used but required
        """
        if not self._is_message_for_service(message):
            return
        if time.monotonic() - self.play_start_time > 1:
            with self.service_lock:
                try:
                    self._perform_stop(message)
                except Exception as e:
                    LOG.exception(e)
                    LOG.error("failed to stop!")

    def lower_volume(self, message: Message = None):
        """
            Is triggered when mycroft starts to speak and reduces the volume.

            Args:
                message: message bus message, not used but required
        """
        if not self._is_message_for_service(message):
            return
        if self.current and not self.volume_is_low:
            LOG.debug('lowering volume')
            self.current.lower_volume()
            self.volume_is_low = True

    def restore_volume(self, message: Message = None):
        """Triggered when OVOS is done speaking and restores the volume."""
        if not self._is_message_for_service(message):
            return
        if self.current and self.volume_is_low:
            LOG.debug('restoring volume')
            self.volume_is_low = False
            self.current.restore_volume()

    def play(self, uri, preferred_service: MediaBackend = None):
        """
            play starts playing the media on the preferred service if it
            supports the uri. If not the next best backend is found.

            Args:
                uri: uri of track to play.
                preferred_service: indicates the service the user prefer to play
                                  the tracks.
        """
        uri_type = uri.split(':')[0]

        # check if user requested a particular service
        if preferred_service and uri_type in preferred_service.supported_uris():
            selected_service = preferred_service

        # check if default supports the uri
        elif self.current and uri_type in self.current.supported_uris():
            selected_service = self.current

        else:  # Check if any media service can play the media
            for s in self.services:
                if uri_type in s.supported_uris():
                    LOG.debug(f"Service {s.__class__.__name__} supports URI {uri_type}")
                    selected_service = s
                    break
            else:
                LOG.info('No service found for uri_type: ' + uri_type)
                self.bus.emit(Message("ovos.common_play.media.state",
                                      {"state": MediaState.INVALID_MEDIA}))
                return

        LOG.debug(f"Using {selected_service.__class__.__name__}")
        self.current = selected_service
        self.play_start_time = time.monotonic()
        try:
            # once loaded self.handle_media_state_change is called
            selected_service.load_track(uri)
        except Exception as e:
            LOG.exception(f"Failed to load track '{uri}' on "
                          f"{selected_service.__class__.__name__}: {e}")
            self.bus.emit(Message("ovos.common_play.media.state",
                                  {"state": MediaState.INVALID_MEDIA}))
            self.current = None

    def _is_message_for_service(self, message: Message):
        if not message or not self.validate_source:
            return True
        # F9: docs/configuration.md documents `media.native_sources` as the
        # key to set, but validate_message_context() (ovos_media/utils.py)
        # only ever reads the top-level `native_sources` config key on its
        # own — its `native_sources` parameter was always available for a
        # caller to override that lookup, but nothing here ever passed it,
        # so the documented media-scoped key was silently ignored. self.config
        # IS the media-scoped config (see __init__), so honour its
        # `native_sources` here; the top-level key still works unchanged
        # since we only override when the media-scoped key is actually set.
        return validate_message_context(
            message, native_sources=self.config.get("native_sources"))

    def handle_play(self, message: Message):
        """
            Handler for ovos.media.service.play. Starts playback of a
            tracklist. Also  determines if the user requested a special
            service.

            Args:
                message: message bus message, not used but required
        """
        if not self._is_message_for_service(message):
            return
        with self.service_lock:
            tracks = message.data.get('tracks') or []
            if not tracks:
                LOG.warning(f"ovos.{self.namespace}.service.play: no tracks provided")
                return
            repeat = message.data.get("repeat", False)

            # self.play() takes a single uri, not a tracklist — mirror how
            # ovos-audio's legacy service resolves the first playable track.
            # 'tracks' may be a bare uri string, a list of uri strings, or a
            # list of (uri, mime) tuples.
            if isinstance(tracks, str):
                track_list = [tracks]
            else:
                track_list = list(tracks)

            # C6: an entry may itself be an empty list/tuple (eg.
            # tracks=[[]]) — indexing straight into it used to raise
            # IndexError and kill the request; skip empty entries and use
            # the first genuinely playable one instead.
            uris = []
            for t in track_list:
                if isinstance(t, (list, tuple)):
                    if not t:
                        continue
                    uris.append(t[0])
                else:
                    uris.append(t)
            if not uris:
                LOG.warning(f"ovos.{self.namespace}.service.play: no playable "
                            f"track entries in tracks={track_list!r}")
                return
            uri = uris[0]

            # C6: hand the rest of the tracklist (and repeat) through to the
            # backend path — mirrors ovos-audio's PlaybackService.play, which
            # queues the whole list instead of dropping everything after the
            # first uri. BaseMediaService/MediaBackend only load one uri at a
            # time (no add_list()/play(repeat) on this template), so the
            # remainder is advanced incrementally from track_start() as each
            # track completes.
            self._last_full_playlist = uris
            self._pending_playlist = uris[1:]
            self._pending_repeat = repeat

            # Find if the user wants to use a specific backend
            query = message.data.get("utterance", "").lower()
            for s in self.services:
                try:
                    # match query against "aliases" (assigned from config on load)
                    if any(a.lower() in query.lower() for a in s.aliases):
                        preferred_service = s
                        LOG.debug(s.name + ' would be preferred')
                        break
                except Exception as e:
                    LOG.error(f"failed to parse media service name: {s}")
            else:
                preferred_service = None

            try:
                # Use a timer to avoid blocking the bus event loop
                threading.Timer(0.5, self.play, args=[uri, preferred_service]).start()
            except Exception as e:
                LOG.exception(e)

    def handle_track_info(self, message: Message):
        """
            Returns track info on the message bus.

            Args:
                message: message bus message, not used but required
        """
        if not self._is_message_for_service(message):
            return
        if self.current:
            track_info = self.current.track_info()
        else:
            track_info = {}
        self.bus.emit(message.response(track_info))

    def handle_list_backends(self, message: Message):
        """ Return a dict of available backends. """
        if not self._is_message_for_service(message):
            return
        data = self.available_backends()
        self.bus.emit(message.response(data))

    def handle_get_track_length(self, message: Message):
        """
        getting the duration of the media in milliseconds
        """
        if not self._is_message_for_service(message):
            return
        dur = None
        if self.current:
            dur = self.current.get_track_length()
        self.bus.emit(message.response({"length": dur}))

    def handle_get_track_position(self, message: Message):
        """
        get current position in milliseconds
        """
        if not self._is_message_for_service(message):
            return
        pos = None
        if self.current:
            pos = self.current.get_track_position()
        self.bus.emit(message.response({"position": pos}))

    def handle_set_track_position(self, message: Message):
        """
            Handle message bus command to go to position (in milliseconds)

            Args:
                message: message bus message
        """
        if not self._is_message_for_service(message):
            return
        milliseconds = message.data.get("position")
        if milliseconds and self.current:
            self.current.set_track_position(milliseconds)

    def handle_seek_forward(self, message: Message):
        """
            Handle message bus command to skip X seconds

            Args:
                message: message bus message
        """
        if not self._is_message_for_service(message):
            return
        seconds = message.data.get("seconds", 1)
        if self.current:
            self.current.seek_forward(seconds)

    def handle_seek_backward(self, message: Message):
        """
            Handle message bus command to rewind X seconds

            Args:
                message: message bus message
        """
        if not self._is_message_for_service(message):
            return
        seconds = message.data.get("seconds", 1)
        if self.current:
            self.current.seek_backward(seconds)

    def get_track_length(self) -> Optional[int]:
        """
        Get the duration of the currently loaded track, in milliseconds.

        Mirrors the backend contract defined by
        ``ovos_plugin_manager.templates.media.MediaBackend.get_track_length``,
        which reports/consumes milliseconds throughout.

        Returns:
            (int) duration of the current track in milliseconds, or None if
            no backend is currently active.
        """
        if self.current is None:
            return None
        return self.current.get_track_length()

    def get_track_position(self) -> Optional[int]:
        """
        Get the current playback position, in milliseconds.

        Mirrors the backend contract defined by
        ``ovos_plugin_manager.templates.media.MediaBackend.get_track_position``,
        which reports/consumes milliseconds throughout.

        Returns:
            (int) current position in milliseconds, or None if no backend is
            currently active.
        """
        if self.current is None:
            return None
        return self.current.get_track_position()

    def set_track_position(self, milliseconds: int) -> None:
        """
        Seek to a specific position in the currently loaded track.

        Mirrors the backend contract defined by
        ``ovos_plugin_manager.templates.media.MediaBackend.set_track_position``,
        which reports/consumes milliseconds throughout.

        Args:
            milliseconds (int): position, in milliseconds, to seek to.
        """
        if self.current is None:
            return
        self.current.set_track_position(milliseconds)

    def shutdown(self):
        for s in self.services:
            try:
                LOG.info('shutting down ' + s.name)
                s.shutdown()
            except Exception as e:
                LOG.error('shutdown of ' + s.name + ' failed: ' + repr(e))
        self.remove_listeners()

    def remove_listeners(self):
        self.bus.remove(f'ovos.{self.namespace}.service.play', self.handle_play)
        self.bus.remove(f'ovos.{self.namespace}.service.pause', self.pause)
        self.bus.remove(f'ovos.{self.namespace}.service.resume', self.resume)
        self.bus.remove(f'ovos.{self.namespace}.service.stop', self.stop)
        self.bus.remove(f'ovos.{self.namespace}.service.track_info', self.handle_track_info)
        self.bus.remove(f'ovos.{self.namespace}.service.get_track_position', self.handle_get_track_position)
        self.bus.remove(f'ovos.{self.namespace}.service.set_track_position', self.handle_set_track_position)
        self.bus.remove(f'ovos.{self.namespace}.service.get_track_length', self.handle_get_track_length)
        self.bus.remove(f'ovos.{self.namespace}.service.seek_forward', self.handle_seek_forward)
        self.bus.remove(f'ovos.{self.namespace}.service.seek_backward', self.handle_seek_backward)
        self.bus.remove(f'ovos.{self.namespace}.service.list_backends', self.handle_list_backends)
        self.bus.remove(f'ovos.{self.namespace}.service.duck', self.lower_volume)
        self.bus.remove(f'ovos.{self.namespace}.service.unduck', self.restore_volume)
        self.bus.remove("ovos.common_play.media.state", self.handle_media_state_change)

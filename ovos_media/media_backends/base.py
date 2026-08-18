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
from ovos_utils.log import LOG
from ovos_utils.process_utils import MonotonicEvent


def _safe_supported_uris(s) -> list:
    """Call s.supported_uris() defensively.

    A plugin raising here must not abort backend listing/selection for
    every other, healthy backend. Treated the same as a plugin that
    supports nothing. The return value is also validated: a plugin
    returning a bare str (eg. "filesystem" instead of ["file"]) would
    otherwise be iterated character-by-character, giving substring
    semantics downstream ("file" in "filesystem") that pick the wrong
    backend for a uri_type.
    """
    try:
        uris = s.supported_uris()
    except Exception:
        LOG.exception(f"{getattr(s, 'name', s.__class__.__name__)}"
                      f".supported_uris() raised")
        return []
    if isinstance(uris, (list, tuple, set)):
        return list(uris)
    LOG.warning(f"{getattr(s, 'name', s.__class__.__name__)}"
               f".supported_uris() returned {type(uris).__name__}, expected"
               f" a list/tuple/set - treating as unsupported")
    return []


class BaseMediaService:

    def __init__(self, bus, namespace: str, plugin_loader: Callable,
                 config=None, autoload=True,
                 on_stop: Callable = None):
        """
            Args:
                bus: OVOS messagebus
                on_stop: optional callback invoked at the start of stop(),
                    in the calling thread. OCPMediaPlayer uses it to learn
                    that a stop it did not itself request happened here (a
                    skill stopping this service directly), so the
                    END_OF_MEDIA the backend's ocp_stop() is about to emit
                    does not advance the queue.
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
        self._init_runtime_state(on_stop=on_stop)

        self._loaded = MonotonicEvent()
        if autoload:
            self.load_services()
        self.bus.on("ovos.common_play.media.state", self.handle_media_state_change)

    def _init_runtime_state(self, on_stop: Callable = None) -> None:
        """Initialise the plain in-memory playback bookkeeping.

        Split out of __init__ so it can be applied on its own to a service
        whose backends and bus wiring are supplied by other means.
        """
        self.on_stop = on_stop
        # Handle for the deferred stop started by stop() when a stop lands
        # inside the post-play-start guard window. Cancellable and daemonic
        # — an orphaned timer used to survive a stop and fire into an
        # already shut-down service.
        self._deferred_stop_timer: Optional[threading.Timer] = None

    def available_backends(self):
        """Return available media backends.

        Returns:
            dict with backend names as keys
        """
        data = {}
        for s in self.services:
            try:
                info = {
                    'supported_uris': _safe_supported_uris(s),
                    'remote': isinstance(s, RemoteAudioPlayerBackend) or
                              isinstance(s, RemoteWebPlayerBackend) or
                              isinstance(s, RemoteVideoPlayerBackend)
                }
                data[s.name] = info
            except Exception:
                LOG.exception(f"{s.__class__.__name__} raised while listing"
                              f" available backends - skipping")
                continue
        return data

    def can_play(self, uri: str, preferred_service: MediaBackend = None) -> bool:
        """Return True if some loaded backend would claim *uri*, without
        actually loading it. Mirrors the backend-selection resolution
        _play() performs, so a caller can decide whether to attempt this
        service at all (eg. before falling back to a different playback
        type) instead of dispatching blind and reacting to the resulting
        MediaState.INVALID_MEDIA.
        """
        uri_type = uri.split(':')[0]
        if preferred_service and uri_type in _safe_supported_uris(preferred_service):
            return True
        if self.current and uri_type in _safe_supported_uris(self.current):
            return True
        return any(uri_type in _safe_supported_uris(s) for s in self.services)

    def track_start(self, track):
        """Callback method called from the services to indicate start of
        playback of a track or end of playlist.
        """
        if track:
            # Inform about the track about to start.
            LOG.info(f'New {self.namespace} track coming up!')
            self.bus.emit(Message(f'ovos.{self.namespace}.playing_track',
                                  data={'track': track}))
        else:
            # If no track is about to start last track of the queue has been
            # played.
            LOG.debug('End of playlist!')
            self.bus.emit(Message(f'ovos.{self.namespace}.queue_end'))

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
                f"Install at least one backend plugin (e.g. ovos-media-plugin-vlc, ovos-media-plugin-mplayer)."
            )
            self.bus.emit(Message("ovos.common_play.media.state",
                                  {"state": MediaState.NO_MEDIA}))

        # Register end of track callback
        for s in self.services:
            s.set_track_start_callback(self.track_start)

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
        names = []
        for s in self.services:
            try:
                names.append(s.name)
            except Exception as e:
                LOG.exception(f"Failed to get name for backend "
                              f"{s.__class__.__name__}: {e}")
        return names

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

    def pause(self):
        """Pause the current media service."""
        if self.current:
            self.current.ocp_pause()

    def resume(self):
        """Resume the current media service."""
        if self.current:
            self.current.ocp_resume()

    def _cancel_deferred_stop(self):
        """Cancel any pending deferred stop so it cannot fire late."""
        if self._deferred_stop_timer is not None:
            self._deferred_stop_timer.cancel()
            self._deferred_stop_timer = None

    def _perform_stop(self):
        """Stop mediaservice if active."""
        self._cancel_deferred_stop()
        if self.current:
            LOG.debug(f'stopping playing service: {self.current}')
            if self.current.stop():
                self.current.ocp_stop()  # emit ocp state events
                self.bus.emit(Message("mycroft.stop.handled", {"by": "OCP"}))

        self.current = None

    def stop(self):
        """Stop any playing service."""
        # The on_stop callback is player-wide (shared _stop_requested
        # across audio/video/web), but this service instance may have no
        # active backend at all — eg. a skill calls the video interface's
        # stop() while only audio is playing. Firing on_stop unconditionally
        # in that case flags a stop the player never asked for, which then
        # swallows the NEXT unrelated END_OF_MEDIA from whichever service is
        # actually playing. Only a real stop (self.current is not None) is
        # allowed to signal it.
        if self.on_stop is not None and self.current is not None:
            # Tell the owning player this end-of-playback is a stop, before
            # the backend's ocp_stop() emits END_OF_MEDIA.
            try:
                self.on_stop()
            except Exception as e:
                LOG.exception(f"on_stop callback failed: {e}")
        elapsed = time.monotonic() - self.play_start_time
        if elapsed > 1:
            with self.service_lock:
                try:
                    self._perform_stop()
                except Exception as e:
                    LOG.exception(e)
                    LOG.error("failed to stop!")
        else:
            # The <1s guard exists to swallow the stop that ovos-core fires
            # right as playback begins. Dropping it outright meant the internal
            # caller (OCPMediaPlayer.stop) reported STOPPED while audio kept
            # playing forever. Defer the stop past the guard window instead.
            LOG.debug(f"{self.namespace}: stop within the start guard window — "
                      f"deferring it")
            self._schedule_deferred_stop(1.0 - elapsed + 0.05)

    def _schedule_deferred_stop(self, delay: float):
        """Run _perform_stop once the post-play-start guard window closes."""
        if self._deferred_stop_timer is not None:
            self._deferred_stop_timer.cancel()
        timer = threading.Timer(max(delay, 0.0), self._deferred_stop)
        timer.daemon = True
        self._deferred_stop_timer = timer
        timer.start()

    def _deferred_stop(self):
        self._deferred_stop_timer = None
        with self.service_lock:
            try:
                self._perform_stop()
            except Exception as e:
                LOG.exception(e)
                LOG.error("failed to stop!")

    def lower_volume(self):
        """Lower volume, eg. when mycroft starts to speak (ducking)."""
        if self.current and not self.volume_is_low:
            LOG.debug('lowering volume')
            self.current.lower_volume()
            self.volume_is_low = True

    def restore_volume(self):
        """Restore volume, eg. once mycroft is done speaking (unducking)."""
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
        # A new playback request supersedes any deferred stop aimed at the
        # previous track.
        self._cancel_deferred_stop()
        self._play(uri, preferred_service)

    def _play(self, uri, preferred_service: MediaBackend = None):
        """Select a backend and load *uri* on it.

        Internal entry point kept separate from play(): play() additionally
        cancels a pending deferred stop, which a caller advancing an
        already-active queue must not do.
        """
        uri_type = uri.split(':')[0]

        # check if user requested a particular service
        if preferred_service and uri_type in _safe_supported_uris(preferred_service):
            selected_service = preferred_service

        # check if default supports the uri
        elif self.current and uri_type in _safe_supported_uris(self.current):
            selected_service = self.current

        else:  # Check if any media service can play the media
            for s in self.services:
                if uri_type in _safe_supported_uris(s):
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
        # A pending deferred stop must not fire into a shut-down backend
        self._cancel_deferred_stop()
        for s in self.services:
            try:
                LOG.info('shutting down ' + s.name)
                s.shutdown()
            except Exception as e:
                LOG.error('shutdown of ' + s.name + ' failed: ' + repr(e))
        self.remove_listeners()

    def remove_listeners(self):
        self.bus.remove("ovos.common_play.media.state", self.handle_media_state_change)

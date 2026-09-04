import abc
import threading
import time
from functools import partial
from threading import Lock
from typing import Callable, Optional

from ovos_plugin_manager.templates.media import MediaBackend, PlaybackEvent, \
    RemoteAudioPlayerBackend, RemoteVideoPlayerBackend, RemoteWebPlayerBackend
from ovos_utils.ocp import MediaState, TrackState

from ovos_bus_client.message import Message
from ovos_config.config import Configuration
from ovos_utils.log import LOG
from ovos_utils.process_utils import MonotonicEvent

_REMOTE_BASES = (RemoteAudioPlayerBackend, RemoteVideoPlayerBackend,
                 RemoteWebPlayerBackend)


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


def _is_remote(x) -> bool:
    """Whether *x* (a backend instance OR class) drives remote gear.

    Prefers the v2 template's own ``is_remote`` class attribute - readable
    on both an instance and the class itself, no instantiation required -
    and falls back to an isinstance/issubclass check against the three
    ``Remote*PlayerBackend`` bases only for a plugin that predates that
    flag (a v2 plugin that forgot to set it, or a stale v1 one).
    """
    is_remote = getattr(x, "is_remote", None)
    if isinstance(is_remote, bool):
        return is_remote
    if isinstance(x, type):
        return issubclass(x, _REMOTE_BASES)
    return isinstance(x, _REMOTE_BASES)


class BaseMediaService:

    def __init__(self, bus, namespace: str, plugin_loader: Callable,
                 config=None, autoload=True,
                 on_stop: Callable = None,
                 on_external_event: Callable = None):
        """
            Args:
                bus: OVOS messagebus
                on_stop: optional callback invoked at the start of stop(),
                    in the calling thread. OCPMediaPlayer uses it to learn
                    that a stop it did not itself request happened here (a
                    skill stopping this service directly), so the
                    END_OF_MEDIA _perform_stop() is about to emit
                    does not advance the queue.
                on_external_event: optional callback invoked (in the
                    reporting backend's own thread) with a
                    ``PlaybackEvent.{PAUSED,RESUMED,STOPPED}`` this service
                    did not itself ask for - eg. a Chromecast app or a
                    Music Assistant UI pausing/resuming/stopping playback on
                    its own. OCPMediaPlayer uses it to reflect that
                    transport change into its own state machine.
        """
        self.bus = bus
        self.namespace = namespace
        self.plugin_loader = plugin_loader
        self.config = config or Configuration().get("media") or {}
        # INVARIANT: a plugin verb (load_track/play/pause/resume/stop/
        # lower_volume/restore_volume/get_track_*/bind_event_reporter) is
        # NEVER called while holding service_lock. A plugin may call
        # report() synchronously from inside any of those verbs (eg.
        # stop() reporting PlaybackEvent.STOPPED before returning) - report()
        # runs on the SAME thread and re-enters _handle_backend_event, which
        # needs this same lock. Held across the verb call, that deadlocks
        # (service_lock is a plain, non-reentrant Lock - and this typically
        # wedges the single-worker dispatcher thread permanently, killing
        # every subsequent player command, not just this one call).
        self.service_lock = Lock()

        self.default = None
        self.services = []
        self.current = None
        # The uri _play() last loaded onto self.current, recorded under
        # service_lock at the same point self.current is set. Best-effort
        # provenance for _handle_backend_event to recognise a stale
        # END_OF_MEDIA/ERROR from a track a since-superseded load already
        # moved past, on a backend instance reused across tracks (see
        # _handle_backend_event's docstring for why backend identity plus a
        # bind-time generation cannot do this - report() only ever
        # dereferences the CURRENT reporter, never a stale closure).
        self._current_uri: Optional[str] = None
        self.play_start_time = 0
        self.volume_is_low = False
        self._init_runtime_state(on_stop=on_stop, on_external_event=on_external_event)

        self._loaded = MonotonicEvent()
        if autoload:
            self.load_services()

    def _init_runtime_state(self, on_stop: Callable = None,
                            on_external_event: Callable = None) -> None:
        """Initialise the plain in-memory playback bookkeeping.

        Split out of __init__ so it can be applied on its own to a service
        whose backends and bus wiring are supplied by other means.
        """
        self.on_stop = on_stop
        self.on_external_event = on_external_event
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
                    'remote': _is_remote(s)
                }
                data[s.name] = info
            except Exception:
                LOG.exception(f"{s.__class__.__name__} raised while listing"
                              f" available backends - skipping")
                continue
        return data

    def claimed_schemes(self) -> set:
        """Every uri scheme/prefix some loaded backend declares via
        ``supported_uris()``.

        Cheap and IO-free (unlike :meth:`can_play`, which may probe a
        backend) - just the union of what each loaded backend already
        advertises. Computed fresh from the current ``self.services`` on
        every call, so it always reflects the backends presently loaded.
        """
        schemes = set()
        for s in self.services:
            schemes.update(_safe_supported_uris(s))
        return schemes

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
        current = self.current
        if current and uri_type in _safe_supported_uris(current):
            return True
        return any(uri_type in _safe_supported_uris(s) for s in self.services)

    def _load_plugin(self, player_name: str, plug_cfg: dict, plugs: dict,
                      local: list, remote: list) -> None:
        """Instantiate one backend and append it to *local* or *remote*.

        A plugin whose constructor raises is logged and skipped - one
        broken plugin must never block its siblings from loading. A
        ``TypeError`` specifically is called out as a likely MediaBackend v1
        plugin (missing a v2-only concrete method), since that is by far the
        most common and most confusing way an unported plugin fails here.
        """
        plug_name = plug_cfg["module"] if "module" in plug_cfg else player_name
        try:
            service = plugs[plug_name](plug_cfg, self.bus)
        except TypeError:
            LOG.exception(
                f"{plug_name} raised TypeError while instantiating - it is "
                f"likely a MediaBackend v1 plugin; ovos-media requires "
                f"MediaBackend v2 (ovos-plugin-manager>=3.0.0a1) - upgrade "
                f"the plugin")
            return
        except Exception:
            LOG.exception(f"Failed to load {plug_name}")
            return

        try:
            fallback = [player_name] if player_name == plug_name else [player_name, plug_name]
            service.aliases = plug_cfg.get("aliases", []) or fallback
            service.name = player_name
            if _is_remote(service):
                remote.append(service)
            else:
                local.append(service)
            LOG.info(f"Loaded {self.__class__.__name__} plugin: {plug_name}")
        except Exception:
            LOG.exception(f"Failed to load {plug_name}")

    def load_services(self):
        """Method for loading services.

        Every backend plugin installed for this namespace is loaded
        unless disabled - either individually, via an ``active: false``
        entry under ``{namespace}_players``, or altogether, via
        ``autoload_backends: false``. Configured entries load first, in
        config order, and keep their configured name/aliases; discovered
        plugins not referenced by any configured entry load afterwards,
        in sorted order, with their entry-point name as name/alias and an
        empty (plugin-default) config.

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

        configured_modules = set()
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
            configured_modules.add(plug_name)
            if plug_name not in plugs:
                LOG.error(f"{plug_name} configured but not installed")
                continue
            if not plug_cfg.get("active", True):
                LOG.info(f"{plug_name} is disabled in configuration")
                continue
            self._load_plugin(player_name, plug_cfg, plugs, local, remote)

        if self.config.get("autoload_backends", True):
            for plug_name in sorted(plugs):
                if plug_name in configured_modules:
                    continue
                plug_cls = plugs[plug_name]
                if not isinstance(plug_cls, type):
                    LOG.warning(f"{plug_name} is not a class "
                                f"({type(plug_cls).__name__}) - skipping "
                                f"autoload")
                    continue
                if _is_remote(plug_cls):
                    LOG.info(f"{plug_name} drives a remote target - add an "
                             f"explicit '{self.namespace}_players' entry to "
                             f"load it")
                    continue
                self._load_plugin(plug_name, {}, plugs, local, remote)

        # Sort services so local services are checked first
        self.services = local + remote

        if not self.services:
            LOG.error(
                f"No {self.namespace} backends loaded — all {self.namespace} playback will fail. "
                f"Install a {self.namespace} backend plugin (e.g. ovos-media-plugin-vlc, ovos-media-plugin-mplayer), "
                f"or check the 'autoload_backends' and 'active' settings under "
                f"'{self.namespace}_players' in configuration."
            )
            self.bus.emit(Message("ovos.common_play.media.state",
                                  {"state": MediaState.NO_MEDIA}))

        # Bind every backend's physical-event reporter. Nothing is
        # "current" yet, so _handle_backend_event drops anything reported
        # before this service's first real play() (backend identity check
        # fails). One broken plugin's bind_event_reporter() must not stop
        # its siblings from binding - and this call happens outside any
        # lock, same as every other plugin verb (see service_lock's
        # invariant comment in __init__).
        for s in self.services:
            try:
                s.bind_event_reporter(partial(self._handle_backend_event, s))
            except Exception:
                LOG.exception(f"Failed to bind event reporter for "
                              f"{getattr(s, 'name', s.__class__.__name__)} - "
                              f"this backend will report nothing")

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

    def _track_state_for_namespace(self) -> TrackState:
        """The TrackState.PLAYING_* this namespace maps to, normalizing an
        unrecognized/custom namespace to PLAYING_AUDIO (with a warning)
        instead of silently dropping it. Shared by TRACK_START handling and
        resume()."""
        track_state = {
            "audio": TrackState.PLAYING_AUDIO,
            "video": TrackState.PLAYING_VIDEO,
            "web": TrackState.PLAYING_WEBVIEW,
        }.get(self.namespace)
        if track_state is None:
            LOG.warning(f"_track_state_for_namespace: unknown namespace "
                       f"'{self.namespace}' — normalizing to PLAYING_AUDIO")
            track_state = TrackState.PLAYING_AUDIO
        return track_state

    def _handle_backend_event(self, backend: MediaBackend,
                              event: PlaybackEvent, **data) -> None:
        """Translate a physical PlaybackEvent reported by *backend* into the
        ovos.common_play.* wire messages this service used to emit from its
        own listener on the shared bus (the v1 self-listening loop this port
        removes - see load_services()/bind_event_reporter).

        A backend identity mismatch (``backend is not self.current``) drops
        an event from a backend deactivated by a playback-type switch or
        superseded entirely by a different one. It does NOT, on its own,
        catch two tracks played back-to-back on the SAME backend instance -
        a very common case (eg. a persistent vlc/mpv process reused across
        tracks) where a late END_OF_MEDIA/ERROR from track 1 is otherwise
        indistinguishable from one genuinely about track 2.

        An earlier revision tried to solve that with a monotonic
        "generation" baked into a fresh partial rebound on every _play() -
        that does not work: the OPM template's ``report()`` always
        dereferences ``self._event_reporter`` fresh, at CALL time, never a
        stale closure a caller happened to save earlier, so no in-flight
        physical event can ever actually be carrying an old generation by
        the time it reaches here (confirmed by an executed repro: a
        watcher-thread END_OF_MEDIA for track 1 sailed straight through
        while track 2 was already playing). The generation counter
        protected nothing and has been removed.

        What IS available: ``self._current_uri``, the uri _play() last
        loaded onto this backend (see _play()). For END_OF_MEDIA/ERROR
        specifically, when the reported event carries a ``uri`` kwarg that
        disagrees with it, the event is stale and dropped. This is
        best-effort and documented as such: a plugin that does not attach
        ``uri=`` to its report() call gives this nothing to compare
        against, and the event passes through un-filtered - no worse than
        v1, which had no staleness detection at all. The OPM template's
        docs are being amended in parallel to say plugins SHOULD attach
        ``uri=<the uri that ended/errored>`` to these two events.

        Only self.current/self._current_uri are read here, under
        service_lock - no plugin verb is ever called while holding it (see
        the invariant comment on service_lock's definition).
        """
        with self.service_lock:
            if backend is not self.current:
                LOG.debug(f"Ignoring {event} from an inactive {self.namespace} "
                          f"backend ({backend.__class__.__name__})")
                return
            if event in (PlaybackEvent.ERROR, PlaybackEvent.END_OF_MEDIA):
                event_uri = data.get("uri")
                if event_uri is not None and event_uri != self._current_uri:
                    LOG.debug(f"Ignoring stale {event} from "
                              f"{backend.__class__.__name__}: reported uri "
                              f"{event_uri!r} does not match the currently "
                              f"loaded {self._current_uri!r}")
                    return

        if event == PlaybackEvent.TRACK_START:
            # mirrors what the old handle_media_state_change emitted right
            # after calling current.play() on LOADED_MEDIA - now driven by
            # the plugin's own confirmation that playback actually started,
            # instead of assumed synchronously with the load.
            self.bus.emit(Message("ovos.common_play.track.state",
                                  {"state": self._track_state_for_namespace()}))

        elif event == PlaybackEvent.END_OF_MEDIA:
            # a natural end, reported by the backend itself rather than
            # caused by our own stop() (_perform_stop emits this same
            # message on an explicit stop). OCPMediaPlayer is the sole
            # consumer of END_OF_MEDIA on this channel and drives queue
            # advance from it (handle_player_media_update ->
            # handle_playback_ended) - unchanged by this port.
            self.bus.emit(Message("ovos.common_play.media.state",
                                  {"state": MediaState.END_OF_MEDIA}))

        elif event == PlaybackEvent.ERROR:
            LOG.error(f"{backend.__class__.__name__} reported a playback "
                      f"error: {data.get('error')}")
            # self.current is deliberately left untouched here - matches
            # v1's ownership: the player's on_invalid_stream/play_next flow
            # (triggered by the INVALID_MEDIA message below, see
            # OCPMediaPlayer.handle_player_media_update) owns the transition
            # away from a failed track, not this layer.
            self.bus.emit(Message("ovos.common_play.media.state",
                                  {"state": MediaState.INVALID_MEDIA}))

        elif event in (PlaybackEvent.PAUSED, PlaybackEvent.RESUMED,
                      PlaybackEvent.STOPPED):
            # A transport change this daemon did not itself ask for - a
            # Chromecast app, a Music Assistant UI, a hardware remote...
            # OCPMediaPlayer's own pause()/resume()/stop() already emit the
            # matching player.state for a request THEY drove; this is the
            # other direction, and needs a way back into the player's state
            # machine - see on_external_event (BaseMediaService.__init__).
            if self.on_external_event is not None:
                try:
                    self.on_external_event(event)
                except Exception as e:
                    LOG.exception(f"on_external_event callback failed: {e}")

        else:
            LOG.debug(f"Unhandled PlaybackEvent {event} from "
                      f"{backend.__class__.__name__}: {data}")

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
        current = self.current
        if current:
            current.pause()

    def resume(self):
        """Resume the current media service."""
        current = self.current
        if current:
            current.resume()
            # v1's ocp_resume() emitted this same track.state alongside
            # player.state PLAYING (the latter is OCPMediaPlayer's own
            # resume()'s job, via set_player_state - see player/__init__.py).
            self.bus.emit(Message("ovos.common_play.track.state",
                                  {"state": self._track_state_for_namespace()}))

    def _cancel_deferred_stop(self):
        """Cancel any pending deferred stop so it cannot fire late."""
        if self._deferred_stop_timer is not None:
            self._deferred_stop_timer.cancel()
            self._deferred_stop_timer = None

    def _perform_stop(self):
        """Stop mediaservice if active.

        Owns its own (brief) lock section internally, scoped to ONLY the
        snapshot-and-clear of self.current/self._current_uri - current.stop()
        (the actual plugin verb call) runs OUTSIDE the lock. This is the fix
        for a confirmed deadlock: current.stop() previously ran WHILE
        service_lock was held (by the caller, stop()/_deferred_stop()), and
        a backend reporting synchronously from inside its own stop() (eg.
        ``self.report(PlaybackEvent.STOPPED)`` before returning) re-entered
        _handle_backend_event on the SAME thread, which needs this same,
        non-reentrant Lock - permanent wedge (this ran on the dispatcher
        thread, so it killed every subsequent player command, not just this
        one call). See service_lock's own invariant comment in __init__.

        self.current is read exactly once into a local (a still-earlier
        version double-read it, `if self.current: ... if
        self.current.stop(): ...`, racing a concurrent
        _handle_backend_event ERROR/END_OF_MEDIA branch that could set it
        None in between).
        """
        self._cancel_deferred_stop()
        with self.service_lock:
            current = self.current
            self.current = None
            self._current_uri = None
        if current is None:
            return
        LOG.debug(f'stopping playing service: {current}')
        if current.stop():
            # v1 backends emitted these two messages themselves from
            # ocp_stop(); the daemon owns them now. player.state STOPPED
            # is NOT re-emitted here - OCPMediaPlayer.stop() already sets
            # it (AFTER routing this call - see player/__init__.py's
            # stop(), which routes to the adapters first and calls
            # set_player_state last).
            self.bus.emit(Message("ovos.common_play.media.state",
                                  {"state": MediaState.END_OF_MEDIA}))
            self.bus.emit(Message("mycroft.stop.handled", {"by": "OCP"}))

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
            # _perform_stop() emits END_OF_MEDIA.
            try:
                self.on_stop()
            except Exception as e:
                LOG.exception(f"on_stop callback failed: {e}")
        elapsed = time.monotonic() - self.play_start_time
        if elapsed > 1:
            # _perform_stop() owns its own (brief) locking internally now -
            # it must NOT be called from within a `with self.service_lock:`
            # block here, or the plugin verb call inside it (current.stop(),
            # deliberately run unlocked - see _perform_stop's docstring)
            # would still be reachable from a thread already holding this
            # same non-reentrant lock one frame up, right back to the
            # deadlock this shape exists to avoid.
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
        # see stop()'s comment: _perform_stop() must not be called from
        # inside a `with self.service_lock:` block
        try:
            self._perform_stop()
        except Exception as e:
            LOG.exception(e)
            LOG.error("failed to stop!")

    def lower_volume(self):
        """Lower volume, eg. when mycroft starts to speak (ducking)."""
        current = self.current
        if current and not self.volume_is_low:
            LOG.debug('lowering volume')
            current.lower_volume()
            self.volume_is_low = True

    def restore_volume(self):
        """Restore volume, eg. once mycroft is done speaking (unducking)."""
        current = self.current
        if current and self.volume_is_low:
            LOG.debug('restoring volume')
            self.volume_is_low = False
            current.restore_volume()

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

    def _select_service(self, uri: str, preferred_service: Optional[MediaBackend],
                        current: Optional[MediaBackend]):
        """Resolve which backend should play *uri*.

        *current* is a single unlocked snapshot the caller took of
        self.current - matches every other verb method's read pattern (see
        pause()/resume()/etc.), and keeps supported_uris() calls (a plugin
        verb) from ever running while service_lock is held. Best-effort:
        self.current may already have moved on by the time the actual
        selection below runs; that only risks picking a slightly stale
        "reuse the current backend" preference, never a correctness issue -
        the authoritative commit of self.current happens under the lock in
        _play(), after this returns.
        """
        uri_type = uri.split(':')[0]

        if preferred_service and uri_type in _safe_supported_uris(preferred_service):
            return preferred_service
        if current and uri_type in _safe_supported_uris(current):
            return current
        for s in self.services:
            if uri_type in _safe_supported_uris(s):
                LOG.debug(f"Service {s.__class__.__name__} supports URI {uri_type}")
                return s
        return None

    def _play(self, uri, preferred_service: MediaBackend = None):
        """Select a backend and load *uri* on it.

        Internal entry point kept separate from play(): play() additionally
        cancels a pending deferred stop, which a caller advancing an
        already-active queue must not do.
        """
        selected_service = self._select_service(uri, preferred_service, self.current)
        if selected_service is None:
            LOG.info('No service found for uri_type: ' + uri.split(':')[0])
            self.bus.emit(Message("ovos.common_play.media.state",
                                  {"state": MediaState.INVALID_MEDIA}))
            return

        LOG.debug(f"Using {selected_service.__class__.__name__}")
        with self.service_lock:
            self.current = selected_service
            self._current_uri = uri
            self.play_start_time = time.monotonic()

        # bind_event_reporter is a plugin verb - never called while holding
        # service_lock (see its invariant comment in __init__)
        try:
            selected_service.bind_event_reporter(
                partial(self._handle_backend_event, selected_service))
        except Exception:
            LOG.exception(f"Failed to rebind event reporter for "
                          f"{selected_service.__class__.__name__}")

        try:
            # v2 load_track is synchronous and reports nothing itself - it
            # only signals load success/failure via its return value. The
            # daemon owns the LOADED_MEDIA/INVALID_MEDIA transition on that
            # value, where v1 backends emitted LOADED_MEDIA themselves and
            # this service learned of it back over the shared bus.
            loaded = selected_service.load_track(uri)
        except Exception as e:
            LOG.exception(f"Failed to load track '{uri}' on "
                          f"{selected_service.__class__.__name__}: {e}")
            self.bus.emit(Message("ovos.common_play.media.state",
                                  {"state": MediaState.INVALID_MEDIA}))
            with self.service_lock:
                if self.current is selected_service:
                    self.current = None
                    self._current_uri = None
            return

        if loaded is None:
            # a real v2 backend always returns True/False; None means
            # load_track() fell off the end of the method without a return
            # statement - the single most common tell of an unported
            # MediaBackend v1 plugin (v1's load_track() returned nothing).
            LOG.error(f"{selected_service.__class__.__name__}.load_track() "
                      f"returned None - likely a MediaBackend v1 plugin "
                      f"(ovos-media requires MediaBackend v2, "
                      f"ovos-plugin-manager>=3.0.0a1) - upgrade the plugin. "
                      f"Treating as a failed load.")
            loaded = False

        if not loaded:
            LOG.warning(f"{selected_service.__class__.__name__} failed to "
                       f"load '{uri}'")
            self.bus.emit(Message("ovos.common_play.media.state",
                                  {"state": MediaState.INVALID_MEDIA}))
            with self.service_lock:
                if self.current is selected_service:
                    self.current = None
                    self._current_uri = None
            return

        self.bus.emit(Message("ovos.common_play.media.state",
                              {"state": MediaState.LOADED_MEDIA}))
        try:
            # v1 waited to learn of LOADED_MEDIA back over the bus before
            # starting playback (handle_media_state_change); load_track's
            # bool return lets the daemon start it directly. The matching
            # track.state PLAYING_* now comes from the backend's own
            # TRACK_START PlaybackEvent (see _handle_backend_event), once
            # playback has actually, physically started.
            selected_service.play()
        except Exception as e:
            LOG.exception(f"Failed to start playback on "
                          f"{selected_service.__class__.__name__}: {e}")
            self.bus.emit(Message("ovos.common_play.media.state",
                                  {"state": MediaState.INVALID_MEDIA}))
            with self.service_lock:
                if self.current is selected_service:
                    self.current = None
                    self._current_uri = None

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
        current = self.current
        if current is None:
            return None
        return current.get_track_length()

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
        current = self.current
        if current is None:
            return None
        return current.get_track_position()

    def set_track_position(self, milliseconds: int) -> None:
        """
        Seek to a specific position in the currently loaded track.

        Mirrors the backend contract defined by
        ``ovos_plugin_manager.templates.media.MediaBackend.set_track_position``,
        which reports/consumes milliseconds throughout. Whether the backend
        actually honours it is gated by its own ``can_seek`` flag inside the
        OPM template (``MediaBackend.set_track_position`` no-ops by default
        when ``can_seek`` is False) - this layer delegates unconditionally.

        Args:
            milliseconds (int): position, in milliseconds, to seek to.
        """
        current = self.current
        if current is None:
            return
        current.set_track_position(milliseconds)

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
        """No-op: this service no longer subscribes to the shared bus itself
        (see load_services()/bind_event_reporter - it listens to its own
        backends via a direct reporter callback, not a bus subscription).
        Kept as a call site for shutdown() so a subclass that adds its own
        listeners still has a natural place to tear them down."""

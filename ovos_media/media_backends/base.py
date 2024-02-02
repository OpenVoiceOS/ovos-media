import abc
import time
from threading import Lock
from typing import Callable

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
        for player_name, plug_cfg in self.config.get(f"{self.namespace}_players", {}).items():
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
        return []

    def handle_media_state_change(self, message: Message):
        """
        if self.current and state == MediaState.LOADED_MEDIA:
            self.current.play()
            self.bus.emit(Message("ovos.common_play.track.state",
                                  {"state": TrackState.PLAYING_AUDIO}))
        """
        state = message.data["state"]
        if self.current and state == MediaState.LOADED_MEDIA:
            self.current.play()
            if self.namespace == "audio":
                self.bus.emit(Message("ovos.common_play.track.state",
                                      {"state": TrackState.PLAYING_AUDIO}))
            elif self.namespace == "video":
                self.bus.emit(Message("ovos.common_play.track.state",
                                      {"state": TrackState.PLAYING_VIDEO}))
            elif self.namespace == "web":
                self.bus.emit(Message("ovos.common_play.track.state",
                                      {"state": TrackState.PLAYING_WEBVIEW}))
            else:
                pass  # ???

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
                return

        LOG.debug(f"Using {selected_service.__class__.__name__}")
        self.current = selected_service
        self.play_start_time = time.monotonic()
        # once loaded self.handle_media_state_change is called
        selected_service.load_track(uri)

    def _is_message_for_service(self, message: Message):
        if not message or not self.validate_source:
            return True
        return validate_message_context(message)

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
            tracks = message.data['tracks']

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
                self.play(tracks, preferred_service)
                time.sleep(0.5)
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

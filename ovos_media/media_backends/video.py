from ovos_bus_client.message import Message
from ovos_utils.log import LOG
from ovos_utils.ocp import TrackState, MediaState

from ovos_plugin_manager.ocp import find_ocp_video_plugins
from ovos_plugin_manager.templates.media import RemoteVideoPlayerBackend
from .base import BaseMediaService


class VideoService(BaseMediaService):
    """ Video Service class.
        Handles playback of video and selecting proper backend for the uri
        to be played.
    """

    def load_services(self):
        """Method for loading services.

        Sets up the global service, default and registers the event handlers
        for the subsystem.
        """
        local = []
        remote = []

        plugs = find_ocp_video_plugins()
        for player_name, plug_cfg in self.config.get("video_players", {}).items():
            plug_name = plug_cfg["module"]
            try:
                service = plugs[plug_name](plug_cfg, self.bus)
                if isinstance(service, RemoteVideoPlayerBackend):
                    remote.append(service)
                else:
                    local.append(service)
            except:
                LOG.exception(f"Failed to load {plug_name}")

        # Sort services so local services are checked first
        self.services = local + remote

        # Register end of track callback
        for s in self.services:
            s.set_track_start_callback(self.track_start)

        # Setup event handlers
        self.bus.on('ovos.video.service.play', self.handle_play)
        self.bus.on('ovos.video.service.pause', self.pause)
        self.bus.on('ovos.video.service.resume', self.resume)
        self.bus.on('ovos.video.service.stop', self.stop)
        self.bus.on('ovos.video.service.track_info', self.handle_track_info)
        self.bus.on('ovos.video.service.list_backends', self.handle_list_backends)
        self.bus.on('ovos.video.service.set_track_position', self.handle_set_track_position)
        self.bus.on('ovos.video.service.get_track_position', self.handle_get_track_position)
        self.bus.on('ovos.video.service.get_track_length', self.handle_get_track_length)
        self.bus.on('ovos.video.service.seek_forward', self.handle_seek_forward)
        self.bus.on('ovos.video.service.seek_backward', self.handle_seek_backward)
        self.bus.on('ovos.video.service.duck', self.lower_volume)
        self.bus.on('ovos.video.service.unduck', self.restore_volume)

        self._loaded.set()  # Report services loaded
        return self.services

    def track_start(self, track):
        """Callback method called from the services to indicate start of
        playback of a track or end of playlist.
        """
        if track:
            # Inform about the track about to start.
            LOG.debug('New track coming up!')
            self.bus.emit(Message('ovos.video.playing_track',
                                  data={'track': track}))
        else:
            # If no track is about to start last track of the queue has been
            # played.
            LOG.debug('End of playlist!')
            self.bus.emit(Message('ovos.video.queue_end'))

    def handle_media_state_change(self, message: Message):
        state = message.data["state"]
        if self.current and state == MediaState.LOADED_MEDIA:
            self.current.play()
            self.bus.emit(Message("ovos.common_play.track.state",
                                  {"state": TrackState.PLAYING_VIDEO}))

    def remove_listeners(self):
        self.bus.remove('ovos.video.service.play', self.handle_play)
        self.bus.remove('ovos.video.service.pause', self.pause)
        self.bus.remove('ovos.video.service.resume', self.resume)
        self.bus.remove('ovos.video.service.stop', self.stop)
        self.bus.remove('ovos.video.service.track_info', self.handle_track_info)
        self.bus.remove('ovos.video.service.get_track_position', self.handle_get_track_position)
        self.bus.remove('ovos.video.service.set_track_position', self.handle_set_track_position)
        self.bus.remove('ovos.video.service.get_track_length', self.handle_get_track_length)
        self.bus.remove('ovos.video.service.seek_forward', self.handle_seek_forward)
        self.bus.remove('ovos.video.service.seek_backward', self.handle_seek_backward)

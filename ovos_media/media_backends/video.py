from ovos_plugin_manager.ocp import find_ocp_video_plugins

from ovos_bus_client.message import Message
from ovos_utils.ocp import TrackState, MediaState
from .base import BaseMediaService


class VideoService(BaseMediaService):
    """ Video Service class.
        Handles playback of video and selecting proper backend for the uri
        to be played.
    """

    def __init__(self, bus, config=None, *args, **kwargs):
        """
            Args:
                bus: OVOS messagebus
        """
        super().__init__(bus, "video", find_ocp_video_plugins, config, *args, **kwargs)

    def get_preferred_players(self):
        return self.config.get("preferred_video_services")


from ovos_plugin_manager.ocp import find_ocp_web_plugins

from ovos_bus_client.message import Message
from ovos_utils.ocp import MediaState, TrackState
from .base import BaseMediaService


class WebService(BaseMediaService):
    """ Web Service class.
        Handles playback of web and selecting proper backend for the uri
        to be played.
    """

    def __init__(self, bus, config=None, *args, **kwargs):
        """
            Args:
                bus: OVOS messagebus
        """
        super().__init__(bus, "web", find_ocp_web_plugins, config, *args, **kwargs)

    def get_preferred_players(self):
        return self.config.get("preferred_web_services")


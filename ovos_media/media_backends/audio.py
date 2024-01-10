from ovos_plugin_manager.ocp import find_ocp_audio_plugins

from ovos_bus_client.message import Message
from ovos_media.media_backends.base import BaseMediaService
from ovos_utils.ocp import MediaState, TrackState


class AudioService(BaseMediaService):
    """ Audio Service class.
        Handles playback of audio and selecting proper backend for the uri
        to be played.
    """

    def __init__(self, bus, config=None, *args, **kwargs):
        """
            Args:
                bus: OVOS messagebus
        """
        super().__init__(bus, "audio", find_ocp_audio_plugins, config, *args, **kwargs)

    def get_preferred_players(self):
        return self.config.get("preferred_audio_services")


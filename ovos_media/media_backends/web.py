from ovos_plugin_manager.ocp import find_ocp_web_plugins

from ovos_media.media_backends.base import BaseMediaService


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

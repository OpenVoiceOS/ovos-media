from threading import Thread

from ovos_bus_client import Message, MessageBusClient
from ovos_utils.log import LOG
from ovos_utils.process_utils import ProcessStatus, StatusCallbackMap

from ovos_config.config import Configuration
from ovos_media.player import OCPMediaPlayer
from ovos_media.gui import OCPGUIState


def on_ready():
    LOG.info('Audio service is ready.')


def on_alive():
    LOG.info('Audio service is alive.')


def on_started():
    LOG.info('Audio service started.')


def on_error(e='Unknown'):
    LOG.error(f'Audio service failed to launch ({e}).')


def on_stopping():
    LOG.info('Audio service is shutting down...')


# TODO
class MediaService(Thread):
    def __init__(self, ready_hook=on_ready, error_hook=on_error,
                 stopping_hook=on_stopping, alive_hook=on_alive,
                 started_hook=on_started, watchdog=lambda: None,
                 bus=None, validate_source=True):
        super(MediaService, self).__init__()

        LOG.info("Starting Media Service")
        callbacks = StatusCallbackMap(on_ready=ready_hook, on_error=error_hook,
                                      on_stopping=stopping_hook,
                                      on_alive=alive_hook,
                                      on_started=started_hook)
        self.status = ProcessStatus('audio', callback_map=callbacks)
        self.status.set_started()

        self.config = Configuration().get("media", {})
        self.native_sources = self.config.get("native_sources", ["debug_cli", "audio"]) or []

        self.validate_source = validate_source

        if not bus:
            bus = MessageBusClient()
            bus.run_in_thread()
        self.bus = bus
        self.status.bind(self.bus)
        self.status.set_alive()
        self.init_messagebus()
        self.ocp = OCPMediaPlayer(self.bus)
        self.ocp.add_event('ovos.common_play.home', self.handle_home)
        self.ocp.add_event("ovos.common_play.ping", self.handle_ping)
        self.ocp.add_event("ovos.common_play.search.start", self.handle_search_start)
        self.ocp.add_event("ovos.common_play.search.end", self.handle_search_end)

    def handle_home(self, message):
        self.ocp.gui.manage_display(OCPGUIState.HOME)

    def handle_ping(self, message):
        """
        Handle ovos.common_play.ping Messages and emit a response
        @param message: message associated with request
        """
        self.bus.emit(message.reply("ovos.common_play.pong"))

    def handle_search_start(self, message):
        """when OCP pipeline triggers, show search animation"""
        self.ocp.gui.manage_display(OCPGUIState.SPINNER)

    def handle_search_end(self, message):
        """remove search spinner"""
        self.ocp.gui.remove_search_spinner()

    def run(self):
        self.status.set_ready()

    def shutdown(self):
        """Shutdown the audio service cleanly.

        Stop any playing audio and make sure threads are joined correctly.
        """
        # TODO - update gui for no-media in now_playing page
        self.ocp.reset()
        self.status.set_stopping()
        self.ocp.shutdown()

    def init_messagebus(self):
        """
        Start speech related handlers.
        """
        Configuration.set_config_update_handlers(self.bus)

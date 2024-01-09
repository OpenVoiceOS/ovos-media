from threading import Thread

from ovos_bus_client import Message, MessageBusClient
from ovos_utils.log import LOG
from ovos_utils.process_utils import ProcessStatus, StatusCallbackMap

from ovos_config.config import Configuration
from ovos_media.player import OCPMediaPlayer


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
        self.init_messagebus()

        self.ocp = OCPMediaPlayer(self.bus)

    def run(self):
        self.status.set_alive()
        # if self.ocp.wait_for_load():
        #    if len(self.ocp.service) == 0:
        #        LOG.warning('No audio backends loaded! '
        #                    'Audio playback is not available')
        #        LOG.info("Running audio service in TTS only mode")
        # If at least TTS exists, report ready
        self.status.set_ready()

    def handle_stop(self, message: Message):
        """Handle stop message.

        Shutdown any speech.
        """

    def shutdown(self):
        """Shutdown the audio service cleanly.

        Stop any playing audio and make sure threads are joined correctly.
        """
        self.status.set_stopping()
        self.ocp.shutdown()

    def init_messagebus(self):
        """
        Start speech related handlers.
        """
        Configuration.set_config_update_handlers(self.bus)
        self.bus.on('mycroft.stop', self.handle_stop)

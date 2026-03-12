from threading import Thread

from ovos_bus_client import Message, MessageBusClient
from ovos_utils.log import LOG
from ovos_utils.process_utils import ProcessStatus, StatusCallbackMap

from ovos_config.config import Configuration
from ovos_media.legacy_api import LegacyAudioServiceCompat
from ovos_media.player import OCPMediaPlayer

def on_ready():
    LOG.info('Media service is ready.')


def on_alive():
    LOG.info('Media service is alive.')


def on_started():
    LOG.info('Media service started.')


def on_error(e='Unknown'):
    LOG.error(f'Media service failed to launch ({e}).')


def on_stopping():
    LOG.info('Media service is shutting down...')


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
        self.bus.on('ovos.common_play.home', self.handle_home)
        self.bus.on("ovos.common_play.ping", self.handle_ping)
        self.bus.on("ovos.common_play.search.start", self.handle_search_start)
        self.bus.on("ovos.common_play.search.end", self.handle_search_end)
        self.legacy_compat = LegacyAudioServiceCompat(self.ocp, self.bus)

    def handle_home(self, message):
        self.ocp._update_gui()

    def handle_ping(self, message):
        """
        Handle ovos.common_play.ping Messages and emit a response
        @param message: message associated with request
        """
        self.bus.emit(message.reply("ovos.common_play.pong"))

    def handle_search_start(self, message):
        """when OCP pipeline triggers, show search animation"""
        self.ocp.gui.show_media_player(
            now_playing=None,
            playlist=[],
            search_results=[],
            state="loading",
        )

    def handle_search_end(self, message: "Message") -> None:
        """Dismiss the search spinner and refresh the player GUI."""
        self.ocp._update_gui()

    def run(self):
        self.status.set_ready()

    def handle_opm_audio_query(self, message: Message) -> None:
        """Handle ``opm.audio.query`` — report installed audio backends.

        Returns the same structure as the old ``PlaybackService`` handler so
        that OPM discovery continues to work after migration to ovos-media.
        """
        backends = self.ocp.audio_service.available_backends() if self.ocp else {}
        data = {
            "plugins": list(backends.keys()),
            "configs": backends,
            "options": {},
        }
        self.bus.emit(message.response(data))

    def shutdown(self):
        """Shutdown the audio service cleanly.

        Stop any playing audio and make sure threads are joined correctly.
        """
        # TODO - update gui for no-media in now_playing page
        self.ocp.reset()
        self.status.set_stopping()
        self.legacy_compat.shutdown()
        self.ocp.shutdown()

    def init_messagebus(self):
        """
        Start speech related handlers.
        """
        Configuration.set_config_update_handlers(self.bus)
        self.bus.on("opm.audio.query", self.handle_opm_audio_query)

from threading import Thread

from ovos_bus_client import Message, MessageBusClient
from ovos_utils.log import LOG
from ovos_utils.process_utils import ProcessStatus, StatusCallbackMap

from ovos_config.config import Configuration
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
                 bus=None, validate_source=None):
        super(MediaService, self).__init__()

        LOG.info("Starting Media Service")
        callbacks = StatusCallbackMap(on_ready=ready_hook, on_error=error_hook,
                                      on_stopping=stopping_hook,
                                      on_alive=alive_hook,
                                      on_started=started_hook)
        self.status = ProcessStatus('audio', callback_map=callbacks)
        self.status.set_started()

        self.config = Configuration().get("media", {})
        # This attribute was dead — it read media.native_sources but was
        # never passed anywhere; the real consumer, validate_message_context()
        # in utils.py, only ever read the top-level `native_sources` key.
        # validate_message_context() now also honours the documented
        # media-scoped key directly, so this unused shadow copy is removed
        # rather than wired through a second time.

        # Only act on playback commands from the local/"default" session.
        # In a HiveMind split the OCP pipeline (on the server) forwards
        # commands stamped with the originating satellite session; a
        # server-side ovos-media must ignore those (the satellite runs its
        # own embedded ovos-media). The constructor argument wins; otherwise
        # read `media.validate_source` from config (default True). A satellite
        # that is not getting default-NAT'd sessions sets this False to act on
        # all sessions.
        if validate_source is None:
            validate_source = self.config.get("validate_source", True)
        self.validate_source = validate_source

        if not bus:
            bus = MessageBusClient()
            bus.run_in_thread()
        self.bus = bus
        self.status.bind(self.bus)
        self.status.set_alive()
        self.init_messagebus()
        self.ocp = OCPMediaPlayer(self.bus, validate_source=self.validate_source)
        self.bus.on('ovos.common_play.home', self.handle_home)
        self.bus.on("ovos.common_play.ping", self.handle_ping)
        # 'ovos.common_play.search.start' is handled by
        # OCPMediaPlayer.handle_search_start (player.py) — that registration
        # duplicated it here, unconditionally (no session gating), so every
        # search.start double-pushed the "loading" GUI state.
        self.bus.on("ovos.common_play.search.end", self.handle_search_end)
        # opm.audio.query's handler reads self.ocp.audio_service — it
        # must not be reachable until self.ocp exists. Registered here
        # (after OCPMediaPlayer's plugin-loading construction above), not in
        # init_messagebus() which runs before self.ocp is assigned.
        self.bus.on("opm.audio.query", self.handle_opm_audio_query)

    def handle_home(self, message):
        self.ocp._update_gui()

    def handle_ping(self, message):
        """
        Handle ovos.common_play.ping Messages and emit a response
        @param message: message associated with request
        """
        self.bus.emit(message.reply("ovos.common_play.pong"))

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
        self.ocp.shutdown()
        # Remove the four handlers registered directly on MediaService
        # (not on self.ocp) so a shut-down service stops answering
        # home/ping/search.end/opm.audio.query.
        self.bus.remove('ovos.common_play.home', self.handle_home)
        self.bus.remove("ovos.common_play.ping", self.handle_ping)
        self.bus.remove("ovos.common_play.search.end", self.handle_search_end)
        self.bus.remove("opm.audio.query", self.handle_opm_audio_query)

    def init_messagebus(self):
        """
        Start speech related handlers.
        """
        Configuration.set_config_update_handlers(self.bus)

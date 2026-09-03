"""The daemon: what gets built, in what order, and what gets torn down."""
from threading import Thread

from ovos_bus_client import MessageBusClient
from ovos_config.config import Configuration
from ovos_utils.log import LOG
from ovos_utils.ocp import OCP_ID
from ovos_utils.process_utils import ProcessStatus, StatusCallbackMap

from ovos_media.bus.api import OCPBusApi
from ovos_media.catalog import LikedSongsStore
from ovos_media.player import OCPMediaPlayer
from ovos_media.skill import OCPVoiceSkill


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
        self.status = ProcessStatus('media', callback_map=callbacks)
        self.status.set_started()

        self.config = Configuration().get("media", {})

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
        Configuration.set_config_update_handlers(self.bus)
        # one liked-songs store, shared by the player (which writes likes
        # and play counts) and the voice skill (which searches it)
        self.likes = LikedSongsStore()
        self.ocp = OCPMediaPlayer(self.bus, validate_source=self.validate_source,
                                  likes=self.likes)
        # the voice front-end: the only thing in this daemon that speaks.
        # It shares the player's liked-songs store and listens on its
        # catalog for the dialogs playback asks to have announced. The
        # skill_id is what the OCP pipeline sees on its search results and
        # keyword registrations, so it stays the one the catalog always
        # used.
        self.voice_skill = OCPVoiceSkill(bus=self.bus,
                                         skill_id=OCP_ID + ".favorites",
                                         likes=self.likes,
                                         catalog=self.ocp.media,
                                         validate_source=self.validate_source)
        # last: the daemon's own topics read the player built above
        self.bus_api = OCPBusApi(self.bus, service=self)

    def run(self):
        self.status.set_ready()

    def shutdown(self):
        """Shutdown the audio service cleanly.

        Stop any playing audio and make sure threads are joined correctly.
        """
        self.ocp.reset()
        self.status.set_stopping()
        self.ocp.shutdown()
        # default_shutdown() is the real OVOSSkill teardown (plain
        # shutdown() is the no-op user hook): without it a shut-down
        # service keeps answering the media intents.
        self.voice_skill.default_shutdown()
        # the service's own topics go last: a shut-down service must stop
        # answering ping/opm.audio.query too.
        self.bus_api.shutdown()

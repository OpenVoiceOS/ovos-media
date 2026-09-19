"""MPRIS integration, in two independent halves.

Role A, :mod:`~ovos_media.mpris.exporter`, publishes ovos-media itself as
``org.mpris.MediaPlayer2.OCP``. It is what makes the virtual player show up in
KDE Connect and playerctl, and it is always on when ``enable_mpris`` is set.

Role B, :mod:`~ovos_media.mpris.manager`, is the opposite direction: awareness
of the other MPRIS players on the machine, gated behind
``manage_external_players``. It moves to ``ovos-media-plugin-mpris`` in a future
release, and the boundary in this package is where that cut goes.

Both run on the one thread :mod:`~ovos_media.mpris.loop` owns.

:class:`OcpMprisExporter` wires the three together and is the only thing the
player holds.
"""
from ovos_utils.log import LOG

from ovos_media.mpris.loop import DbusLoop, patch_dbus_next
from ovos_media.mpris.exporter import (MprisExporter, submit_to_player,
                                       _MediaPlayer2Interface,
                                       _MediaPlayer2PlayerInterface,
                                       _MediaPlayer2PlaylistsInterface)
from ovos_media.mpris.manager import ExternalPlayerManager


class OcpMprisExporter:
    """MPRIS, as the player sees it.

    Owns the D-Bus thread, the exported OCP player and — when
    ``manage_external_players`` is on — the external-player manager. The player
    only ever asks it for a transport verb, a property refresh or a shutdown.
    """

    def __init__(self, player, config=None, daemonic=True, manage_players=False):
        self.config = config or {}
        self._ocp_player = player

        self.loop = DbusLoop(config=self.config, daemonic=daemonic)
        self.exporter = MprisExporter(player)
        self.manager = ExternalPlayerManager(player, self.loop,
                                             config=self.config,
                                             manage_players=manage_players)

        self.loop.on_connect = self.exporter.export
        self.loop.tick = self.manager.tick

        self.loop.start()

    @property
    def manage_players(self) -> bool:
        return self.manager.manage_players

    @property
    def stop_event(self):
        return self.manager.stop_event

    def update_props(self, props):
        self.exporter.update_props(props)

    # transport verbs — each crosses to the D-Bus thread as a posted coroutine
    def play_prev(self):
        self.loop.call_async(self.manager.do_play_prev())

    def play_next(self):
        self.loop.call_async(self.manager.do_play_next())

    def resume(self):
        self.loop.call_async(self.manager.do_resume())

    def pause(self):
        self.loop.call_async(self.manager.do_pause_all())

    def stop(self):
        # the flag is raised here rather than on the loop thread: the player
        # reads it to decide whether a stop is already outstanding, and that
        # question must be answered the moment stop() returns
        self.manager.stop_event.set()
        return self.loop.call_async(self.manager.do_stop_all())

    def toggle_shuffle(self):
        self.loop.call_async(self.manager.do_toggle_shuffle())

    def toggle_repeat(self):
        self.loop.call_async(self.manager.do_toggle_repeat())

    def shutdown(self) -> None:
        """Stop the MPRIS event loop and release resources."""
        # let the stop actually reach the external players before the loop it
        # runs on goes away; an unresponsive player must not hold shutdown up
        future = self.stop()
        if future is not None:
            try:
                future.result(timeout=2)
            except Exception as e:
                LOG.debug(f"MPRIS stop did not complete before shutdown: {e}")
        self.loop.shutdown()


# Backward-compatibility alias — remove after ovos-media-plugin-mpris is released
MprisPlayerCtl = OcpMprisExporter

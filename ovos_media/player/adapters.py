"""Player adapters — one uniform interface over everything that can play media.

``ovos-media`` is the abstract OVOS media player: a single virtual player that
tracks whatever is actually playing, wherever it plays. The concrete players are
not uniform — an OPM media plugin is driven through :class:`BaseMediaService`,
while a OCP skill is driven by bus messages it subscribes to itself — so the
player used to branch on :class:`PlaybackType` in every transport verb.

A :class:`PlayerAdapter` wraps one concrete player behind the verbs the virtual
player needs (play/pause/resume/stop/seek, position/length, duck/restore).
Adapters are an internal detail: the OPM
``MediaBackend`` template stays the plugin contract, and
:class:`OPMBackendAdapter` speaks to it through the service layer exactly as
before.
"""
import abc
from typing import Optional

from ovos_bus_client.message import Message
from ovos_plugin_manager.templates.media import MediaBackend
from ovos_utils.log import LOG
from ovos_utils.ocp import TrackState


class PlayerAdapter(metaclass=abc.ABCMeta):
    """One concrete player, behind the verbs the virtual player speaks."""

    #: True for a player ovos-media only observes. External players join the
    #: roster so the virtual player knows about them, but they are never told
    #: to give way — an MPRIS takeover exists to yield to one of them.
    external: bool = False

    def __init__(self, player_id: str):
        self._id = player_id

    @property
    def id(self) -> str:
        return self._id

    def __repr__(self):
        return f"{self.__class__.__name__}({self._id})"

    @abc.abstractmethod
    def can_play(self, uri: str) -> bool:
        """Whether this player would claim *uri*."""

    @abc.abstractmethod
    def play(self, uri: str) -> None:
        """Start playing *uri*."""

    @abc.abstractmethod
    def pause(self) -> None:
        ...

    @abc.abstractmethod
    def resume(self) -> None:
        ...

    @abc.abstractmethod
    def stop(self) -> None:
        ...

    @abc.abstractmethod
    def seek(self, milliseconds: int) -> None:
        ...

    @abc.abstractmethod
    def position(self) -> Optional[int]:
        """Current playback position in milliseconds, or None."""

    @abc.abstractmethod
    def length(self) -> Optional[int]:
        """Current track duration in milliseconds, or None."""

    def lower_volume(self) -> None:
        """Duck. Players that cannot duck ignore this."""

    def restore_volume(self) -> None:
        """Unduck. Players that cannot duck ignore this."""

    def deactivate(self) -> None:
        """Give up whatever this player still holds.

        Called when the virtual player switches to a different concrete
        player: whatever this one is still playing must not keep running
        alongside the new track.
        """


class OPMBackendAdapter(PlayerAdapter):
    """Adapter over one OPM media-plugin family (audio, video or web).

    Wraps the :class:`~ovos_media.media_backends.base.BaseMediaService`
    instance that owns that family. Backend selection, the supported_uris
    isolation and the stop guard window all stay in the service layer; this
    adapter only resolves the configured preference before each request.

    The service is looked up on the owning player by attribute name on every
    call, so the adapter tracks whichever service instance the player holds.
    """

    def __init__(self, player_id: str, player, service_attr: str):
        super().__init__(player_id)
        self.player = player
        self.service_attr = service_attr

    @property
    def service(self):
        return getattr(self.player, self.service_attr)

    @property
    def namespace(self) -> str:
        return self.service.namespace

    def _preferred(self) -> Optional[MediaBackend]:
        return self.player._resolve_preferred_service(self.service)

    def can_play(self, uri: str) -> bool:
        return self.service.can_play(uri, preferred_service=self._preferred())

    def play(self, uri: str) -> None:
        self.service.play(uri, preferred_service=self._preferred())

    def pause(self) -> None:
        self.service.pause()

    def resume(self) -> None:
        self.service.resume()

    def stop(self) -> None:
        self.service.stop()

    def seek(self, milliseconds: int) -> None:
        self.service.set_track_position(milliseconds)

    def position(self) -> Optional[int]:
        return self.service.get_track_position()

    def length(self) -> Optional[int]:
        return self.service.get_track_length()

    def lower_volume(self) -> None:
        self.service.lower_volume()

    def restore_volume(self) -> None:
        self.service.restore_volume()

    def deactivate(self) -> None:
        # a leftover `current` is enough for a later, unrelated LOADED_MEDIA
        # event to revive this backend and leave two backends playing at once
        if self.service.current is None:
            return
        try:
            self.service.current.stop()
        except Exception as e:
            LOG.exception(f"Failed to stop inactive {self.namespace} backend: {e}")
        self.service.current = None


class SkillPlayerAdapter(PlayerAdapter):
    """Adapter over an OCP skill that handles its own playback.

    The skill is driven entirely by ``ovos.common_play.{skill_id}.*`` messages
    it subscribes to, and reports nothing back except through the shared OCP
    state events, so position and length come from ``now_playing``.
    """

    def __init__(self, player, player_id: str = "skill"):
        super().__init__(player_id)
        self.player = player

    @property
    def bus(self):
        return self.player.bus

    @property
    def now_playing(self):
        return self.player.now_playing

    @property
    def skill_id(self) -> Optional[str]:
        return self.now_playing.skill_id

    def _emit(self, verb: str, data: dict = None) -> None:
        self.bus.emit(Message(f"ovos.common_play.{self.skill_id}.{verb}",
                              data or {}))

    def can_play(self, uri: str) -> bool:
        # the skill offered this track in the first place
        return True

    def play(self, uri: str = None) -> None:
        self._emit("play", self.now_playing.infocard)
        self.bus.emit(Message("ovos.common_play.track.state",
                              {"state": TrackState.PLAYING_SKILL}))

    def pause(self) -> None:
        self._emit("pause")

    def resume(self) -> None:
        self._emit("resume")

    def stop(self) -> None:
        self._emit("stop")

    def next(self) -> None:
        self._emit("next")

    def prev(self) -> None:
        self._emit("previous")

    def seek(self, milliseconds: int) -> None:
        LOG.warning("seek is not supported for skill playback, ignoring")

    def position(self) -> Optional[int]:
        return self.now_playing.position

    def length(self) -> Optional[int]:
        return self.now_playing.length


class MprisPlayerAdapter(PlayerAdapter):
    """One external MPRIS player, as a member of the roster.

    Registered by :class:`~ovos_media.mpris.manager.ExternalPlayerManager` as
    external players appear on the session bus, so the roster knows about the
    players ovos-media observes as well as the ones it drives. It is a
    presence, not a remote control: nothing routes a transport verb here.
    External players are driven by the manager's own coroutines on the D-Bus
    thread, and the verbs below stay inert until a routing table row sends
    something their way.
    """

    external = True

    def __init__(self, manager, bus_name: str):
        super().__init__(f"mpris:{bus_name}")
        self.manager = manager
        self.bus_name = bus_name

    def can_play(self, uri: str) -> bool:
        # ovos-media never hands a track to an external player; it only
        # reflects what that player chose to play on its own
        return False

    def _ignore(self, verb: str) -> None:
        LOG.debug(f"{verb} is not routed to external MPRIS players, ignoring")

    def play(self, uri: str = None) -> None:
        self._ignore("play")

    def pause(self) -> None:
        self._ignore("pause")

    def resume(self) -> None:
        self._ignore("resume")

    def stop(self) -> None:
        self._ignore("stop")

    def seek(self, milliseconds: int) -> None:
        self._ignore("seek")

    def position(self):
        return self.manager.player_meta.get(self.bus_name, {}).get("position")

    def length(self):
        return self.manager.player_meta.get(self.bus_name, {}).get("length")

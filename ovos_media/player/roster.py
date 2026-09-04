"""The roster of concrete players known to the virtual player.

:class:`PlayerRoster` holds every :class:`~ovos_media.player.adapters.PlayerAdapter`
and answers the only two questions the virtual player has about them: which one
plays the current track, and which ones a given transport verb reaches.

The second question is not the same as the first. ``PlaybackType.UNDEFINED``
means "nothing is loaded, so make sure nothing is playing anywhere", and a stop
or a pause on it fans out to every player that could be holding audio. The
routing table below records that, one row per verb, and is the single place the
old per-verb ``if playback_type in [...]`` ladders used to live.
"""
from typing import Dict, List, Tuple

from ovos_utils.log import LOG
from ovos_utils.ocp import PlaybackType

AUDIO = "opm:audio"
VIDEO = "opm:video"
WEB = "opm:web"
SKILL = "skill"

# which concrete players each verb reaches, per playback type, in call order.
# A playback type absent from a row means the verb reaches nobody (and, for
# seek, is reported as unsupported).
_ROUTES: Dict[str, Dict[PlaybackType, Tuple[str, ...]]] = {
    "pause": {
        PlaybackType.AUDIO: (AUDIO,),
        PlaybackType.VIDEO: (VIDEO,),
        PlaybackType.SKILL: (SKILL,),
        PlaybackType.UNDEFINED: (AUDIO, VIDEO, SKILL),
    },
    "resume": {
        PlaybackType.AUDIO: (AUDIO,),
        PlaybackType.VIDEO: (VIDEO,),
        PlaybackType.SKILL: (SKILL,),
        PlaybackType.UNDEFINED: (AUDIO, SKILL),
    },
    "stop": {
        PlaybackType.AUDIO: (AUDIO,),
        PlaybackType.VIDEO: (VIDEO,),
        PlaybackType.WEBVIEW: (WEB,),
        PlaybackType.SKILL: (SKILL,),
        PlaybackType.UNDEFINED: (AUDIO, SKILL, VIDEO, WEB),
    },
    "seek": {
        PlaybackType.AUDIO: (AUDIO,),
        PlaybackType.VIDEO: (VIDEO,),
        PlaybackType.UNDEFINED: (AUDIO,),
    },
    # ducking only applies where there is a volume to lower
    "volume": {
        PlaybackType.AUDIO: (AUDIO,),
        PlaybackType.VIDEO: (VIDEO,),
    },
    # the seekbar/track-info queries read the backend only where the backend
    # reports a meaningful position; everywhere else now_playing is the source
    "position": {
        PlaybackType.AUDIO: (AUDIO,),
    },
    "position_offset": {
        PlaybackType.AUDIO: (AUDIO,),
        PlaybackType.UNDEFINED: (AUDIO,),
    },
}

# which player starts a track of each playback type
_PLAYBACK_OWNER: Dict[PlaybackType, str] = {
    PlaybackType.AUDIO: AUDIO,
    PlaybackType.VIDEO: VIDEO,
    PlaybackType.WEBVIEW: WEB,
    PlaybackType.SKILL: SKILL,
}


class PlayerRoster:
    """Every concrete player the virtual player can drive."""

    def __init__(self, adapters):
        self.adapters = list(adapters)
        self._by_id = {a.id: a for a in self.adapters}

    def get(self, player_id: str):
        return self._by_id.get(player_id)

    def register(self, adapter) -> None:
        """Add a player that appeared while the daemon was running.

        External MPRIS players come and go; the ones ovos-media owns are all
        there from the start.
        """
        if adapter.id in self._by_id:
            return
        self.adapters.append(adapter)
        self._by_id[adapter.id] = adapter

    def unregister(self, player_id: str) -> None:
        """Forget a player that went away."""
        adapter = self._by_id.pop(player_id, None)
        if adapter is not None:
            self.adapters.remove(adapter)

    @property
    def owned(self) -> List[object]:
        """Every player ovos-media drives itself, external ones excluded."""
        return [a for a in self.adapters if not getattr(a, "external", False)]

    def owner(self, playback_type: PlaybackType):
        """The player that starts tracks of *playback_type*, or None."""
        return self.get(_PLAYBACK_OWNER.get(playback_type, ""))

    def route(self, verb: str, playback_type: PlaybackType,
              can_seek: bool = True) -> List[object]:
        """The players *verb* reaches for *playback_type*, in call order.

        ``can_seek`` only matters for ``verb == "seek"`` against
        ``PlaybackType.SKILL``: the routing table has no static row for it
        because whether a skill's rendering is reachable depends on its
        OCP-1 §4.3.1 announcement, not on the playback type alone. Callers
        that already know the current skill did not declare ``can_seek``
        pass ``False`` so the verb is reported as unsupported, matching
        every other unroutable (verb, playback_type) pair.
        """
        if verb == "seek" and playback_type == PlaybackType.SKILL:
            ids = (SKILL,) if can_seek else ()
        else:
            ids = _ROUTES.get(verb, {}).get(playback_type, ())
        return [self._by_id[i] for i in ids if i in self._by_id]

    def select(self, playback_type: PlaybackType, uri: str):
        """Pick the player that will start the track about to play.

        A video/web track no installed backend claims is demoted to audio
        rather than dead-ending in ``MediaState.INVALID_MEDIA`` — a headless
        install with only audio backends still plays the soundtrack.

        Returns:
            (adapter, playback_type): the selected player and the playback
            type it will play the track as, which differs from the requested
            one when the track was demoted to audio. The adapter is None when
            no player can play the track at all.

        Raises:
            ValueError: on a playback type no player owns.
        """
        adapter = self.owner(playback_type)
        if adapter is None:
            raise ValueError("invalid playback request")

        if playback_type in (PlaybackType.VIDEO, PlaybackType.WEBVIEW) and \
                not adapter.can_play(uri):
            LOG.warning(f"No {adapter.id} backend can play {uri!r}; "
                        f"falling back to audio")
            # deactivate_others() ran while this was still the intended
            # player, so it left this one alone. Now that we abandon it, it
            # must give up its track or it keeps playing under the new one.
            adapter.deactivate()
            playback_type = PlaybackType.AUDIO
            adapter = self.get(AUDIO)
            if adapter is None or not adapter.can_play(uri):
                return None, playback_type

        return adapter, playback_type

    def deactivate_others(self, playback_type: PlaybackType) -> None:
        """Make every player that does not own *playback_type* give up its track."""
        for ptype, player_id in _PLAYBACK_OWNER.items():
            if ptype == playback_type:
                continue
            adapter = self.get(player_id)
            if adapter is not None:
                adapter.deactivate()

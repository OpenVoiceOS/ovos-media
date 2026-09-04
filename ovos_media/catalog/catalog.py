"""The media catalog: what can be played and what was found.

Holds the roster of OCP skills that announced themselves, the featured
media they advertise, the liked-songs store and the search results the
player merges into its queue. It is a plain object with no bus
subscriptions of its own — :mod:`ovos_media.bus.api` binds
``ovos.common_play.announce`` and ``ovos.common_play.skills.detach`` to
the handlers below.

It is also the seam through which the player asks for a dialog. The
player never speaks: it notifies the catalog, and whatever voice
front-end registered a listener (see :class:`ovos_media.skill.
OCPVoiceSkill`) decides what to say. Without a listener the notification
is dropped, which is what a headless player with no skill attached wants.
"""
import threading
from typing import Callable, List, Optional

from ovos_bus_client.message import Message
from ovos_utils.log import LOG
from ovos_utils.ocp import MediaType, Playlist

from ovos_media.bus.schemas import flatten_media_types
from ovos_media.catalog.likes import LikedSongsStore


class MediaCatalog:

    def __init__(self, bus, likes: LikedSongsStore) -> None:
        self.bus = bus
        # injected, never invented: one store per process, shared with the
        # voice front-end that searches it (see ovos_media.service)
        self.likes = likes
        self.search_playlist = Playlist()
        self.ocp_skills = {}
        self.featured_skills = {}
        # OCP-1 §4.3.1: a rendering skill that can reposition its own
        # playback declares `can_seek: true` in its announcement. Absent
        # (or non-boolean) that declaration the skill's rendering is
        # non-seekable, so this only ever holds skills that opted in.
        self._can_seek = {}
        self._dialog_listeners: List[Callable[[str, Optional[dict]], None]] = []
        self._likes_listeners: List[Callable[[], None]] = []

    def add_dialog_listener(self, listener: Callable) -> None:
        """Register a voice front-end to speak notified dialogs."""
        if listener not in self._dialog_listeners:
            self._dialog_listeners.append(listener)

    def remove_dialog_listener(self, listener: Callable) -> None:
        if listener in self._dialog_listeners:
            self._dialog_listeners.remove(listener)

    def add_likes_listener(self, listener: Callable) -> None:
        """Register a callback to run whenever the liked-songs store
        changes, so a voice front-end can refresh its keyword matcher."""
        if listener not in self._likes_listeners:
            self._likes_listeners.append(listener)

    def remove_likes_listener(self, listener: Callable) -> None:
        if listener in self._likes_listeners:
            self._likes_listeners.remove(listener)

    def notify_likes_changed(self) -> None:
        """Tell any listener the liked-songs store changed, e.g. after a
        like/unlike, so keyword registration for song titles can be
        refreshed without waiting for a restart."""
        for listener in list(self._likes_listeners):
            try:
                listener()
            except Exception as e:
                LOG.exception(f"Failed to notify likes-changed listener: {e}")

    def notify_dialog(self, dialog: str, data: Optional[dict] = None) -> None:
        """Ask the voice front-end to speak a dialog.

        A listener that raises must not take the caller down with it — the
        callers are playback paths where a failed announcement is never a
        reason to abort playback.
        """
        for listener in list(self._dialog_listeners):
            try:
                listener(dialog, data)
            except Exception as e:
                LOG.exception(f"Failed to speak {dialog} dialog: {e}")

    def handle_skill_announce(self, message: Message) -> None:
        skill_id = message.data.get("skill_id")
        skill_name = message.data.get("skill_name") or skill_id
        img = message.data.get("image") or message.data.get("thumbnail")
        has_featured = bool(message.data.get("featured_tracks"))
        media_types = message.data.get("media_types") or \
                      message.data.get("media_type") or \
                      [MediaType.GENERIC]
        media_types = flatten_media_types(media_types)

        if skill_id not in self.ocp_skills:
            LOG.debug(f"Registered {skill_id}")
            self.ocp_skills[skill_id] = []

        # only a literal boolean True opts in (OCP-1 §4.3.1); anything else,
        # including truthy-but-not-boolean values like "yes" or 1, keeps the
        # skill non-seekable
        self._can_seek[skill_id] = message.data.get("can_seek") is True

        if has_featured:
            LOG.debug(f"Found skill with featured media: {skill_id}")
            self.featured_skills[skill_id] = {
                "skill_id": skill_id,
                "skill_name": skill_name,
                "image": img,
                "media_types": media_types
            }

    def handle_ocp_skill_detach(self, message: Message) -> None:
        skill_id = message.data["skill_id"]
        if skill_id in self.ocp_skills:
            self.ocp_skills.pop(skill_id)
        if skill_id in self.featured_skills:
            self.featured_skills.pop(skill_id)
        self._can_seek.pop(skill_id, None)

    def can_seek(self, skill_id: Optional[str]) -> bool:
        """Whether *skill_id* declared ``can_seek: true`` on announce
        (OCP-1 §4.3.1). False for an unknown or undeclared skill.

        The catalog has no reference back to the player or its MPRIS
        exporter, so a (re-)announce that changes this value for the
        currently-playing skill does not push an MPRIS CanSeek
        PropertiesChanged signal on its own; the property is still correct
        on the next live read, and every play/state transition that already
        signals CanSeek (see OCPMediaPlayer.set_now_playing/
        set_player_state/play) picks up the new value then.
        """
        return self._can_seek.get(skill_id, False)

    def get_featured_skills(self, adult: bool = False) -> list:
        """Emit a skills-get broadcast and return the registered featured skills.

        The 200 ms wait allows in-process skill announcements to arrive before
        the list is read.  A threading.Event is used instead of time.sleep so
        the call can be interrupted by a shutdown signal in future work.
        """
        self.bus.emit(Message("ovos.common_play.skills.get"))
        threading.Event().wait(timeout=0.2)  # non-blocking sleep equivalent
        skills = list(self.featured_skills.values())
        if adult:
            return skills
        return [s for s in skills
                if MediaType.ADULT not in s["media_types"] and
                MediaType.HENTAI not in s["media_types"]]

    def clear(self) -> None:
        self.search_playlist.clear()

    def replace(self, playlist) -> None:
        self.search_playlist.replace(playlist)

    def shutdown(self) -> None:
        self._dialog_listeners.clear()
        self._likes_listeners.clear()

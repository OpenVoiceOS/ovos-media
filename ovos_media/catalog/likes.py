"""The persisted liked-songs store.

A thin wrapper around the ``OCP_liked_songs`` JSON store that owns the lock
guarding it. Every mutation and every read that iterates the dict goes
through this object: ``store()`` does a ``json.dump`` that iterates while
the like/unlike/play-count writers mutate from separate bus-dispatch
threads, so without a single shared lock a ``store()`` racing a ``pop()``
raises "dictionary changed size during iteration".
"""
from threading import RLock
from typing import List, Optional

from json_database import JsonStorageXDG
from ovos_config.meta import get_xdg_base
from ovos_utils.log import LOG
from ovos_utils.ocp import MediaEntry, MediaType, PlaybackType


class LikedSongsStore:
    """Liked songs, keyed by uri, persisted as JSON."""

    def __init__(self, store=None) -> None:
        self._store = store if store is not None else \
            JsonStorageXDG("OCP_liked_songs", subfolder=get_xdg_base())
        self._lock = RLock()
        LOG.debug(f"Liked songs playlist loaded: {self.path}")

    @property
    def path(self) -> str:
        return getattr(self._store, "path", "")

    def __contains__(self, uri: str) -> bool:
        with self._lock:
            return uri in self._store

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    def items(self) -> list:
        with self._lock:
            return list(self._store.items())

    def like(self, uri: str, title: str = "", artist: str = "",
             image: str = "") -> None:
        with self._lock:
            self._store[uri] = {"title": title, "artist": artist,
                                "image": image, "uri": uri}
            self._store.store()
        LOG.info(f"liked song: {uri}")

    def unlike(self, uri: str) -> bool:
        """Drop a song from the store. Returns whether it was there."""
        with self._lock:
            if uri not in self._store:
                return False
            self._store.pop(uri)
            self._store.store()
        LOG.info(f"unliked song: {uri}")
        return True

    def increment_play_count(self, uri: Optional[str]) -> bool:
        """Bump the play count of a liked song. Returns whether it was one."""
        with self._lock:
            entry = self._store.get(uri)
            if entry is None:
                return False
            entry["play_count"] = entry.get("play_count", 0) + 1
            self._store.store()
        return True

    def titles(self) -> List[str]:
        """The titles of every well-formed entry.

        The store is persisted JSON, editable outside this process (GUI,
        manual edits, older or newer schema versions). A single malformed
        entry — a non-dict value, or a dict without a title — is skipped
        with a warning rather than raised, which used to kill daemon
        startup.
        """
        titles = []
        for uri, song in self.items():
            if not isinstance(song, dict):
                LOG.warning(f"Skipping malformed liked song entry {uri!r}: "
                            f"expected a dict, got {type(song).__name__}")
                continue
            title = song.get("title", "")
            if not title:
                LOG.warning(f"Skipping liked song entry {uri!r}: "
                            f"missing/empty title")
                continue
            titles.append(title)
        return titles

    def as_entries(self) -> List[MediaEntry]:
        """The store as canonical MediaEntry objects, most-played first.

        ``match_confidence`` tracks the play count so the entries sort
        most-played-first once handed to a Playlist.
        """
        entries = [MediaEntry(uri=uri,
                              title=song.get("title", ""),
                              artist=song.get("artist", ""),
                              image=song.get("image", ""),
                              media_type=MediaType.MUSIC,
                              playback=PlaybackType.AUDIO,
                              match_confidence=song.get("play_count", 0) + 50)
                   for uri, song in self.items()]
        return sorted(entries, key=lambda e: e.match_confidence, reverse=True)

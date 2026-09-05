"""The persisted play-history store.

Same idiom as :mod:`ovos_media.catalog.likes`: a thin wrapper around a
JSON store that owns the lock guarding it, so a ``store()`` racing a
reader never raises "dictionary changed size during iteration". One
record per uri: the last-seen entry dict, a ``play_count`` and a
``last_played`` unix timestamp, bounded to a configurable number of
uris so an always-on daemon does not grow the file without limit.
"""
import time
from threading import RLock
from typing import List, Optional

from json_database import JsonStorageXDG
from ovos_config.meta import get_xdg_base
from ovos_utils.log import LOG
from ovos_utils.ocp import MediaEntry, MediaType, PlaybackType

DEFAULT_MAX_ENTRIES = 500
# also the exemption window for eviction: see _evict_one
DEFAULT_RECENT_LIMIT = 50


class PlayHistoryStore:
    """Play history, keyed by uri, persisted as JSON.

    Bounded to ``max_entries`` uris. Once full, ``record_play`` evicts a
    victim to make room for the new/updated uri: the ``DEFAULT_RECENT_LIMIT``
    most-recently-played entries are exempt (evicting them would starve
    ``recent()``, which is exactly the window it reads), and among the
    rest the lowest play_count is evicted, oldest last_played breaking
    ties.
    """

    def __init__(self, store=None,
                 max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self._store = store if store is not None else \
            JsonStorageXDG("OCP_play_history", subfolder=get_xdg_base())
        self._lock = RLock()
        self.max_entries = max_entries
        LOG.debug(f"Play history loaded: {self.path}")

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

    def record_play(self, entry: dict) -> None:
        """Upsert a play: bump the count, refresh last_played and the
        stored entry dict.

        If the store is at capacity and the uri is new, a victim is
        evicted first (see class docstring). A malformed existing row
        (the store is persisted JSON, editable outside this process) is
        treated as absent rather than raised, so one bad row on disk
        cannot take playback down with it.

        ``self._store.store()`` below is a synchronous ``json.dump`` of
        the whole store on the player thread, on every play() - at the
        500-entry bound this is a real but small (single-digit-to-low-
        double-digit ms) stall, the same trade-off ``likes.py`` already
        makes for the play-count bump. A dirty-flag with a periodic/
        shutdown flush would remove it at the cost of losing the last few
        plays on a crash; not worth that trade for a once-per-track write.
        """
        uri = entry.get("uri") if isinstance(entry, dict) else None
        if not uri:
            LOG.warning("Skipping history record with no uri")
            return
        with self._lock:
            if uri not in self._store and len(self._store) >= self.max_entries:
                self._evict_one()
            existing = self._store.get(uri)
            if not isinstance(existing, dict):
                if existing is not None:
                    LOG.warning(f"Overwriting malformed history entry "
                                f"{uri!r}: expected a dict, got "
                                f"{type(existing).__name__}")
                existing = {}
            record = dict(existing)
            record.update(entry)
            record["play_count"] = existing.get("play_count", 0) + 1
            record["last_played"] = time.time()
            self._store[uri] = record
            self._store.store()

    def _evict_one(self) -> None:
        """Drop a victim uri to make room for a new one. Caller holds the
        lock.

        The ``DEFAULT_RECENT_LIMIT`` most-recently-played uris are exempt:
        without this, every brand-new track (play_count 1) is the global
        lowest count and gets evicted by its own successor the moment the
        store saturates, so "recently played" would degenerate to one
        fresh entry plus a wall of old high-count tracks. Among the
        non-exempt remainder, the lowest play_count is evicted, oldest
        last_played breaking ties.
        """
        rows = []
        for uri, row in self._store.items():
            if not isinstance(row, dict):
                rows.append((0, 0, uri))
                continue
            rows.append((row.get("play_count", 0),
                        row.get("last_played", 0), uri))
        if not rows:
            return
        by_recency = sorted(rows, key=lambda r: r[1], reverse=True)
        # capped at max_entries - 1 so at least one candidate always
        # remains evictable, even on a store smaller than the recent
        # window - otherwise every row would be exempt and eviction could
        # never make room for the very insert that triggered it
        exempt_count = min(DEFAULT_RECENT_LIMIT, self.max_entries - 1)
        exempt = {uri for _, _, uri in by_recency[:exempt_count]}
        candidates = [r for r in rows if r[2] not in exempt] or rows
        candidates.sort(key=lambda c: (c[0], c[1]))
        evict_uri = candidates[0][2]
        self._store.pop(evict_uri)
        LOG.debug(f"Evicted {evict_uri} from play history (bound reached)")

    def _to_entry(self, uri: str, row: dict) -> Optional[MediaEntry]:
        """Build a MediaEntry from a stored row, tolerant of malformed
        data - the store is persisted JSON, editable outside this
        process.

        Reads the media_type/playback the entry was actually recorded
        with (a played movie must round-trip as a video, not silently
        become MUSIC/AUDIO and get handed to an audio backend); the
        MediaType/PlaybackType defaults only apply when the row predates
        those fields or they were stripped.
        """
        if not isinstance(row, dict):
            LOG.warning(f"Skipping malformed history entry {uri!r}: "
                        f"expected a dict, got {type(row).__name__}")
            return None
        title = row.get("title", "")
        if not title:
            LOG.warning(f"Skipping history entry {uri!r}: "
                        f"missing/empty title")
            return None
        try:
            media_type = MediaType(row.get("media_type", MediaType.MUSIC))
        except ValueError:
            LOG.warning(f"History entry {uri!r} has an unrecognised "
                        f"media_type {row.get('media_type')!r} - defaulting "
                        f"to MUSIC (row written under a newer ovos-utils?)")
            media_type = MediaType.MUSIC
        try:
            playback = PlaybackType(row.get("playback", PlaybackType.AUDIO))
        except ValueError:
            LOG.warning(f"History entry {uri!r} has an unrecognised "
                        f"playback {row.get('playback')!r} - defaulting "
                        f"to AUDIO (row written under a newer ovos-utils?)")
            playback = PlaybackType.AUDIO
        return MediaEntry(uri=uri, title=title, artist=row.get("artist", ""),
                          image=row.get("image", ""),
                          media_type=media_type,
                          playback=playback,
                          match_confidence=row.get("play_count", 0) + 50)

    def recent(self, limit: int = DEFAULT_RECENT_LIMIT) -> List[MediaEntry]:
        """Most recently played entries first."""
        rows = sorted(self.items(),
                      key=lambda kv: kv[1].get("last_played", 0)
                      if isinstance(kv[1], dict) else 0, reverse=True)
        entries = []
        for uri, row in rows:
            entry = self._to_entry(uri, row)
            if entry is not None:
                entries.append(entry)
            if len(entries) >= limit:
                break
        return entries

    def most_played(self, limit: int = DEFAULT_RECENT_LIMIT) -> List[MediaEntry]:
        """Most played entries first, most-recent breaking ties."""
        rows = sorted(self.items(),
                      key=lambda kv: (kv[1].get("play_count", 0),
                                      kv[1].get("last_played", 0))
                      if isinstance(kv[1], dict) else (0, 0), reverse=True)
        entries = []
        for uri, row in rows:
            entry = self._to_entry(uri, row)
            if entry is not None:
                entries.append(entry)
            if len(entries) >= limit:
                break
        return entries

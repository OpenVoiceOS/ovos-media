# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""Validation and coercion of bus payloads.

Nothing here talks to the bus or to a player: every function takes a raw
value off the wire and returns something safe to act on, so the rules a
payload must satisfy live in one place instead of being restated inside
each handler.
"""

import math
import unicodedata
from collections.abc import Iterable
from typing import Optional

from ovos_utils.log import LOG
from ovos_utils.ocp import (MediaEntry, MediaState, Playlist, PluginStream,
                            TrackState, dict2entry)

# fields carrying a duration/offset in milliseconds on every track-shaped
# payload; a non-numeric value in any of them poisons Playlist.length,
# which sums them over every contained entry.
_NUMERIC_TRACK_FIELDS = ("length", "position")


def is_real_number(value) -> bool:
    """True only for an actual finite real number.

    ``bool`` is an ``int`` subclass but is never a legitimate numeric value
    here (durations/positions/etc), and ``NaN``/``inf``/``-inf`` ARE valid
    ``float`` instances that pass a bare ``isinstance(x, (int, float))``
    check yet blow up downstream (``int(nan)`` raises ``ValueError``,
    ``int(inf)`` raises ``OverflowError``, ``nan * 1000`` propagates a NaN
    into the backend). Every bus-fed numeric field is guarded by this one
    check.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def is_injection_char(c: str) -> bool:
    """True for a character that acts as a newline/format-injection
    surface in log viewers or HTTP stacks: C0/C1 controls (category Cc,
    also covering the historical ``< 0x20`` and ``0x7f`` checks) and the
    Unicode line/paragraph separators U+2028/U+2029 (Zl/Zp) - all of which
    act as newline-equivalents even though they aren't ASCII control
    characters.

    Format chars (Cf: soft hyphen U+00AD, zero-width joiner U+200D, BOM
    U+FEFF, bidi overrides, ...) are deliberately NOT rejected here - they
    show up in legitimate real-world filenames, and rejecting them refused
    playback of real local files. Bidi/zero-width spoofing via Cf is
    cosmetic, not a newline-equivalent, and isn't worth breaking those
    files over.
    """
    return unicodedata.category(c) in ("Cc", "Zl", "Zp")


def decode_playback_time(data: dict) -> dict:
    """Decode the 'length'/'position' pair of an ``ovos.common_play.playback_time``
    payload into milliseconds.

    Both fields are validated before either is returned, so a valid
    ``length`` never commits while an invalid ``position`` in the same
    message is still undiscovered — a partially applied message would leave
    the player describing a track that never existed.

    Only real numbers survive: a ``str`` would otherwise silently coerce
    (``int("5000" * 1000)`` overflows the MPRIS int64 wire type) and
    ``NaN``/``inf`` would raise out of ``int()``. Negative values are
    refused; ``0`` is valid. Rejected fields are absent from the result.
    """
    decoded = {}
    for field in _NUMERIC_TRACK_FIELDS:
        value = data.get(field)
        if not is_real_number(value) or value < 0:
            LOG.debug(f"ignoring invalid '{field}' in playback_time "
                      f"message: {value!r}")
            continue
        decoded[field] = int(value)
    return decoded


def decode_track_state(data: dict) -> TrackState:
    """Decode the 'state' field of an ``ovos.common_play.track.state``
    payload. An int is coerced to its ``TrackState`` member; anything else
    is refused."""
    state = data.get("state")
    if state is None:
        raise ValueError(f"Got state update message with no state: {data}")
    if isinstance(state, int):
        state = TrackState(state)
    if not isinstance(state, TrackState):
        raise ValueError(f"Expected int or TrackState, but got: {state}")
    return state


def decode_media_state(data: dict) -> MediaState:
    """Decode the 'state' field of an ``ovos.common_play.media.state``
    payload. An int is coerced to its ``MediaState`` member; anything else
    is refused."""
    state = data.get("state")
    if state is None:
        raise ValueError(f"Got state update message with no state: {data}")
    if isinstance(state, int):
        state = MediaState(state)
    if not isinstance(state, MediaState):
        raise ValueError(f"Expected int or MediaState, but got: {state}")
    return state


def _is_valid_media(media: dict) -> bool:
    """True when a media dict has a shape :func:`ovos_utils.ocp.dict2entry`
    can turn into a playable track. One predicate, shared by
    :func:`decode_media` and :func:`validated_entries`, so the two never
    disagree on what a malformed dict looks like.

    Mirrors ``dict2entry``'s own precedence exactly - playlist >
    extractor_id > uri - rather than letting 'uri' govern regardless of
    the other two: a truthy 'playlist' wins and must be a non-empty
    list/tuple; failing that, a truthy 'extractor_id' wins and must be a
    str paired with a truthy 'stream'; failing that, a present 'uri' must
    be a non-empty str (an int/float/list 'uri' passes ``dict2entry``'s
    own truthiness check and reaches ``MediaEntry``/roster.select
    unvalidated, where it dies as a logged traceback instead of the
    warn-and-drop every other malformed bus field gets). Anything else -
    no shape at all - is refused.

    Because 'playlist'/'extractor_id' are checked first, a malformed
    'uri' alongside a valid 'playlist' or 'extractor_id' is ignored, not
    refused - ``dict2entry`` itself never looks at 'uri' once 'playlist'
    or 'extractor_id' has already decided the shape.
    """
    if media.get("playlist"):
        playlist = media["playlist"]
        return isinstance(playlist, (list, tuple)) and len(playlist) > 0
    if media.get("extractor_id"):
        extractor_id = media["extractor_id"]
        return isinstance(extractor_id, str) and bool(media.get("stream"))
    if "uri" in media:
        uri = media["uri"]
        return isinstance(uri, str) and uri != ""
    return False


def decode_media(data: dict) -> Optional[dict]:
    """Decode the 'media' track of an ``ovos.common_play.play`` payload.

    Returns None when there is nothing to act on: an absent/empty track, a
    track that is not a dict (a list/str would bleed into the now_playing
    metadata field by field), or a plain-media dict (no 'playlist'/
    'extractor_id') whose 'uri' is not a non-empty string.
    """
    media = data.get("media")
    if not media:
        return None
    if not isinstance(media, dict):
        LOG.warning(f"ignoring play request, expected a dict track, "
                    f"got: {media!r}")
        return None
    if not _is_valid_media(media):
        LOG.warning(f"ignoring play request with invalid media shape: "
                    f"{media!r}")
        return None
    media = _screen_nested_playlist(media)
    if media is None:
        return None
    return media


def decode_playlist_tracks(data: dict) -> Optional[list]:
    """Decode the 'tracks' list of a playlist payload.

    An absent key decodes to an empty list (a legal "set an empty
    playlist"); a non-list value is refused, so the current playlist is
    never cleared on account of a malformed request.
    """
    tracks = data.get("tracks") or []
    if not isinstance(tracks, (list, tuple)):
        LOG.warning(f"ignoring playlist payload of type "
                    f"{type(tracks).__name__} - keeping current playlist")
        return None
    return list(tracks)


def decode_seek(data: dict) -> Optional[dict]:
    """Decode an ``ovos.common_play.seek`` payload into the one path it
    asks for.

    A seek arrives either as an absolute position in milliseconds
    ('seekValue', from the audio player GUI's seekbar) or as an offset in
    seconds from the current position ('seconds', from the bus api). The two
    fields are decoded independently, so a bad value in one never costs the
    other:

    - a valid 'seekValue' wins and is returned as ``{"seekValue": ms}``,
      whatever 'seconds' holds. ``0`` is a legal absolute position (the very
      start of the track), not a missing field.
    - 'seekValue' present but not a finite number refuses the whole request:
      the caller asked to jump to a specific position, so falling back to a
      relative seek would move the track somewhere nobody asked for.
    - otherwise the relative path is returned as ``{"seconds": offset}``,
      an absent 'seconds' meaning 0.

    NaN/inf are refused in both fields: they reach the backend's
    set_track_position as-is, and raise out of ``int()`` in the MPRIS and
    GUI paths.
    """
    if "seekValue" in data:
        position = data["seekValue"]
        if is_real_number(position):
            return {"seekValue": position}
        LOG.warning(f"ignoring seek request with non-numeric 'seekValue': "
                    f"{position!r}")
        return None
    seconds = data.get("seconds", 0)
    if not is_real_number(seconds):
        LOG.warning(f"ignoring seek request with non-numeric 'seconds': "
                    f"{seconds!r}")
        return None
    return {"seconds": seconds}


def decode_track_position(data: dict) -> Optional[float]:
    """Decode the 'position' of an ``ovos.common_play.set_track_position``
    payload, in milliseconds. An absent position means there is nothing to
    seek to; NaN/inf are refused before they reach the backend."""
    position = data.get("position")
    if position is None:
        return None
    if not is_real_number(position):
        LOG.warning(f"ignoring set_track_position request with non-numeric "
                    f"'position': {position!r}")
        return None
    return position


def decode_skill_id(data: dict) -> Optional[str]:
    """Decode the 'skill_id' every OCP skill announcement is keyed by."""
    skill_id = data.get("skill_id")
    if not skill_id or not isinstance(skill_id, str):
        LOG.warning(f"ignoring skill announcement with invalid 'skill_id': "
                    f"{skill_id!r}")
        return None
    return skill_id


def flatten_media_types(value) -> list:
    """Flatten an announced ``media_types`` value into a flat list of members.

    A skill can announce media_types as a set/tuple/dict_keys/generator, or
    nest any of those inside a list - wrapping such a value as ``[value]``
    would make membership checks like ``MediaType.ADULT in media_types``
    always False regardless of contents, so any non-scalar iterable is
    flattened recursively.
    """
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        return [value]
    flat = []
    for item in value:
        flat.extend(flatten_media_types(item))
    return flat


def _coerce_numeric_attrs(entry) -> None:
    """Coerce non-real length/position ATTRIBUTES to 0, in place."""
    for field in _NUMERIC_TRACK_FIELDS:
        value = getattr(entry, field, None)
        if not is_real_number(value):
            LOG.debug(f"coercing invalid '{field}' on playlist entry "
                      f"to 0: {value!r}")
            setattr(entry, field, 0)


def _coerce_numeric_keys(member: dict) -> None:
    """Coerce non-real length/position KEYS to 0, in place.

    Only keys that are present are touched: a raw dict member is handed
    back to ``MediaEntry.from_dict`` later, which supplies its own defaults
    for absent fields.
    """
    for field in _NUMERIC_TRACK_FIELDS:
        if field in member and not is_real_number(member[field]):
            LOG.debug(f"coercing invalid '{field}' on raw playlist "
                      f"entry to 0: {member[field]!r}")
            member[field] = 0


def sanitize_nested_playlist(playlist, visited: Optional[set] = None) -> None:
    """Sanitize length/position on every MediaEntry/PluginStream
    reachable inside a (possibly self-referential) tree of nested
    Playlists, mutating the shared objects in place.

    `Playlist.entries` is a computed property that returns a filtered
    copy of the list and drops nested Playlist members entirely, so it
    cannot be used to reach or fix them. Iterating the Playlist itself
    (it subclasses list) reaches every raw member, including nested
    Playlists. Dict members are *not* sanitized by reading them either:
    `Playlist.entries` calls `dict2entry`/`MediaEntry.from_dict` fresh
    on every read, and neither applies any numeric coercion - so a raw
    dict member must be sanitized here, in place, or it stays a live
    landmine for `Playlist.length`'s sum().
    """
    visited = set() if visited is None else visited
    if id(playlist) in visited:
        return
    visited.add(id(playlist))
    for member in list.__iter__(playlist):
        if isinstance(member, (MediaEntry, PluginStream)):
            _coerce_numeric_attrs(member)
        elif isinstance(member, Playlist):
            sanitize_nested_playlist(member, visited)
        elif isinstance(member, dict):
            _coerce_numeric_keys(member)
            # dict2entry() only turns a dict into a nested Playlist when
            # it carries a truthy "playlist" key - recurse into that
            # list of raw (also unsanitized) member dicts too.
            if isinstance(member.get("playlist"), list):
                sanitize_raw_playlist_dicts(member["playlist"], visited)


def sanitize_raw_playlist_dicts(members, visited: Optional[set] = None) -> None:
    """Sanitize length/position in place across a raw list of playlist
    member dicts/objects, as found under a dict's "playlist" key
    (see :func:`sanitize_nested_playlist`)."""
    visited = set() if visited is None else visited
    if id(members) in visited:
        return
    visited.add(id(members))
    for member in members:
        if isinstance(member, (MediaEntry, PluginStream)):
            _coerce_numeric_attrs(member)
        elif isinstance(member, Playlist):
            sanitize_nested_playlist(member, visited)
        elif isinstance(member, dict):
            _coerce_numeric_keys(member)
            if isinstance(member.get("playlist"), list):
                sanitize_raw_playlist_dicts(member["playlist"], visited)


def _screen_nested_playlist(media: dict) -> Optional[dict]:
    """When *media* is playlist-shaped, screen its nested 'playlist'
    entries through :func:`validated_entries` before ``dict2entry`` ever
    builds a ``Playlist`` from them.

    ``dict2entry``/``Playlist.from_dict`` only truthiness-checks each raw
    member and builds a ``MediaEntry``/``PluginStream`` from it
    unconditionally - a bad nested entry (eg. a non-str 'uri') would
    otherwise reach ``MediaEntry``/roster.select unvalidated one level
    down from the guard on the outer dict, the exact failure this module
    exists to prevent.

    Returns a shallow copy of *media* with 'playlist' replaced by its
    validated entries, or None if none of them survive - a dict2entry'd
    Playlist with zero entries would let ``track[0]`` index into an
    empty list. *media* itself is left untouched. A no-op (returns
    *media* as-is) when 'playlist' is absent/empty, since that shape
    never reaches ``Playlist.from_dict`` in the first place.
    """
    nested = media.get("playlist")
    if not nested:
        return media
    screened = validated_entries(nested)
    if not screened:
        LOG.warning(f"ignoring play request, no valid entries in nested "
                    f"playlist: {nested!r}")
        return None
    media = dict(media)
    media["playlist"] = screened
    return media


def validated_entries(tracks) -> list:
    """Coerce a playlist payload into valid entries, skipping the bad
    ones with a warning instead of aborting mid-mutation. A non-list
    payload (eg. a bare string, which would iterate character-wise)
    yields no entries."""
    if not isinstance(tracks, (list, tuple)):
        LOG.warning(f"ignoring playlist payload of type "
                    f"{type(tracks).__name__}, expected a list of tracks")
        return []
    entries = []
    for track in tracks:
        try:
            if isinstance(track, dict):
                if not _is_valid_media(track):
                    raise ValueError(f"invalid media shape: {track!r}")
                track = _screen_nested_playlist(track)
                if track is None:
                    raise ValueError("nested playlist has no valid entries")
                track = dict2entry(track)
            if not isinstance(track, (MediaEntry, Playlist, PluginStream)):
                raise ValueError(f"not a valid track: {track!r}")
            # a non-numeric length/position (eg. bus-fed "garbage")
            # must not poison Playlist.length's later sum() over all
            # entries - sanitize to 0 rather than reject the whole entry.
            # Playlist.length is a read-only computed property (sum of
            # its own entries), so only individual tracks are sanitized.
            if isinstance(track, (MediaEntry, PluginStream)):
                _coerce_numeric_attrs(track)
            elif isinstance(track, Playlist):
                # a nested Playlist's own tracks are entries too - bad
                # values inside them would otherwise reach the outer
                # Playlist.length (a sum over all contained entries)
                # unsanitized. Recurse arbitrarily deep.
                sanitize_nested_playlist(track)
            entries.append(track)
        except Exception as e:
            LOG.warning(f"skipping invalid playlist entry: {e}")
    return entries

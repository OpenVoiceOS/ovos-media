"""Stream extraction for a media entry.

OCP plugins turn a ``{SEI}//{uri}`` reference into a real stream plus extra
metadata at playback time. This module runs that extraction against an entry
and validates whatever comes back before any of it reaches the entry.
"""
from typing import Optional

from ovos_plugin_manager.ocp import load_stream_extractors
from ovos_utils.log import LOG

from ovos_media.bus.schemas import is_injection_char


def extract_stream(entry, video: bool, stream_xtract=None) -> None:
    """Resolve the stream of *entry* and merge the extractor metadata into it.

    @param entry: MediaEntry (or NowPlaying) to resolve and update in place
    @param video: whether a video stream is wanted
    @param stream_xtract: stream extractor collection; loaded on demand
    @raise ValueError: the entry has no uri, or the resolved uri is not a
        playable stream
    """
    uri = entry.uri
    if not uri:
        raise ValueError("No URI to extract stream from")
    if stream_xtract is None:
        stream_xtract = load_stream_extractors()
    meta = stream_xtract.extract_stream(uri, video)
    # validate the extractor-returned uri BEFORE mutating any state - a
    # non-string uri (int/list/dict) or a whitespace-only string must never
    # poison the entry's uri/title, it must refuse the same way a missing uri
    # does. An empty string is left alone: it is falsy, so update(newonly=True)
    # below does not overwrite the existing uri with it anyway.
    if meta:
        _validate_extracted_uri(meta.get("uri"))
        LOG.info(f"OCP plugins metadata: {meta}")
        entry.update(meta, newonly=True)
        entry.original_uri = uri

    # validate extracted uri
    if not any(entry.uri.startswith(s) for s in ["http", "file", "/"]):
        raise ValueError(f"invalid stream: {entry.uri!r}")
    # a uri passing the prefix check above may still smuggle control
    # characters (eg. embedded newlines for header/log injection) - refuse
    # those too
    if any(is_injection_char(c) for c in entry.uri):
        raise ValueError(f"invalid stream: {entry.uri!r}")


def _validate_extracted_uri(extracted_uri: Optional[str]) -> None:
    if extracted_uri is None:
        return
    if not isinstance(extracted_uri, str):
        raise ValueError(f"invalid stream: {extracted_uri!r}")
    if extracted_uri and not extracted_uri.strip():
        raise ValueError(f"invalid stream: {extracted_uri!r}")
    if any(is_injection_char(c) for c in extracted_uri):
        # a uri that otherwise looks fine (eg. a valid http prefix) may still
        # smuggle control characters/newlines - refuse before mutating state,
        # same as the non-string/whitespace checks above (log/header-injection
        # surface downstream)
        raise ValueError(f"invalid stream: {extracted_uri!r}")

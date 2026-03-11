# Copyright 2024 OpenVoiceOS
#
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
"""Legacy ``mycroft.audio.service.*`` bus API compatibility shim.

Skills and other components written against the classic AudioService API emit
``mycroft.audio.service.*`` messages.  This module bridges that namespace to
the modern ``ovos.common_play.*`` API so that ovos-media can serve as a
drop-in replacement for ovos-audio's AudioService+OCP combination without
requiring all callers to be updated at once.

Usage::

    from ovos_media.legacy_api import LegacyAudioServiceCompat
    compat = LegacyAudioServiceCompat(player, bus)
    # …later…
    compat.shutdown()
"""
from typing import List, Union, Tuple, Optional

from ovos_bus_client.message import Message
from ovos_utils.log import LOG
from ovos_utils.ocp import MediaEntry, PlaybackType, MediaType


def _tracks_to_entries(
    tracks: List[Union[str, Tuple[str, str]]]
) -> List[dict]:
    """Convert a legacy track list to a list of ``MediaEntry`` dicts.

    The legacy format is either a list of URI strings or a list of
    ``(uri, mime_type)`` tuples.  We wrap each into a minimal
    ``MediaEntry`` dict with ``PlaybackType.AUDIO``.

    Args:
        tracks: legacy track list from ``mycroft.audio.service.play``

    Returns:
        List of ``MediaEntry.as_dict`` dicts suitable for OCP bus messages.
    """
    entries = []
    for t in tracks:
        if isinstance(t, (list, tuple)):
            uri = t[0]
        else:
            uri = str(t)
        entry = MediaEntry(
            uri=uri,
            title=uri,
            playback=PlaybackType.AUDIO,
            media_type=MediaType.AUDIO,
        )
        entries.append(entry.as_dict)
    return entries


class LegacyAudioServiceCompat:
    """Bridge between ``mycroft.audio.service.*`` and ``ovos.common_play.*``.

    Registers listeners for all ``mycroft.audio.service.*`` bus events and
    translates them to the equivalent modern OCP commands.  This allows
    skills that have not yet been updated to the new API to continue working
    when ovos-media replaces the legacy AudioService.

    Attributes:
        player: ``OCPMediaPlayer`` instance this shim delegates to.
        bus: ``MessageBusClient`` used for sending/receiving messages.
    """

    def __init__(self, player, bus):
        """Initialise the compatibility shim.

        Args:
            player: ``OCPMediaPlayer`` instance.
            bus: MessageBusClient to register event handlers on.
        """
        self.player = player
        self.bus = bus
        self._register_handlers()
        LOG.info("LegacyAudioServiceCompat: mycroft.audio.service.* shim active")

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------

    def _register_handlers(self) -> None:
        """Register all ``mycroft.audio.service.*`` bus event handlers."""
        self.bus.on("mycroft.audio.service.play", self.handle_play)
        self.bus.on("mycroft.audio.service.queue", self.handle_queue)
        self.bus.on("mycroft.audio.service.pause", self.handle_pause)
        self.bus.on("mycroft.audio.service.resume", self.handle_resume)
        self.bus.on("mycroft.audio.service.stop", self.handle_stop)
        self.bus.on("mycroft.audio.service.next", self.handle_next)
        self.bus.on("mycroft.audio.service.prev", self.handle_prev)
        self.bus.on("mycroft.audio.service.track_info", self.handle_track_info)
        self.bus.on("mycroft.audio.service.list_backends", self.handle_list_backends)
        self.bus.on("mycroft.audio.service.set_track_position",
                    self.handle_set_track_position)
        self.bus.on("mycroft.audio.service.get_track_position",
                    self.handle_get_track_position)
        self.bus.on("mycroft.audio.service.get_track_length",
                    self.handle_get_track_length)
        self.bus.on("mycroft.audio.service.seek_forward", self.handle_seek_forward)
        self.bus.on("mycroft.audio.service.seek_backward", self.handle_seek_backward)

    # ------------------------------------------------------------------
    # Playback control handlers
    # ------------------------------------------------------------------

    def handle_play(self, message: Message) -> None:
        """Handle ``mycroft.audio.service.play``.

        Converts the legacy track list to OCP MediaEntry format and
        emits ``ovos.common_play.play``.

        Legacy message data:
            - ``tracks``: ``List[str | Tuple[str, str]]``
            - ``repeat``: bool (optional, default False)
            - ``utterance``: str (optional, for preferred backend selection)
        """
        tracks: List = message.data.get("tracks", [])
        repeat: bool = message.data.get("repeat", False)

        if not tracks:
            LOG.warning("mycroft.audio.service.play: no tracks provided")
            return

        entries = _tracks_to_entries(tracks)
        media = entries[0]
        playlist = entries

        LOG.debug(f"Legacy play request: {len(tracks)} track(s), repeat={repeat}")
        self.bus.emit(message.forward("ovos.common_play.play", {
            "media": media,
            "playlist": playlist,
            "disambiguation": playlist,
            "repeat": repeat,
        }))

    def handle_queue(self, message: Message) -> None:
        """Handle ``mycroft.audio.service.queue``.

        Adds tracks to the OCP playlist without interrupting current playback.
        Falls back to ``handle_play`` if nothing is currently playing.

        Legacy message data:
            - ``tracks``: ``List[str | Tuple[str, str]]``
        """
        from ovos_utils.ocp import PlayerState
        tracks: List = message.data.get("tracks", [])
        if not tracks:
            LOG.warning("mycroft.audio.service.queue: no tracks provided")
            return

        if self.player.state == PlayerState.STOPPED:
            # nothing playing — start playback instead of queuing
            self.handle_play(message)
            return

        entries = _tracks_to_entries(tracks)
        LOG.debug(f"Legacy queue request: {len(tracks)} track(s)")
        self.bus.emit(message.forward("ovos.common_play.playlist.queue",
                                      {"tracks": entries}))

    def handle_pause(self, message: Message) -> None:
        """Handle ``mycroft.audio.service.pause``."""
        self.bus.emit(message.forward("ovos.common_play.pause"))

    def handle_resume(self, message: Message) -> None:
        """Handle ``mycroft.audio.service.resume``."""
        self.bus.emit(message.forward("ovos.common_play.resume"))

    def handle_stop(self, message: Message) -> None:
        """Handle ``mycroft.audio.service.stop``."""
        self.bus.emit(message.forward("ovos.common_play.stop"))

    def handle_next(self, message: Message) -> None:
        """Handle ``mycroft.audio.service.next``."""
        self.bus.emit(message.forward("ovos.common_play.next"))

    def handle_prev(self, message: Message) -> None:
        """Handle ``mycroft.audio.service.prev``."""
        self.bus.emit(message.forward("ovos.common_play.previous"))

    # ------------------------------------------------------------------
    # Track / backend info handlers
    # ------------------------------------------------------------------

    def handle_track_info(self, message: Message) -> None:
        """Handle ``mycroft.audio.service.track_info``.

        Queries the OCP player for current track info and re-emits it as
        ``mycroft.audio.service.track_info_reply``.
        """
        response = self.bus.wait_for_response(
            message.forward("ovos.common_play.track_info"),
            reply_type="ovos.common_play.track_info.response",
            timeout=3.0,
        )
        data = response.data if response else {}
        self.bus.emit(message.reply("mycroft.audio.service.track_info_reply", data))

    def handle_list_backends(self, message: Message) -> None:
        """Handle ``mycroft.audio.service.list_backends``.

        Returns available audio backends in the legacy response format.
        """
        response = self.bus.wait_for_response(
            message.forward("ovos.common_play.list_backends"),
            reply_type="ovos.common_play.list_backends.response",
            timeout=3.0,
        )
        data = response.data if response else {}
        self.bus.emit(message.reply("mycroft.audio.service.list_backends.response",
                                    data))

    # ------------------------------------------------------------------
    # Position / seeking handlers
    # ------------------------------------------------------------------

    def handle_set_track_position(self, message: Message) -> None:
        """Handle ``mycroft.audio.service.set_track_position``.

        Legacy data: ``position`` in milliseconds.
        """
        self.bus.emit(message.forward("ovos.common_play.set_track_position",
                                      {"position": message.data.get("position")}))

    def handle_get_track_position(self, message: Message) -> None:
        """Handle ``mycroft.audio.service.get_track_position``.

        Queries OCP and re-emits position as a response.
        """
        response = self.bus.wait_for_response(
            message.forward("ovos.common_play.get_track_position"),
            reply_type="ovos.common_play.get_track_position.response",
            timeout=3.0,
        )
        data = response.data if response else {"position": None}
        self.bus.emit(message.reply(
            "mycroft.audio.service.get_track_position.response", data))

    def handle_get_track_length(self, message: Message) -> None:
        """Handle ``mycroft.audio.service.get_track_length``.

        Queries OCP and re-emits duration as a response.
        """
        response = self.bus.wait_for_response(
            message.forward("ovos.common_play.get_track_length"),
            reply_type="ovos.common_play.get_track_length.response",
            timeout=3.0,
        )
        data = response.data if response else {"length": None}
        self.bus.emit(message.reply(
            "mycroft.audio.service.get_track_length.response", data))

    def handle_seek_forward(self, message: Message) -> None:
        """Handle ``mycroft.audio.service.seek_forward``.

        Legacy data: ``seconds`` to skip forward.
        """
        seconds = message.data.get("seconds", 1)
        self.bus.emit(message.forward("ovos.common_play.seek",
                                      {"seconds": seconds}))

    def handle_seek_backward(self, message: Message) -> None:
        """Handle ``mycroft.audio.service.seek_backward``.

        Legacy data: ``seconds`` to skip backward (emitted as negative).
        """
        seconds = message.data.get("seconds", 1)
        self.bus.emit(message.forward("ovos.common_play.seek",
                                      {"seconds": -seconds}))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Remove all registered bus event listeners."""
        self.bus.remove("mycroft.audio.service.play", self.handle_play)
        self.bus.remove("mycroft.audio.service.queue", self.handle_queue)
        self.bus.remove("mycroft.audio.service.pause", self.handle_pause)
        self.bus.remove("mycroft.audio.service.resume", self.handle_resume)
        self.bus.remove("mycroft.audio.service.stop", self.handle_stop)
        self.bus.remove("mycroft.audio.service.next", self.handle_next)
        self.bus.remove("mycroft.audio.service.prev", self.handle_prev)
        self.bus.remove("mycroft.audio.service.track_info", self.handle_track_info)
        self.bus.remove("mycroft.audio.service.list_backends",
                        self.handle_list_backends)
        self.bus.remove("mycroft.audio.service.set_track_position",
                        self.handle_set_track_position)
        self.bus.remove("mycroft.audio.service.get_track_position",
                        self.handle_get_track_position)
        self.bus.remove("mycroft.audio.service.get_track_length",
                        self.handle_get_track_length)
        self.bus.remove("mycroft.audio.service.seek_forward",
                        self.handle_seek_forward)
        self.bus.remove("mycroft.audio.service.seek_backward",
                        self.handle_seek_backward)
        LOG.info("LegacyAudioServiceCompat: shutdown complete")

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
"""Ingress edge for the player, its NowPlaying, its OCPMediaCatalog and
MediaService: every subscription those four make is one entry in a
registration table, and the same table drives teardown, so what is added
and what is removed can never drift apart.

An entry names the topic, an optional payload decoder from
:mod:`ovos_media.bus.schemas`, whether the topic is gated to the local
session, whether it mutates the player, and the method to call. The
wrapper built around each entry decodes, gates, and then either submits
the call to the player's dispatcher (mutating commands, which then run
one at a time in arrival order) or runs it inline (queries, which answer
from the published snapshot and must not wait behind a command).

The backend services (:mod:`ovos_media.media_backends`) still bind their
own 'ovos.common_play.media.state' listener in
``BaseMediaService.__init__``; they are not routed through this table.
"""

from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from ovos_bus_client.session import SessionManager
from ovos_utils.log import LOG

from ovos_media.bus.schemas import (decode_media, decode_media_state,
                                    decode_playlist_tracks,
                                    decode_seek, decode_skill_id,
                                    decode_track_position, decode_track_state)
from ovos_media.utils import is_default_session


@dataclass(frozen=True)
class BusHandler:
    """One bus subscription.

    topic: the message type to listen on.
    target: the method called with the Message once the payload is accepted.
    decoder: a :mod:`ovos_media.bus.schemas` function taking ``message.data``.
        It rejects a payload by returning None or raising ValueError, and
        the target is then not called at all.
    gated: True when only the local/"default" session may trigger the
        target (see :func:`ovos_media.utils.is_default_session`).
    dispatch: True when the target mutates the player and must therefore
        run on its dispatcher. False for queries (answered from the
        published snapshot or read live from a backend) and for the one
        target that blocks on the bus, ``handle_record_end``.
    """
    topic: str
    target: Callable
    decoder: Optional[Callable] = None
    gated: bool = False
    dispatch: bool = True


class _ServiceTopics:
    """The two topics the daemon answers about itself.

    'ovos.common_play.ping' proves the daemon is alive, and
    'opm.audio.query' reports the installed audio backends in the shape
    OPM discovery expects.
    """

    def __init__(self, bus, service) -> None:
        self.bus = bus
        self.service = service

    def handle_ping(self, message) -> None:
        self.bus.emit(message.reply("ovos.common_play.pong"))

    def handle_opm_audio_query(self, message) -> None:
        backends = self.service.ocp.audio_service.available_backends()
        self.bus.emit(message.response({"plugins": list(backends.keys()),
                                        "configs": backends,
                                        "options": {}}))


class OCPBusApi:
    """Owns every bus subscription of an ovos-media process.

    An instance is built around whichever owners it is given: a player (its
    own handlers plus those of the NowPlaying and OCPMediaCatalog it
    created) and/or a MediaService. Registration happens on construction;
    :meth:`shutdown` undoes exactly it.
    """

    # session_ids a gated drop has already been WARNING-logged for, bounded
    # so a malicious/misbehaving peer spraying distinct session ids cannot
    # grow this without limit (see _log_gated_drop)
    _MAX_WARNED_DROPPED_SESSIONS = 32

    def __init__(self, bus, player=None, service=None) -> None:
        self.bus = bus
        self.player = player
        self.service = service
        self.table: List[BusHandler] = self._build_table()
        # (topic, wrapper) pairs actually bound on the bus
        self._registrations: List[Tuple[str, Callable]] = []
        self._warned_dropped_sessions: set = set()
        self.register()

    @property
    def validate_source(self) -> bool:
        """Whether gated topics are filtered by session at all.

        A satellite whose sessions are not NAT'd to "default" sets
        ``media.validate_source: false`` and acts on every session.
        """
        owner = self.player if self.player is not None else self.service
        return getattr(owner, "validate_source", True)

    def _build_table(self) -> List[BusHandler]:
        """Build the registration table for the owners this edge was given.

        Order is significant: several topics have more than one subscriber
        and the bus dispatches them in registration order. NowPlaying's
        'ovos.common_play.play' entry comes before the player's, matching
        the order in which those two objects used to bind themselves.
        """
        table: List[BusHandler] = []
        if self.player is not None:
            table += self._now_playing_table(self.player.now_playing)
            table += self._catalog_table(self.player.media)
            table += self._player_table(self.player)
        if self.service is not None:
            table += self._service_table(self.service)
        return table

    @staticmethod
    def _now_playing_table(np) -> List[BusHandler]:
        # NowPlaying deliberately does NOT subscribe to
        # 'ovos.common_play.media.state'. The player is the only consumer of
        # END_OF_MEDIA on that topic (the backend services also listen, but
        # act on LOADED_MEDIA only) and it calls NowPlaying.on_end_of_media()
        # itself, in order, after capturing the pre-reset playback type and
        # uri. As a second subscriber, NowPlaying had no ordering guarantee
        # against the player and raced its own reset against the autoplay
        # decision that reads it.
        return [
            BusHandler("ovos.common_play.track.state",
                       np.handle_track_state_change,
                       decoder=decode_track_state),
            # a play command from a satellite session must not bleed its
            # metadata into the local now_playing
            BusHandler("ovos.common_play.play", np.handle_external_play,
                       decoder=decode_media, gated=True),
            BusHandler("ovos.common_play.playback_time",
                       np.handle_sync_seekbar),
        ]

    @staticmethod
    def _catalog_table(catalog) -> List[BusHandler]:
        # the catalog owns its own bookkeeping (the skill roster, the
        # search playlist) and touches no player state, so its handlers stay
        # off the dispatcher: an announce must land inside the 200 ms
        # settle window get_featured_skills waits, not behind a play command.
        return [
            BusHandler("ovos.common_play.skills.detach",
                       catalog.handle_ocp_skill_detach,
                       decoder=decode_skill_id, dispatch=False),
            BusHandler("ovos.common_play.announce",
                       catalog.handle_skill_announce,
                       decoder=decode_skill_id, dispatch=False),
        ]

    @staticmethod
    def _player_table(player) -> List[BusHandler]:
        # NOTE: the player does NOT subscribe to its own
        # 'ovos.common_play.player.state' event — set_player_state() is the
        # single authoritative writer of player.state; external subscribers
        # (MPRIS, GUI clients) may still listen on the bus.
        #
        # 'ovos.common_play.search.start'/'.search.end'/'.home' are
        # pipeline-side signals (the OCP pipeline plugin uses them to drive
        # a GUI's own loading/navigation state); this daemon has no
        # in-process GUI and no other state to change in response to them,
        # so none of the three is subscribed.
        return [
            BusHandler("ovos.common_play.media.state",
                       player.handle_player_media_update,
                       decoder=decode_media_state),
            BusHandler("ovos.common_play.play", player.handle_play_request,
                       decoder=decode_media, gated=True),
            BusHandler("ovos.common_play.pause", player.handle_pause_request,
                       gated=True),
            BusHandler("ovos.common_play.play_pause",
                       player.handle_pause_toggle_request, gated=True),
            BusHandler("ovos.common_play.resume", player.handle_resume_request,
                       gated=True),
            BusHandler("ovos.common_play.stop", player.handle_stop_request,
                       gated=True),
            BusHandler("ovos.common_play.next", player.handle_next_request,
                       gated=True),
            BusHandler("ovos.common_play.previous", player.handle_prev_request,
                       gated=True),
            BusHandler("ovos.common_play.seek", player.handle_seek_request,
                       decoder=decode_seek, gated=True),
            BusHandler("ovos.common_play.get_track_length",
                       player.handle_track_length_request,
                       dispatch=False),
            BusHandler("ovos.common_play.set_track_position",
                       player.handle_set_track_position_request,
                       decoder=decode_track_position, gated=True),
            BusHandler("ovos.common_play.get_track_position",
                       player.handle_track_position_request,
                       dispatch=False),
            BusHandler("ovos.common_play.track_info",
                       player.handle_track_info_request,
                       dispatch=False),
            BusHandler("ovos.common_play.list_backends",
                       player.handle_list_backends_request,
                       dispatch=False),
            BusHandler("ovos.common_play.playlist.set",
                       player.handle_playlist_set_request,
                       decoder=decode_playlist_tracks, gated=True),
            BusHandler("ovos.common_play.playlist.clear",
                       player.handle_playlist_clear_request, gated=True),
            BusHandler("ovos.common_play.playlist.queue",
                       player.handle_playlist_queue_request,
                       decoder=decode_playlist_tracks, gated=True),
            BusHandler("ovos.common_play.duck", player.handle_duck_request,
                       gated=True),
            BusHandler("ovos.common_play.unduck", player.handle_unduck_request,
                       gated=True),
            BusHandler("ovos.common_play.cork", player.handle_cork_request,
                       gated=True),
            BusHandler("ovos.common_play.uncork", player.handle_uncork_request,
                       gated=True),
            # ovos-audio emits 'ovos.audio.output.started'/'.ended'
            # unconditionally on every TTS output (ovos_audio/playback.py
            # begin_audio/end_audio). The ovos.common_play.duck/cork
            # messages are an alternative ducking path, only emitted when
            # tts.ocp_duck / tts.ocp_cork are enabled in config (both
            # default False). Binding these spec topics to the same targets
            # keeps ducking working on default installs.
            BusHandler("ovos.audio.output.started",
                       player.handle_duck_request, gated=True),
            BusHandler("ovos.audio.output.ended",
                       player.handle_unduck_request, gated=True),
            # mic-cork integration — no listener emits an OCP cork
            # equivalent for the mic recording window.
            BusHandler("recognizer_loop:record_begin",
                       player.handle_cork_request, gated=True),
            # record_end and utterance.handled do nothing except resume or
            # unduck, both of which are gated, so the gate sits on the
            # topic itself.
            # waits up to 8s for a 'speak' before uncorking — that wait
            # stays on the bus thread, and only the resume it decides on is
            # submitted (see OCPMediaPlayer.handle_record_end)
            BusHandler("recognizer_loop:record_end", player.handle_record_end,
                       gated=True, dispatch=False),
            BusHandler("ovos.utterance.handled",
                       player.handle_utterance_handled, gated=True),
            BusHandler("mycroft.stop", player.handle_mycroft_stop),
            BusHandler("ovos.common_play.shuffle.toggle",
                       player.handle_shuffle_toggle_request, gated=True),
            BusHandler("ovos.common_play.shuffle.set",
                       player.handle_set_shuffle, gated=True),
            BusHandler("ovos.common_play.shuffle.unset",
                       player.handle_unset_shuffle, gated=True),
            BusHandler("ovos.common_play.repeat.toggle",
                       player.handle_repeat_toggle_request, gated=True),
            BusHandler("ovos.common_play.repeat.set",
                       player.handle_set_repeat, gated=True),
            BusHandler("ovos.common_play.repeat.unset",
                       player.handle_unset_repeat, gated=True),
            BusHandler("ovos.common_play.SEI.get", player.handle_get_SEIs,
                       dispatch=False),
            BusHandler("ovos.common_play.like", player.handle_like,
                       gated=True),
            BusHandler("ovos.common_play.unlike", player.handle_unlike,
                       gated=True),
            BusHandler("ovos.common_play.status", player.handle_status,
                       dispatch=False),
            # OCP-1 §4.4.2: read-only, like status - mutate nothing, so
            # neither is gated or dispatched.
            BusHandler("ovos.common_play.disambiguation",
                       player.handle_disambiguation_query, dispatch=False),
            BusHandler("ovos.common_play.likes",
                       player.handle_likes_query, dispatch=False),
            # external MPRIS player → reflect as OCP now_playing (no local
            # backend)
            BusHandler("ovos.common_play.mpris.now_playing",
                       player.handle_mpris_now_playing),
        ]

    def _service_table(self, service) -> List[BusHandler]:
        # the daemon's own two topics, answered here rather than by the
        # MediaService: both are pure bus edge, and the query one reads the
        # player the service built, so it can only be bound once that
        # player exists — which is when this edge is constructed.
        topics = _ServiceTopics(self.bus, service)
        self._service_topics = topics
        return [
            BusHandler("ovos.common_play.ping", topics.handle_ping),
            BusHandler("opm.audio.query", topics.handle_opm_audio_query),
        ]

    def _log_gated_drop(self, entry: "BusHandler", message) -> None:
        """Log a session-gated drop (see is_default_session).

        The FIRST drop for a given session_id is a WARNING, naming the
        dropped topic, the offending session, and the remedy - a hub
        operator whose satellite commands are silently ignored otherwise
        has no clue why without reading source. Every later drop for a
        session already warned about stays DEBUG, so a chatty satellite
        does not flood the log. ``_warned_dropped_sessions`` is bounded
        (see _MAX_WARNED_DROPPED_SESSIONS); once full, a newly seen
        session_id keeps warning on every drop rather than being silently
        forgotten - erring toward visibility over log volume.
        """
        try:
            session_id = SessionManager.get(message).session_id
        except Exception:
            session_id = "?"
        if session_id in self._warned_dropped_sessions:
            LOG.debug(f"ignoring '{entry.topic}' message, not from the "
                      f"default/local session ({session_id!r})")
            return
        if len(self._warned_dropped_sessions) < self._MAX_WARNED_DROPPED_SESSIONS:
            self._warned_dropped_sessions.add(session_id)
        LOG.warning(f"ignoring '{entry.topic}' from session {session_id!r}: "
                   f"this daemon only serves the default session; a hub "
                   f"serving satellites directly needs "
                   f"media.validate_source: false - satellite-embedded "
                   f"daemons see 'default' after HiveMind NAT")

    def _wrap(self, entry: BusHandler) -> Callable:
        """Build the listener for one table entry.

        Each entry gets its own closure. That is what makes teardown work
        for the migrated legacy topics: ovos-bus-client wraps a handler for
        a migrated topic ('ovos.audio.output.started'/'.ended',
        'recognizer_loop:record_begin'/'record_end', 'mycroft.stop') in a
        per-handler dedup guard and remembers it keyed by the function
        object. A bound method compares equal across attribute accesses, so
        sharing one between a migrated and a plain topic would put remove()
        on the dedup path for both, and the plain topic — never recorded
        there — would silently keep its listener. Per-entry closures keep
        every registration a distinct object, so each remove() resolves on
        its own.
        """
        # imported here: the player package imports this module
        from ovos_media.player.dispatcher import Dispatcher
        dispatcher = getattr(self.player, "dispatcher", None) \
            if self.player is not None else None
        if not isinstance(dispatcher, Dispatcher):
            # a service-only edge, or a player assembled piecemeal: nothing
            # to serialize against, so every target runs at the edge
            dispatcher = None

        def listener(message):
            if entry.decoder is not None:
                try:
                    if entry.decoder(message.data) is None:
                        return
                except ValueError as e:
                    LOG.warning(f"ignoring '{entry.topic}' message: {e}")
                    return
            if entry.gated and not is_default_session(message,
                                                      self.validate_source):
                self._log_gated_drop(entry, message)
                return
            if entry.dispatch and dispatcher is not None:
                dispatcher.submit(lambda: entry.target(message))
            else:
                entry.target(message)

        return listener

    def register(self) -> None:
        """Subscribe every entry in the table."""
        for entry in self.table:
            listener = self._wrap(entry)
            self.bus.on(entry.topic, listener)
            self._registrations.append((entry.topic, listener))

    def shutdown(self) -> None:
        """Remove every subscription this edge made."""
        for topic, listener in self._registrations:
            self.bus.remove(topic, listener)
        self._registrations.clear()

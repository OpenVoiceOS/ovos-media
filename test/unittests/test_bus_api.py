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
"""Tests for the bus edge: the registration table, the session gate, the
payload decoders it runs before dispatching, and teardown."""
import unittest
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message
from ovos_bus_client.session import Session, SessionManager
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import MediaState, TrackState

# The topics ovos-media answers, per owner. This list IS the contract: a
# topic dropped from the table (or silently renamed) stops a real client
# working, so it must fail here rather than in a deployment.
NOW_PLAYING_TOPICS = [
    "ovos.common_play.track.state",
    "ovos.common_play.play",
    "ovos.common_play.playback_time",
]
CATALOG_TOPICS = [
    "ovos.common_play.skills.detach",
    "ovos.common_play.announce",
]
PLAYER_TOPICS = [
    "ovos.common_play.media.state",
    "ovos.common_play.play",
    "ovos.common_play.pause",
    "ovos.common_play.play_pause",
    "ovos.common_play.resume",
    "ovos.common_play.stop",
    "ovos.common_play.next",
    "ovos.common_play.previous",
    "ovos.common_play.seek",
    "ovos.common_play.get_track_length",
    "ovos.common_play.set_track_position",
    "ovos.common_play.get_track_position",
    "ovos.common_play.track_info",
    "ovos.common_play.list_backends",
    "ovos.common_play.playlist.set",
    "ovos.common_play.playlist.clear",
    "ovos.common_play.playlist.queue",
    "ovos.common_play.duck",
    "ovos.common_play.unduck",
    "ovos.common_play.cork",
    "ovos.common_play.uncork",
    "ovos.audio.output.started",
    "ovos.audio.output.ended",
    "recognizer_loop:record_begin",
    "recognizer_loop:record_end",
    "ovos.utterance.handled",
    "mycroft.stop",
    "ovos.common_play.shuffle.toggle",
    "ovos.common_play.shuffle.set",
    "ovos.common_play.shuffle.unset",
    "ovos.common_play.repeat.toggle",
    "ovos.common_play.repeat.set",
    "ovos.common_play.repeat.unset",
    "ovos.common_play.SEI.get",
    "ovos.common_play.like",
    "ovos.common_play.unlike",
    "ovos.common_play.status",
    "ovos.common_play.disambiguation",
    "ovos.common_play.likes",
    "ovos.common_play.mpris.now_playing",
]
SERVICE_TOPICS = [
    "ovos.common_play.ping",
    "opm.audio.query",
]
# The gated half of the table: topics that change playback or persistent
# state, and so act only on the local/"default" session. This set IS the
# contract in both directions — a lost gate lets a satellite drive the local
# player, and an added one silently stops a legitimate local command.
GATED_TOPICS = {
    "ovos.common_play.play",
    "ovos.common_play.pause",
    "ovos.common_play.play_pause",
    "ovos.common_play.resume",
    "ovos.common_play.stop",
    "ovos.common_play.next",
    "ovos.common_play.previous",
    "ovos.common_play.seek",
    "ovos.common_play.set_track_position",
    "ovos.common_play.playlist.set",
    "ovos.common_play.playlist.clear",
    "ovos.common_play.playlist.queue",
    "ovos.common_play.duck",
    "ovos.common_play.unduck",
    "ovos.common_play.cork",
    "ovos.common_play.uncork",
    "ovos.audio.output.started",
    "ovos.audio.output.ended",
    "recognizer_loop:record_begin",
    "recognizer_loop:record_end",
    "ovos.utterance.handled",
    "ovos.common_play.shuffle.toggle",
    "ovos.common_play.shuffle.set",
    "ovos.common_play.shuffle.unset",
    "ovos.common_play.repeat.toggle",
    "ovos.common_play.repeat.set",
    "ovos.common_play.repeat.unset",
    "ovos.common_play.like",
    "ovos.common_play.unlike",
}
# Read-only queries, broadcasts the player only reacts to, and the global
# stop stay ungated so a remote pipeline can still read state and stop the
# device.
UNGATED_TOPICS = {
    "ovos.common_play.track.state",
    "ovos.common_play.playback_time",
    "ovos.common_play.skills.detach",
    "ovos.common_play.announce",
    "ovos.common_play.media.state",
    "ovos.common_play.get_track_length",
    "ovos.common_play.get_track_position",
    "ovos.common_play.track_info",
    "ovos.common_play.list_backends",
    "mycroft.stop",
    "ovos.common_play.SEI.get",
    "ovos.common_play.status",
    "ovos.common_play.disambiguation",
    "ovos.common_play.likes",
    "ovos.common_play.mpris.now_playing",
}

# Topics ovos-media deliberately does NOT answer: pipeline-side GUI signals,
# and the player's own state broadcast (set_player_state is its single
# authoritative writer).
UNSUBSCRIBED_TOPICS = [
    "ovos.common_play.home",
    "ovos.common_play.search.start",
    "ovos.common_play.search.end",
    "ovos.common_play.player.state",
]
# Topics ovos-bus-client treats as migrated legacy names: it wraps the
# handler in a per-handler dedup guard, so a handler shared with a
# non-migrated topic becomes un-removable.
MIGRATED_TOPICS = [
    "ovos.audio.output.started",
    "ovos.audio.output.ended",
    "recognizer_loop:record_begin",
    "recognizer_loop:record_end",
    "mycroft.stop",
]

NAN = float("nan")
INF = float("inf")


def _named_session_message(msg_type, session_id="sat-7", data=None):
    msg = Message(msg_type, data or {})
    msg.context["session"] = Session(session_id=session_id).serialize()
    return msg


def _dispatch(api, topic, data=None, message=None):
    """Hand a message straight to the edge's listener for *topic*.

    FakeBus round-trips every payload through strict JSON, which rejects
    NaN/inf outright — a real messagebus does not (``json.dumps`` emits
    ``NaN``/``Infinity`` and ``json.loads`` reads them back), so those
    payloads are delivered to the listener directly.
    """
    message = message or Message(topic, data or {})
    for registered_topic, listener in api._registrations:
        if registered_topic == topic:
            listener(message)


def _make_player(bus):
    from ovos_media.player import OCPMediaPlayer
    with patch("ovos_media.player.AudioService"), \
         patch("ovos_media.player.VideoService"), \
         patch("ovos_media.player.WebService"), \
         patch("ovos_media.player.OcpMprisExporter"), \
         patch("ovos_media.player.Configuration", return_value={"media": {}}), \
         patch("ovos_media.player.OCPMediaCatalog"):
        return OCPMediaPlayer(bus, config={})


def _make_api(bus, gated=True, decoder=None, topic="ovos.common_play.probe"):
    """Return a bus edge holding one synthetic entry, plus its target."""
    from ovos_media.bus.api import BusHandler, OCPBusApi

    target = MagicMock()
    owner = MagicMock()
    owner.validate_source = True

    class _OneEntryApi(OCPBusApi):
        def _build_table(self):
            return [BusHandler(topic, target, decoder=decoder, gated=gated)]

    return _OneEntryApi(bus, player=owner), target


class TestRegistrationTableCompleteness(unittest.TestCase):
    """Every topic in the inventory above is subscribed, and nothing else."""

    def test_player_table_covers_every_owner(self):
        player = _make_player(FakeBus())
        topics = [e.topic for e in player.bus_api.table]

        self.assertEqual(topics,
                         NOW_PLAYING_TOPICS + CATALOG_TOPICS + PLAYER_TOPICS)
        player.shutdown()

    def test_gated_column_matches_the_contract(self):
        """The gated flag is half the table's contract: assert the exact set,
        so a lost gate (a satellite driving the local player) and a spurious
        one (a dropped local command) both fail here."""
        player = _make_player(FakeBus())
        gated = {e.topic for e in player.bus_api.table if e.gated}

        self.assertEqual(gated, GATED_TOPICS)
        player.shutdown()

    def test_ungated_column_matches_the_contract(self):
        player = _make_player(FakeBus())
        ungated = {e.topic for e in player.bus_api.table if not e.gated}

        self.assertEqual(ungated, UNGATED_TOPICS)
        player.shutdown()

    def test_the_play_topic_is_gated_for_both_of_its_targets(self):
        """'ovos.common_play.play' is the one topic with two entries; a gate
        on only one of them would still bleed satellite metadata into the
        local now_playing or start local playback."""
        player = _make_player(FakeBus())
        play_entries = [e for e in player.bus_api.table
                        if e.topic == "ovos.common_play.play"]

        self.assertEqual(len(play_entries), 2)
        self.assertTrue(all(e.gated for e in play_entries))
        player.shutdown()

    def test_the_play_topic_is_decoded_for_both_of_its_targets(self):
        """A decoder on only one entry would let a malformed payload reach
        the other target unvalidated - both must refuse it at the edge."""
        player = _make_player(FakeBus())
        play_entries = [e for e in player.bus_api.table
                        if e.topic == "ovos.common_play.play"]

        self.assertTrue(all(e.decoder is not None for e in play_entries))
        player.shutdown()

    def test_service_topics_are_ungated(self):
        from ovos_media.bus.api import OCPBusApi
        api = OCPBusApi(FakeBus(), service=MagicMock())

        self.assertEqual([e.gated for e in api.table], [False, False])

    def test_now_playing_play_is_dispatched_before_the_player(self):
        """Both subscribe to 'ovos.common_play.play' and the bus dispatches
        in registration order: the metadata update must land before the
        player acts on it."""
        player = _make_player(FakeBus())
        play_targets = [e.target.__name__ for e in player.bus_api.table
                        if e.topic == "ovos.common_play.play"]

        self.assertEqual(play_targets,
                         ["handle_external_play", "handle_play_request"])
        player.shutdown()

    def test_ping_is_answered_with_a_pong(self):
        """A live daemon answers every 'ovos.common_play.ping' with a
        'ovos.common_play.pong' reply."""
        from ovos_media.bus.api import OCPBusApi
        bus = FakeBus()
        api = OCPBusApi(bus, service=MagicMock())
        pongs = []
        bus.on("ovos.common_play.pong", lambda m: pongs.append(m))

        bus.emit(Message("ovos.common_play.ping",
                         context={"source": "remote-client",
                                  "destination": ["OCP"]}))

        self.assertEqual(len(pongs), 1)
        # the pong must be a reply(): its destination is the pinger's
        # source, which is what routes it back to a remote client
        self.assertEqual(pongs[0].context.get("destination"), "remote-client")
        api.shutdown()

    def test_opm_query_reports_the_installed_audio_backends(self):
        """'opm.audio.query' answers with the player's audio backends in
        the shape OPM discovery expects."""
        from ovos_media.bus.api import OCPBusApi
        bus = FakeBus()
        service = MagicMock()
        service.ocp.audio_service.available_backends.return_value = {
            "fake": {"supported_uris": ["file"], "remote": False}}
        api = OCPBusApi(bus, service=service)
        answers = []
        bus.on("opm.audio.query.response", lambda m: answers.append(m))

        bus.emit(Message("opm.audio.query"))

        self.assertEqual(len(answers), 1)
        self.assertEqual(answers[0].data["plugins"], ["fake"])
        self.assertEqual(answers[0].data["configs"],
                         {"fake": {"supported_uris": ["file"], "remote": False}})
        self.assertEqual(answers[0].data["options"], {})
        api.shutdown()

    def test_service_table_covers_its_own_topics(self):
        from ovos_media.bus.api import OCPBusApi
        service = MagicMock()
        api = OCPBusApi(FakeBus(), service=service)

        self.assertEqual([e.topic for e in api.table], SERVICE_TOPICS)

    def test_pipeline_side_signals_are_not_subscribed(self):
        player = _make_player(FakeBus())
        topics = {e.topic for e in player.bus_api.table}

        for topic in UNSUBSCRIBED_TOPICS:
            self.assertNotIn(topic, topics)
        player.shutdown()

    def test_every_entry_is_bound_on_the_bus(self):
        bus = FakeBus()
        player = _make_player(bus)

        self.assertEqual(len(player.bus_api._registrations),
                         len(player.bus_api.table))
        for entry, (topic, _) in zip(player.bus_api.table,
                                     player.bus_api._registrations):
            self.assertEqual(entry.topic, topic)
        for topic in {e.topic for e in player.bus_api.table}:
            expected = sum(1 for e in player.bus_api.table if e.topic == topic)
            self.assertGreaterEqual(len(bus.ee.listeners(topic)), expected,
                                    f"{topic} is not subscribed")
        player.shutdown()


class TestSessionGate(unittest.TestCase):
    """A gated topic acts only on the local/"default" session."""

    def setUp(self):
        SessionManager.sessions = {"default": SessionManager.default_session}

    def test_named_session_does_not_reach_a_gated_target(self):
        api, target = _make_api(FakeBus(), gated=True)

        api.bus.emit(_named_session_message("ovos.common_play.probe"))

        target.assert_not_called()

    def test_default_session_reaches_a_gated_target(self):
        api, target = _make_api(FakeBus(), gated=True)

        api.bus.emit(Message("ovos.common_play.probe"))

        target.assert_called_once()

    def test_named_session_reaches_an_ungated_target(self):
        api, target = _make_api(FakeBus(), gated=False)

        api.bus.emit(_named_session_message("ovos.common_play.probe"))

        target.assert_called_once()

    def test_named_session_reaches_a_gated_target_without_validate_source(self):
        api, target = _make_api(FakeBus(), gated=True)
        api.player.validate_source = False

        api.bus.emit(_named_session_message("ovos.common_play.probe"))

        target.assert_called_once()

    def test_malformed_session_field_defaults_and_passes_gate(self):
        # SESSION-1 §2/§2.1: an empty session_id on a well-formed session
        # object behaves as if the field were omitted and resolves to the
        # "default" session, so the gate opens; a consumer MUST NOT reject
        # a Message over one field's value.
        api, target = _make_api(FakeBus(), gated=True)
        msg = Message("ovos.common_play.probe",
                      context={"session": {"session_id": ""}})

        _dispatch(api, "ovos.common_play.probe", message=msg)  # must not raise

        target.assert_called_once()

    def test_malformed_session_carrier_is_refused_not_raised(self):
        # SESSION-1 §2.5: a session that is not a JSON object is dropped
        # and MUST NOT be defaulted.
        api, target = _make_api(FakeBus(), gated=True)
        msg = Message("ovos.common_play.probe",
                      context={"session": "junk"})

        _dispatch(api, "ovos.common_play.probe", message=msg)  # must not raise

        target.assert_not_called()


class TestDecodeRejection(unittest.TestCase):
    """A payload the decoder refuses never reaches its target, and the edge
    does not raise on it."""

    def test_none_returning_decoder_drops_the_message(self):
        api, target = _make_api(FakeBus(), gated=False,
                                decoder=lambda data: None)

        api.bus.emit(Message("ovos.common_play.probe", {"x": 1}))

        target.assert_not_called()

    def test_raising_decoder_drops_the_message_without_raising(self):
        def _decoder(data):
            raise ValueError("nope")

        api, target = _make_api(FakeBus(), gated=False, decoder=_decoder)

        api.bus.emit(Message("ovos.common_play.probe", {"x": 1}))  # no raise

        target.assert_not_called()

    def test_accepted_payload_reaches_the_target_with_the_message(self):
        api, target = _make_api(FakeBus(), gated=False,
                                decoder=lambda data: data["x"])
        msg = Message("ovos.common_play.probe", {"x": 1})

        api.bus.emit(msg)

        target.assert_called_once()
        self.assertEqual(target.call_args[0][0].data, {"x": 1})

    def test_stateless_track_state_is_dropped_not_raised(self):
        bus = FakeBus()
        player = _make_player(bus)
        player.now_playing.status = TrackState.QUEUED_AUDIO

        bus.emit(Message("ovos.common_play.track.state", {}))  # no raise

        self.assertEqual(player.now_playing.status, TrackState.QUEUED_AUDIO)
        player.shutdown()

    def test_stateless_media_state_is_dropped_not_raised(self):
        bus = FakeBus()
        player = _make_player(bus)
        player.media_state = MediaState.LOADED_MEDIA

        bus.emit(Message("ovos.common_play.media.state", {}))  # no raise

        self.assertEqual(player.media_state, MediaState.LOADED_MEDIA)
        player.shutdown()

    def test_non_list_playlist_payload_keeps_the_current_playlist(self):
        bus = FakeBus()
        player = _make_player(bus)
        player.playlist.add_entry({"uri": "http://a.mp3", "title": "A"})

        bus.emit(Message("ovos.common_play.playlist.set",
                         {"tracks": "not-a-list"}))

        self.assertEqual(len(player.playlist), 1)
        player.shutdown()


class TestSeekRejectsNonFiniteNumbers(unittest.TestCase):
    """NaN/inf are valid floats that pass an isinstance check and then
    poison the position handed to the backend (and raise out of int() in
    the MPRIS/GUI paths). Both seek entry points refuse them."""

    def _seekable_player(self, bus):
        player = _make_player(bus)
        player.seek = MagicMock()
        return player

    def test_nan_seconds_seek_is_refused(self):
        bus = FakeBus()
        player = self._seekable_player(bus)

        _dispatch(player.bus_api, "ovos.common_play.seek", {"seconds": NAN})

        player.seek.assert_not_called()
        player.shutdown()

    def test_inf_seconds_seek_is_refused(self):
        bus = FakeBus()
        player = self._seekable_player(bus)

        _dispatch(player.bus_api, "ovos.common_play.seek", {"seconds": INF})

        player.seek.assert_not_called()
        player.shutdown()

    def test_finite_seconds_seek_still_works(self):
        bus = FakeBus()
        player = self._seekable_player(bus)

        bus.emit(Message("ovos.common_play.seek", {"seekValue": 30000}))

        player.seek.assert_called_once_with(30000)
        player.shutdown()

    def test_nan_track_position_is_refused(self):
        bus = FakeBus()
        player = self._seekable_player(bus)

        _dispatch(player.bus_api, "ovos.common_play.set_track_position",
                  {"position": NAN})

        player.seek.assert_not_called()
        player.shutdown()

    def test_inf_track_position_is_refused(self):
        bus = FakeBus()
        player = self._seekable_player(bus)

        _dispatch(player.bus_api, "ovos.common_play.set_track_position",
                  {"position": INF})

        player.seek.assert_not_called()
        player.shutdown()

    def test_bad_seconds_does_not_cancel_a_valid_absolute_seek(self):
        """The two fields are decoded independently: a refused relative
        offset must not swallow the absolute position in the same payload."""
        bus = FakeBus()
        player = self._seekable_player(bus)

        _dispatch(player.bus_api, "ovos.common_play.seek",
                  {"seconds": NAN, "seekValue": 5000})

        player.seek.assert_called_once_with(5000)
        player.shutdown()

    def test_nan_seek_value_refuses_the_whole_request(self):
        """An absolute seek to a non-finite position must not silently
        degrade into a relative seek somewhere nobody asked for."""
        bus = FakeBus()
        player = self._seekable_player(bus)
        player.now_playing.position = 1000

        _dispatch(player.bus_api, "ovos.common_play.seek",
                  {"seekValue": NAN, "seconds": 10})

        player.seek.assert_not_called()
        player.shutdown()

    def test_inf_seek_value_refuses_the_whole_request(self):
        bus = FakeBus()
        player = self._seekable_player(bus)

        _dispatch(player.bus_api, "ovos.common_play.seek",
                  {"seekValue": INF})

        player.seek.assert_not_called()
        player.shutdown()

    def test_relative_seek_adds_to_the_current_position(self):
        bus = FakeBus()
        player = self._seekable_player(bus)
        player.now_playing.position = 0
        player.audio_service.get_track_position.return_value = 5000

        bus.emit(Message("ovos.common_play.seek", {"seconds": 10}))

        player.seek.assert_called_once_with(15000)
        player.shutdown()

    def test_absolute_seek_to_the_very_start_is_honoured(self):
        """`seekValue: 0` is a position, not a missing field."""
        bus = FakeBus()
        player = self._seekable_player(bus)
        player.audio_service.get_track_position.return_value = 5000

        bus.emit(Message("ovos.common_play.seek", {"seekValue": 0}))

        player.seek.assert_called_once_with(0)
        player.shutdown()

    def test_finite_track_position_still_works(self):
        bus = FakeBus()
        player = self._seekable_player(bus)

        bus.emit(Message("ovos.common_play.set_track_position",
                         {"position": 20000}))

        player.seek.assert_called_once_with(20000)
        player.shutdown()

    def test_direct_call_also_refuses_nan(self):
        """The handler is a callable entry point for out-of-tree callers
        too, so it repeats the check the edge already made."""
        bus = FakeBus()
        player = self._seekable_player(bus)

        player.handle_seek_request(Message("ovos.common_play.seek",
                                           {"seconds": NAN}))
        player.handle_set_track_position_request(
            Message("ovos.common_play.set_track_position", {"position": INF}))

        player.seek.assert_not_called()
        player.shutdown()


class TestTeardown(unittest.TestCase):
    """shutdown() removes exactly what register() added — including the
    migrated legacy topics, whose per-handler dedup guard silently swallows
    a remove() for a handler shared with a non-migrated topic."""

    def test_shutdown_removes_every_registration(self):
        bus = FakeBus()
        player = _make_player(bus)
        topics = {e.topic for e in player.bus_api.table}
        before = {t: len(bus.ee.listeners(t)) for t in topics}

        player.bus_api.shutdown()

        for topic in topics:
            self.assertEqual(len(bus.ee.listeners(topic)),
                             before[topic] - sum(1 for e in player.bus_api.table
                                                 if e.topic == topic),
                             f"{topic} kept a listener after shutdown")
        self.assertEqual(player.bus_api._registrations, [])

    def test_no_listener_object_is_shared_between_topics(self):
        """Per-entry closures are what make the migrated topics removable:
        a bound method reused across topics would put remove() on the dedup
        path for all of them."""
        bus = FakeBus()
        player = _make_player(bus)
        listeners = [listener for _, listener in player.bus_api._registrations]

        self.assertEqual(len({id(fn) for fn in listeners}), len(listeners))
        player.shutdown()

    def test_migrated_topics_stop_being_answered_after_shutdown(self):
        bus = FakeBus()
        player = _make_player(bus)
        player.audio_service = MagicMock()
        player.video_service = MagicMock()
        player.bus_api.shutdown()

        for topic in MIGRATED_TOPICS:
            bus.emit(Message(topic))

        player.audio_service.lower_volume.assert_not_called()
        player.audio_service.restore_volume.assert_not_called()

    def test_plain_duck_topics_stop_being_answered_after_shutdown(self):
        """The plain topics share their target methods with the migrated
        'ovos.audio.output.*' ones — they must still be removable."""
        bus = FakeBus()
        player = _make_player(bus)
        player.audio_service = MagicMock()
        player.bus_api.shutdown()

        bus.emit(Message("ovos.common_play.duck"))
        bus.emit(Message("ovos.common_play.unduck"))

        player.audio_service.lower_volume.assert_not_called()
        player.audio_service.restore_volume.assert_not_called()

    def test_shutdown_is_idempotent(self):
        bus = FakeBus()
        player = _make_player(bus)

        player.bus_api.shutdown()
        player.bus_api.shutdown()  # must not raise

        self.assertEqual(player.bus_api._registrations, [])


if __name__ == "__main__":
    unittest.main()

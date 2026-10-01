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
"""Session carriage for state reports and spoken dialogs (OCP-1 §4/§4.4).

OCPMediaPlayer stashes the triggering 'ovos.common_play.play' Message as
self._play_message at play time (handle_play_request/play_media) and
derives every daemon-originated state report/dialog notification tied to
that playback from it (Message.forward), so a satellite session's play
request is answered on ITS session, not the default one - both on the
player's own media.state/player.state/status emissions and on the
media_backends services' media.state/track.state reports (wired through
play_message_provider, see media_backends/base.py).

A play with no triggering message (self._play_message is None) falls
back to a bare, default-session Message.
"""
import threading
import unittest
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message
from ovos_bus_client.session import Session
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import LoopState, MediaEntry, MediaState, PlaybackType, PlayerState
from ovos_plugin_manager.templates.media import PlaybackEvent

from player_fixture import make_player


SATELLITE_SESSION = "satellite-42"


def _play_message(session_id=SATELLITE_SESSION):
    msg = Message("ovos.common_play.play",
                  {"media": {"uri": "http://example.com/t.mp3", "title": "T",
                            "playback": PlaybackType.AUDIO}})
    msg.context["session"] = Session(session_id=session_id).serialize()
    return msg


def _session_id(message) -> str:
    return message.context.get("session", {}).get("session_id")


class TestPlayerStateReportsCarrySession(unittest.TestCase):
    """set_media_state/set_player_state, and the status broadcast they
    trigger, must carry the stashed play message's session."""

    def test_set_media_state_carries_the_stashed_session(self):
        p = make_player()
        p._play_message = _play_message()
        seen = []
        p.bus.on("ovos.common_play.media.state", lambda m: seen.append(m))

        p.set_media_state(MediaState.LOADED_MEDIA)

        self.assertEqual(len(seen), 1)
        self.assertEqual(_session_id(seen[0]), SATELLITE_SESSION)
        # payload is unchanged by session carriage - only context gained a
        # session
        self.assertEqual(seen[0].data, {"state": MediaState.LOADED_MEDIA})

    def test_set_player_state_carries_the_stashed_session(self):
        p = make_player()
        p._play_message = _play_message()
        seen = []
        p.bus.on("ovos.common_play.player.state", lambda m: seen.append(m))

        p.set_player_state(PlayerState.PLAYING)

        self.assertEqual(len(seen), 1)
        self.assertEqual(_session_id(seen[0]), SATELLITE_SESSION)
        self.assertEqual(seen[0].data, {"state": PlayerState.PLAYING})

    def test_no_play_message_falls_back_to_default_session(self):
        """Fallback: nothing was ever stashed (self._play_message is None) -
        emissions still happen, on a bare, default-session Message."""
        p = make_player()
        self.assertIsNone(p._play_message)
        seen = []
        p.bus.on("ovos.common_play.media.state", lambda m: seen.append(m))

        p.set_media_state(MediaState.LOADED_MEDIA)

        self.assertEqual(len(seen), 1)
        self.assertEqual(_session_id(seen[0]), "default")


class TestHandlePlayRequestStashesTheMessage(unittest.TestCase):
    """handle_play_request/play_media stash the triggering Message so every
    later emission for THIS playback can derive its session from it."""

    def test_handle_play_request_stashes_the_triggering_message(self):
        p = make_player()
        p.play = MagicMock()  # play() drives adapters/backends - not under test here
        msg = _play_message()

        p.handle_play_request(msg)

        self.assertIs(p._play_message, msg)

    def test_play_media_without_a_message_clears_a_stale_stash(self):
        """An in-process play_media() call with no message of its own must
        not let a PREVIOUS play's session leak into this one."""
        p = make_player()
        p.play = MagicMock()
        p._play_message = _play_message()

        p.play_media({"uri": "http://example.com/other.mp3", "title": "X",
                     "playback": PlaybackType.AUDIO})

        self.assertIsNone(p._play_message)


class TestResetClearsTheStash(unittest.TestCase):
    """reset() must clear self._play_message so a LATER, unrelated
    emission does not leak the just-ended playback's session - but the
    reset's OWN state emissions still belong to that playback and must
    still carry it."""

    def test_reset_clears_the_stash_after_its_own_emissions(self):
        p = make_player()
        p._play_message = _play_message()
        # set_player_state(STOPPED) is a no-op if the player is already
        # STOPPED (dedup guard) - start PLAYING so reset()'s own transition
        # actually emits
        p.state = PlayerState.PLAYING
        seen = []
        p.bus.on("ovos.common_play.player.state", lambda m: seen.append(m))

        p.reset()

        # the STOPPED emission reset() itself made still carries the
        # session of the playback just reset
        self.assertGreaterEqual(len(seen), 1)
        self.assertEqual(_session_id(seen[-1]), SATELLITE_SESSION)
        # the stash itself is gone afterwards
        self.assertIsNone(p._play_message)

    def test_no_leak_to_a_later_unrelated_emission(self):
        p = make_player()
        p._play_message = _play_message()
        p.reset()

        seen = []
        p.bus.on("ovos.common_play.player.state", lambda m: seen.append(m))
        p.set_player_state(PlayerState.PLAYING)

        self.assertEqual(len(seen), 1)
        self.assertEqual(_session_id(seen[0]), "default")


class TestStaleStashDoesNotLeakAfterQueueExhaustion(unittest.TestCase):
    """A satellite play's session must not go on tagging reports for
    playback it has nothing to do with, once its own queue has run out or
    an external MPRIS player has taken over."""

    def test_play_next_end_of_queue_clears_the_stash_after_notifying(self):
        p = make_player()
        satellite_msg = _play_message()
        p._play_message = satellite_msg
        p.loop_state = LoopState.NONE
        only = MediaEntry(uri="http://only.mp3", title="Only",
                          playback=PlaybackType.AUDIO)
        p.playlist.add_entry(only)
        p.now_playing.uri = only.uri

        with patch.object(p, "play"):
            p.play_next()

        # the notify still carried the just-ended playback's session...
        p.media.notify_dialog.assert_called_once_with(
            "queue.finished", None, satellite_msg)
        # ...but the stash is cleared once the queue is known exhausted, so
        # a later, unrelated report (eg. an external MPRIS player taking
        # over) does not inherit a dead session
        self.assertIsNone(p._play_message)

    def test_mpris_takeover_clears_the_stash(self):
        p = make_player()
        p._play_message = _play_message()

        p.handle_MPRIS_takeover()

        self.assertIsNone(p._play_message)


class TestBackendServiceStateReportsCarrySession(unittest.TestCase):
    """media_backends.base.BaseMediaService derives its media.state/
    track.state reports from a play_message_provider callback - the least
    invasive way to give the (player-lifetime, track-agnostic) service
    access to the CURRENT playback's originating session without holding a
    reference to the player itself."""

    def _make_svc(self, play_message_provider):
        from ovos_media.media_backends.base import BaseMediaService
        from ovos_utils.process_utils import MonotonicEvent

        bus = FakeBus()
        svc = BaseMediaService.__new__(BaseMediaService)
        svc._init_runtime_state(play_message_provider=play_message_provider)
        svc.bus = bus
        svc.namespace = "audio"
        svc.config = {}
        svc.plugin_loader = lambda: {}
        svc.default = None
        svc.services = []
        svc.current = None
        svc._current_uri = None
        svc.play_start_time = 0
        svc.volume_is_low = False
        svc.service_lock = threading.Lock()
        svc._loaded = MonotonicEvent()
        svc._loaded.set()
        return svc, bus

    def test_track_state_report_carries_the_provided_session(self):
        stash = {"message": _play_message()}
        svc, bus = self._make_svc(lambda: stash["message"])
        backend = MagicMock()
        backend.name = "fake"
        svc.current = backend
        seen = []
        bus.on("ovos.common_play.track.state", lambda m: seen.append(m))

        svc._handle_backend_event(backend, PlaybackEvent.TRACK_START)

        self.assertEqual(len(seen), 1)
        self.assertEqual(_session_id(seen[0]), SATELLITE_SESSION)

    def test_media_state_end_of_media_carries_the_provided_session(self):
        stash = {"message": _play_message()}
        svc, bus = self._make_svc(lambda: stash["message"])
        backend = MagicMock()
        backend.name = "fake"
        svc.current = backend
        svc._current_uri = "http://example.com/t.mp3"
        seen = []
        bus.on("ovos.common_play.media.state", lambda m: seen.append(m))

        svc._handle_backend_event(backend, PlaybackEvent.END_OF_MEDIA,
                                  uri="http://example.com/t.mp3")

        self.assertEqual(len(seen), 1)
        self.assertEqual(_session_id(seen[0]), SATELLITE_SESSION)
        self.assertEqual(seen[0].data, {"state": MediaState.END_OF_MEDIA})

    def test_no_provider_falls_back_to_default_session(self):
        svc, bus = self._make_svc(play_message_provider=None)
        backend = MagicMock()
        backend.name = "fake"
        svc.current = backend
        seen = []
        bus.on("ovos.common_play.track.state", lambda m: seen.append(m))

        svc._handle_backend_event(backend, PlaybackEvent.TRACK_START)

        self.assertEqual(len(seen), 1)
        self.assertEqual(_session_id(seen[0]), "default")

    def test_provider_returning_none_falls_back_to_default_session(self):
        """The provider is consulted fresh on every emit - a play that has
        already ended (provider now returns None) must not keep tagging
        reports with the old session."""
        svc, bus = self._make_svc(lambda: None)
        backend = MagicMock()
        backend.name = "fake"
        svc.current = backend
        seen = []
        bus.on("ovos.common_play.track.state", lambda m: seen.append(m))

        svc._handle_backend_event(backend, PlaybackEvent.TRACK_START)

        self.assertEqual(len(seen), 1)
        self.assertEqual(_session_id(seen[0]), "default")

    def test_mycroft_stop_handled_is_a_bare_acknowledgement(self):
        """mycroft.stop.handled acknowledges the stop REQUEST, not a
        playback report - it stays on the default session even when a play
        message is stashed, unlike the media.state END_OF_MEDIA _perform_stop
        emits alongside it (which DOES carry the play session - a report
        belongs to the playback, not to whoever requested the stop)."""
        stash = {"message": _play_message()}
        svc, bus = self._make_svc(lambda: stash["message"])
        backend = MagicMock()
        backend.name = "fake"
        backend.stop.return_value = True
        svc.current = backend
        svc._current_uri = "http://example.com/t.mp3"
        media_state_seen = []
        stop_handled_seen = []
        bus.on("ovos.common_play.media.state", lambda m: media_state_seen.append(m))
        bus.on("mycroft.stop.handled", lambda m: stop_handled_seen.append(m))

        svc._perform_stop()

        self.assertEqual(len(media_state_seen), 1)
        self.assertEqual(_session_id(media_state_seen[0]), SATELLITE_SESSION)
        self.assertEqual(len(stop_handled_seen), 1)
        self.assertEqual(_session_id(stop_handled_seen[0]), "default")


class TestDialogNotificationCarriesSession(unittest.TestCase):
    """MediaCatalog.notify_dialog's optional *message* reaches the voice
    front-end's speak() as the context source (via dig_for_message), so a
    satellite-triggered play's track.failed/queue.finished dialog is
    spoken back on the session that requested the play - not the default
    one."""

    def test_notify_dialog_passes_message_to_the_listener(self):
        from ovos_media.catalog.catalog import MediaCatalog
        from ovos_media.catalog.likes import LikedSongsStore

        catalog = MediaCatalog(FakeBus(), MagicMock(spec=LikedSongsStore))
        listener = MagicMock()
        catalog.add_dialog_listener(listener)
        msg = _play_message()

        catalog.notify_dialog("track.failed", message=msg)

        listener.assert_called_once_with("track.failed", None, msg)

    def test_notify_dialog_without_a_message_passes_none(self):
        """notify_dialog() always calls the listener with the (dialog,
        data, message) shape - message is None when the caller gave none.
        Only the bundled skill registers a listener, and it accepts
        *message* as its third argument, so there is no 2-arg listener
        left to preserve compatibility for."""
        from ovos_media.catalog.catalog import MediaCatalog
        from ovos_media.catalog.likes import LikedSongsStore

        catalog = MediaCatalog(FakeBus(), MagicMock(spec=LikedSongsStore))
        listener = MagicMock()
        catalog.add_dialog_listener(listener)

        catalog.notify_dialog("track.failed")

        listener.assert_called_once_with("track.failed", None, None)

    def test_skill_speaks_back_on_the_originating_session(self):
        """OCPVoiceSkill.handle_dialog_notification holds *message* as a
        local, named argument - exactly what makes speak_dialog()'s
        underlying speak() (via dig_for_message, which walks the call
        stack for a Message positional arg) find it and carry its session,
        instead of falling back to a bare, default-session Message."""
        from ovos_bus_client.util import dig_for_message
        from ovos_media.catalog.catalog import MediaCatalog
        from ovos_media.catalog.likes import LikedSongsStore
        from ovos_media.skill import OCPVoiceSkill

        catalog = MediaCatalog(FakeBus(), MagicMock(spec=LikedSongsStore))
        msg = _play_message()
        found = {}

        def _fake_speak_dialog(dialog, data=None):
            found["message"] = dig_for_message()

        skill = OCPVoiceSkill.__new__(OCPVoiceSkill)
        # bare __new__ instance - just enough attrs for OVOSSkill.__del__'s
        # own error path to resolve cleanly at garbage collection
        skill.catalog = None
        skill.skill_id = "test.ocp.voice"
        skill.speak_dialog = _fake_speak_dialog
        catalog.add_dialog_listener(skill.handle_dialog_notification)

        catalog.notify_dialog("track.failed", message=msg)

        self.assertIs(found["message"], msg)
        self.assertEqual(_session_id(found["message"]), SATELLITE_SESSION)


class TestQueueExhaustionSpeaksOnTheSatelliteSession(unittest.TestCase):
    """A real, end-to-end drive: a satellite-session play, END_OF_MEDIA
    driven through the real bus to natural queue exhaustion, and the
    queue.finished dialog this reaches (play_next()'s end-of-queue branch,
    via _notify_dialog) spoken back on that SAME session - not on whatever
    session the daemon itself runs under.

    Gutting _notify_dialog back to a plain ``self.media.notify_dialog(
    dialog, data)`` (dropping the message argument) makes this test fail:
    that is this test's fail-before."""

    def test_queue_finished_dialog_carries_the_satellite_session(self):
        from ovos_media.player import OCPMediaPlayer
        from ovos_bus_client.util import dig_for_message

        bus = FakeBus()
        player = OCPMediaPlayer(bus, config={"autoplay": True})
        player.now_playing.extract_stream = lambda **kwargs: None

        found = {}

        def _fake_speak_dialog(dialog, data=None):
            found["message"] = dig_for_message()

        def _listener(dialog, data=None, message=None):
            if dialog == "queue.finished":
                _fake_speak_dialog(dialog, data)

        player.media.add_dialog_listener(_listener)

        # a satellite session's play request - single track, so the very
        # next END_OF_MEDIA exhausts the queue
        track = {"uri": "http://example.com/only.mp3", "title": "Only",
                "playback": PlaybackType.AUDIO}
        play_msg = Message("ovos.common_play.play",
                           {"media": track, "playlist": [track]})
        play_msg.context["session"] = Session(SATELLITE_SESSION).serialize()
        player.validate_source = False
        player.handle_play_request(play_msg)

        with patch.object(player, "play"):
            bus.emit(Message("ovos.common_play.media.state",
                             {"state": MediaState.END_OF_MEDIA}))

        self.assertIn("message", found,
                     "queue.finished was never spoken")
        self.assertEqual(_session_id(found["message"]), SATELLITE_SESSION)
        player.shutdown()


if __name__ == "__main__":
    unittest.main()

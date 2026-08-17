"""Contract tests for the player adapters.

Each adapter must speak the same verbs while driving a very different concrete
player: an OPM backend family through its BaseMediaService, or an OCP skill
through the bus.
"""
import unittest
from unittest.mock import MagicMock

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import TrackState

from ovos_media.media_backends.audio import AudioService
from ovos_media.player.adapters import (OPMBackendAdapter, PlayerAdapter,
                                        SkillPlayerAdapter)


class _FakePlayer:
    """The bits of OCPMediaPlayer an adapter reads."""

    def __init__(self, bus=None, service=None, preferred=None):
        self.bus = bus or FakeBus()
        self.audio_service = service or MagicMock()
        self.now_playing = MagicMock()
        self.now_playing.skill_id = "skill.test"
        self.now_playing.infocard = {"uri": "file://track.mp3"}
        self.now_playing.position = 1000
        self.now_playing.length = 180000
        self._preferred = preferred
        self.preferred_calls = []

    def _resolve_preferred_service(self, service):
        self.preferred_calls.append(service)
        return self._preferred


class _Backend:
    """Minimal stand-in for an OPM MediaBackend."""

    def __init__(self, name, uris, raises=False):
        self.name = name
        self.aliases = [name]
        self._uris = uris
        self._raises = raises
        self.stopped = False

    def supported_uris(self):
        if self._raises:
            raise RuntimeError("plugin is broken")
        return self._uris

    def stop(self):
        self.stopped = True
        return True

    def set_track_start_callback(self, cb):
        pass


def _audio_service(*backends):
    svc = AudioService(FakeBus(), config={}, autoload=False)
    svc.services = list(backends)
    return svc


class TestOPMBackendAdapter(unittest.TestCase):
    def setUp(self):
        self.service = MagicMock()
        self.player = _FakePlayer(service=self.service, preferred="preferred-backend")
        self.adapter = OPMBackendAdapter("opm:audio", self.player, "audio_service")

    def test_identity(self):
        self.assertEqual(self.adapter.id, "opm:audio")

    def test_service_is_resolved_on_every_call(self):
        replacement = MagicMock()
        self.player.audio_service = replacement
        self.adapter.stop()
        replacement.stop.assert_called_once_with()
        self.service.stop.assert_not_called()

    def test_play_passes_the_resolved_preference(self):
        self.adapter.play("file://track.mp3")
        self.service.play.assert_called_once_with(
            "file://track.mp3", preferred_service="preferred-backend")
        self.assertEqual(self.player.preferred_calls, [self.service])

    def test_can_play_passes_the_resolved_preference(self):
        self.service.can_play.return_value = True
        self.assertTrue(self.adapter.can_play("file://track.mp3"))
        self.service.can_play.assert_called_once_with(
            "file://track.mp3", preferred_service="preferred-backend")

    def test_transport_verbs_delegate(self):
        self.adapter.pause()
        self.adapter.resume()
        self.adapter.stop()
        self.adapter.seek(4200)
        self.service.pause.assert_called_once_with()
        self.service.resume.assert_called_once_with()
        self.service.stop.assert_called_once_with()
        self.service.set_track_position.assert_called_once_with(4200)

    def test_position_and_length_delegate(self):
        self.service.get_track_position.return_value = 5
        self.service.get_track_length.return_value = 7
        self.assertEqual(self.adapter.position(), 5)
        self.assertEqual(self.adapter.length(), 7)

    def test_volume_verbs_delegate(self):
        self.adapter.lower_volume()
        self.adapter.restore_volume()
        self.service.lower_volume.assert_called_once_with()
        self.service.restore_volume.assert_called_once_with()

    def test_deactivate_stops_and_clears_the_held_backend(self):
        backend = _Backend("vlc", ["file"])
        self.player.audio_service = _audio_service(backend)
        self.player.audio_service.current = backend
        self.adapter.deactivate()
        self.assertTrue(backend.stopped)
        self.assertIsNone(self.player.audio_service.current)

    def test_deactivate_is_a_noop_without_a_held_backend(self):
        self.player.audio_service = _audio_service()
        self.player.audio_service.current = None
        self.adapter.deactivate()  # must not raise
        self.assertIsNone(self.player.audio_service.current)

    def test_deactivate_clears_even_when_the_backend_raises(self):
        backend = _Backend("vlc", ["file"])
        backend.stop = MagicMock(side_effect=RuntimeError("boom"))
        self.player.audio_service = _audio_service(backend)
        self.player.audio_service.current = backend
        self.adapter.deactivate()
        self.assertIsNone(self.player.audio_service.current)


class TestOPMBackendAdapterRouting(unittest.TestCase):
    """can_play routing goes through the service layer, keeping a raising
    plugin from taking the healthy ones with it."""

    def setUp(self):
        self.broken = _Backend("broken", [], raises=True)
        self.good = _Backend("vlc", ["file"])
        self.player = _FakePlayer(service=_audio_service(self.broken, self.good))
        self.adapter = OPMBackendAdapter("opm:audio", self.player, "audio_service")

    def test_a_raising_backend_does_not_hide_a_healthy_one(self):
        self.assertTrue(self.adapter.can_play("file://track.mp3"))

    def test_unclaimed_uri_is_reported_unplayable(self):
        self.assertFalse(self.adapter.can_play("magnet://whatever"))

    def test_preferred_backend_claims_the_uri_first(self):
        preferred = _Backend("mpv", ["magnet"])
        self.player._preferred = preferred
        self.assertTrue(self.adapter.can_play("magnet://whatever"))


class TestSkillPlayerAdapter(unittest.TestCase):
    def setUp(self):
        self.bus = FakeBus()
        self.emitted = []
        self.bus.on("message", lambda m: self.emitted.append(Message.deserialize(m)))
        self.player = _FakePlayer(bus=self.bus)
        self.adapter = SkillPlayerAdapter(self.player)

    def _types(self):
        return [m.msg_type for m in self.emitted]

    def test_play_emits_the_skill_play_and_track_state(self):
        self.adapter.play("file://track.mp3")
        self.assertEqual(self._types(),
                         ["ovos.common_play.skill.test.play",
                          "ovos.common_play.track.state"])
        self.assertEqual(self.emitted[0].data, {"uri": "file://track.mp3"})
        self.assertEqual(self.emitted[1].data["state"], TrackState.PLAYING_SKILL)

    def test_transport_verbs_emit_per_skill_messages(self):
        self.adapter.pause()
        self.adapter.resume()
        self.adapter.stop()
        self.adapter.next()
        self.adapter.prev()
        self.assertEqual(self._types(), ["ovos.common_play.skill.test.pause",
                                         "ovos.common_play.skill.test.resume",
                                         "ovos.common_play.skill.test.stop",
                                         "ovos.common_play.skill.test.next",
                                         "ovos.common_play.skill.test.prev"])

    def test_verbs_follow_the_current_skill(self):
        self.player.now_playing.skill_id = "other.skill"
        self.adapter.stop()
        self.assertEqual(self._types(), ["ovos.common_play.other.skill.stop"])

    def test_seek_is_dropped_rather_than_emitted(self):
        self.adapter.seek(1000)
        self.assertEqual(self._types(), [])

    def test_can_play_always_claims_the_track(self):
        self.assertTrue(self.adapter.can_play("anything://at.all"))

    def test_position_and_length_come_from_now_playing(self):
        self.assertEqual(self.adapter.position(), 1000)
        self.assertEqual(self.adapter.length(), 180000)

    def test_volume_verbs_are_silent_noops(self):
        self.adapter.lower_volume()
        self.adapter.restore_volume()
        self.adapter.deactivate()
        self.assertEqual(self._types(), [])


class TestAdapterContract(unittest.TestCase):
    def test_every_verb_is_implemented_by_every_adapter(self):
        player = _FakePlayer()
        adapters = [OPMBackendAdapter("opm:audio", player, "audio_service"),
                    SkillPlayerAdapter(player)]
        verbs = ["can_play", "play", "pause", "resume", "stop", "seek",
                 "position", "length", "lower_volume", "restore_volume",
                 "deactivate"]
        for adapter in adapters:
            for verb in verbs:
                self.assertTrue(callable(getattr(adapter, verb)),
                                f"{adapter!r} is missing {verb}")

    def test_the_interface_cannot_be_instantiated(self):
        with self.assertRaises(TypeError):
            PlayerAdapter("nope")


if __name__ == "__main__":
    unittest.main()

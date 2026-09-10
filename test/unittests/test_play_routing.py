"""Tests for which backend a play() lands on.

play() asks the roster for an adapter that claims the uri for the current
PlaybackType, demotes video/web to audio when no backend claims it, and
reports INVALID_MEDIA when nothing at all can play the track.
"""
import unittest
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message
from ovos_utils.ocp import MediaState, PlaybackType

from player_fixture import make_player


class TestPlayerPlaySkillPath(unittest.TestCase):
    """Test play() with SKILL playback type."""

    def test_play_skill_emits_skill_play_message(self):
        """play() with SKILL type should emit ovos.common_play.{skill_id}.play."""
        p = make_player(PlaybackType.SKILL)
        p.now_playing.skill_id = "test.skill"

        emitted = []
        p.bus.on("ovos.common_play.test.skill.play", lambda m: emitted.append(m))

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        self.assertEqual(len(emitted), 1)
        # Emitted data is the infocard dict itself
        self.assertIn("uri", emitted[0].data)


class TestPlayerPlayVideoPath(unittest.TestCase):
    """Test play() with VIDEO playback type."""

    def test_play_video_calls_video_service(self):
        """play() with VIDEO type should call video_service.play()."""
        p = make_player(PlaybackType.VIDEO)

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        p.video_service.play.assert_called_once()

    def test_video_backend_present_still_plays_as_video(self):
        """Control case: a video backend claims the uri -> plays as VIDEO,
        does not fall back to audio."""
        p = make_player(PlaybackType.VIDEO)
        p.video_service.can_play.return_value = True

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        p.video_service.play.assert_called_once()
        p.audio_service.play.assert_not_called()
        self.assertEqual(p.now_playing.playback, PlaybackType.VIDEO)

    def test_no_video_backend_claims_uri_falls_back_to_audio(self):
        """No installed video backend claims the uri (eg. a headless
        install with only audio backends configured), but an audio backend
        does -> play() must degrade to audio instead of dead-ending in
        INVALID_MEDIA."""
        p = make_player(PlaybackType.VIDEO)
        p.video_service.can_play.return_value = False
        p.audio_service.can_play.return_value = True

        emitted = []
        p.bus.on("ovos.common_play.media.state", lambda m: emitted.append(m))

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        p.video_service.play.assert_not_called()
        p.audio_service.play.assert_called_once()
        self.assertEqual(p.now_playing.playback, PlaybackType.AUDIO)
        self.assertEqual(emitted, [],
                         "no INVALID_MEDIA should be emitted when the audio "
                         "fallback succeeds")

    def test_no_backend_claims_uri_at_all_emits_invalid_media(self):
        """Neither a video nor an audio backend claims the uri -> the
        fallback must not swallow a genuine INVALID_MEDIA."""
        p = make_player(PlaybackType.VIDEO)
        p.video_service.can_play.return_value = False
        p.audio_service.can_play.return_value = False

        emitted = []
        p.bus.on("ovos.common_play.media.state", lambda m: emitted.append(m))

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        p.video_service.play.assert_not_called()
        p.audio_service.play.assert_not_called()
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].data["state"], MediaState.INVALID_MEDIA)

    def test_audio_fallback_stops_abandoned_video_backend(self):
        """A video backend is genuinely playing track A (svc.current is set)
        when a NEW play() request for a differently-shaped uri that no
        video backend claims arrives, falling back to audio. The abandoned
        video backend must be stopped and cleared — otherwise track A keeps
        playing on the video backend while the audio fallback starts a
        second stream (double playback)."""
        p = make_player(PlaybackType.VIDEO)
        old_backend = MagicMock()
        p.video_service.current = old_backend
        p.video_service.can_play.return_value = False
        p.audio_service.can_play.return_value = True

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        old_backend.stop.assert_called_once()
        self.assertIsNone(p.video_service.current,
                          "abandoned video backend's `current` must be "
                          "cleared, or a later stray LOADED_MEDIA event "
                          "could revive it")
        p.audio_service.play.assert_called_once()

    def test_normal_video_to_video_switch_does_not_stop_new_backend(self):
        """Control: a normal switch where the video backend DOES claim the
        new uri must not have its `current` stopped/cleared by the
        fallback path (which must not trigger at all here)."""
        p = make_player(PlaybackType.VIDEO)
        old_backend = MagicMock()
        p.video_service.current = old_backend
        p.video_service.can_play.return_value = True

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        old_backend.stop.assert_not_called()
        p.video_service.play.assert_called_once()
        p.audio_service.play.assert_not_called()


class TestPlayerPlayWebviewPath(unittest.TestCase):
    """Test play() with WEBVIEW playback type."""

    def test_play_webview_calls_web_service(self):
        """play() with WEBVIEW type should call web_service.play()."""
        p = make_player(PlaybackType.WEBVIEW)

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        p.web_service.play.assert_called_once()

    def test_no_web_backend_claims_uri_falls_back_to_audio(self):
        """Same fallback as VIDEO, for WEBVIEW."""
        p = make_player(PlaybackType.WEBVIEW)
        p.web_service.can_play.return_value = False
        p.audio_service.can_play.return_value = True

        with patch.object(p, "validate_stream", return_value=True), \
             patch.object(p, "set_player_state"):
            p.play()

        p.web_service.play.assert_not_called()
        p.audio_service.play.assert_called_once()
        self.assertEqual(p.now_playing.playback, PlaybackType.AUDIO)


class TestPlayerListBackendsRequest(unittest.TestCase):
    """Test handle_list_backends_request."""

    def test_list_backends_response(self):
        """handle_list_backends_request should emit response with available_backends."""
        p = make_player()
        p.audio_service.available_backends.return_value = {"vlc": {}}

        received = []
        p.bus.on("ovos.common_play.list_backends.response", lambda m: received.append(m))

        p.handle_list_backends_request(Message("ovos.common_play.list_backends"))

        self.assertEqual(len(received), 1)

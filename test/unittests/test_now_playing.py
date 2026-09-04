"""Tests for NowPlaying — the metadata of the track the player is on.

Covers construction defaults, reset/update, the seekbar sync guards
against non-finite or non-numeric positions, external play payloads, and
the track/media state transitions NowPlaying reacts to.
"""
import unittest
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import (MediaEntry, MediaState, PlayerState, TrackState)

from player_fixture import make_player


class TestNowPlayingInit(unittest.TestCase):
    """NowPlaying.__init__ creates instance with expected defaults."""

    def _make_now_playing(self):
        from ovos_media.player import NowPlaying
        bus = FakeBus()
        with patch("ovos_media.player.now_playing.load_stream_extractors"):
            np = NowPlaying(bus)
        return np, bus

    def test_position_defaults_to_zero(self):
        np, _ = self._make_now_playing()
        self.assertEqual(np.position, 0)

    def test_bus_attribute_set(self):
        np, bus = self._make_now_playing()
        self.assertIs(np.bus, bus)

    def test_original_uri_matches_uri(self):
        np, _ = self._make_now_playing()
        # uri defaults to empty string, original_uri mirrors it
        self.assertEqual(np.original_uri, np.uri)

    def test_as_entry_returns_media_entry(self):
        np, _ = self._make_now_playing()
        entry = np.as_entry()
        self.assertIsInstance(entry, MediaEntry)

    def test_reset_clears_fields(self):
        np, _ = self._make_now_playing()
        np.title = "Something"
        np.artist = "Someone"
        np.reset()
        self.assertEqual(np.title, "")
        self.assertEqual(np.artist, "")
        self.assertEqual(np.position, 0)

    def test_update_with_dict(self):
        np, _ = self._make_now_playing()
        np.update({"title": "New Title", "artist": "New Artist"})
        self.assertEqual(np.title, "New Title")

    def test_update_with_media_entry(self):
        np, _ = self._make_now_playing()
        entry = MediaEntry(title="Entry Title", artist="Entry Artist",
                           uri="http://example.com/a.mp3")
        np.update(entry)
        self.assertEqual(np.title, "Entry Title")

    def test_handle_sync_seekbar_updates_position(self):
        np, _ = self._make_now_playing()
        msg = Message("ovos.common_play.playback_time",
                      {"length": 180000, "position": 45000})
        np.handle_sync_seekbar(msg)
        self.assertEqual(np.position, 45000)
        self.assertEqual(np.length, 180000)

    def test_handle_sync_seekbar_missing_keys_does_not_raise(self):
        np, _ = self._make_now_playing()
        np.position = 1000
        np.length = 2000
        msg = Message("ovos.common_play.playback_time", {})
        np.handle_sync_seekbar(msg)  # must not raise KeyError
        # prior values are kept, not clobbered
        self.assertEqual(np.position, 1000)
        self.assertEqual(np.length, 2000)

    def test_handle_sync_seekbar_none_position_keeps_prior_value(self):
        np, _ = self._make_now_playing()
        np.position = 1000
        msg = Message("ovos.common_play.playback_time",
                      {"length": 180000, "position": None})
        np.handle_sync_seekbar(msg)
        self.assertEqual(np.position, 1000)
        self.assertEqual(np.length, 180000)

    def test_handle_sync_seekbar_str_position_keeps_prior_value(self):
        np, _ = self._make_now_playing()
        np.position = 1000
        msg = Message("ovos.common_play.playback_time",
                      {"length": 180000, "position": "5000"})
        np.handle_sync_seekbar(msg)  # must not string-repeat/overflow
        self.assertEqual(np.position, 1000)
        self.assertEqual(np.length, 180000)

    def test_handle_sync_seekbar_nan_position_ignored_without_raising(self):
        np, _ = self._make_now_playing()
        np.position = 1000
        np.length = 2000
        msg = Message("ovos.common_play.playback_time",
                      {"length": 180000, "position": float("nan")})
        np.handle_sync_seekbar(msg)  # must not raise ValueError
        # valid 'length' still applies; the invalid 'position' is ignored
        self.assertEqual(np.position, 1000)
        self.assertEqual(np.length, 180000)

    def test_handle_sync_seekbar_inf_length_ignored_without_raising(self):
        np, _ = self._make_now_playing()
        np.position = 1000
        np.length = 2000
        msg = Message("ovos.common_play.playback_time",
                      {"length": float("inf"), "position": 45000})
        np.handle_sync_seekbar(msg)  # must not raise OverflowError
        self.assertEqual(np.length, 2000)
        self.assertEqual(np.position, 45000)

    def test_handle_sync_seekbar_neg_inf_ignored_without_raising(self):
        np, _ = self._make_now_playing()
        np.position = 1000
        np.length = 2000
        msg = Message("ovos.common_play.playback_time",
                      {"length": float("-inf"), "position": float("-inf")})
        np.handle_sync_seekbar(msg)  # must not raise
        self.assertEqual(np.length, 2000)
        self.assertEqual(np.position, 1000)

    def test_handle_sync_seekbar_mixed_valid_length_nan_position_no_partial_commit(self):
        # the crash used to happen AFTER a valid field had already been
        # applied via setattr; verify both fields are validated up front
        # and a valid field can commit independently of an invalid one
        np, _ = self._make_now_playing()
        np.position = 999
        np.length = 111
        msg = Message("ovos.common_play.playback_time",
                      {"length": 180000, "position": float("nan")})
        np.handle_sync_seekbar(msg)
        self.assertEqual(np.length, 180000)  # valid field applied
        self.assertEqual(np.position, 999)   # invalid field left untouched

    def test_on_invalid_stream_after_bad_extract_does_not_raise(self):
        p = make_player()
        np, _ = self._make_now_playing()
        np.uri = "ocp://original"
        np.stream_xtract = MagicMock()
        np.stream_xtract.extract_stream.return_value = {"uri": ["bad"]}
        with self.assertRaises(ValueError):
            np.extract_stream()
        p.now_playing = np
        # the retry-guard recovery path must not crash on the (still
        # valid, untouched) uri left behind by the refused extraction
        p.on_invalid_stream()  # must not raise

    def test_handle_external_play_updates_metadata(self):
        np, _ = self._make_now_playing()
        msg = Message("ovos.common_play.play",
                      {"media": {"title": "External Song",
                                 "uri": "http://example.com/e.mp3"}})
        np.handle_external_play(msg)
        self.assertEqual(np.title, "External Song")

    def test_handle_external_play_tracks_only_payload_is_noop(self):
        """A 'tracks'-only payload (no 'media' key) is ignored — the old
        tracks-list compat branch is gone, so this now falls through the
        empty-media guard without updating anything."""
        np, _ = self._make_now_playing()
        np.title = "unchanged"
        msg = Message("ovos.common_play.play",
                      {"tracks": [{"title": "Track 0",
                                   "uri": "http://example.com/t0.mp3"}]})
        np.handle_external_play(msg)  # must not raise
        self.assertEqual(np.title, "unchanged")

    def test_handle_track_state_change_updates_status(self):
        np, _ = self._make_now_playing()
        msg = Message("ovos.common_play.track.state",
                      {"state": int(TrackState.PLAYING_AUDIO)})
        np.handle_track_state_change(msg)
        self.assertEqual(np.status, TrackState.PLAYING_AUDIO)

    def test_handle_track_state_change_raises_on_missing(self):
        np, _ = self._make_now_playing()
        msg = Message("ovos.common_play.track.state", {})
        with self.assertRaises(ValueError):
            np.handle_track_state_change(msg)

    def test_handle_track_state_change_queued_does_not_crash(self):
        """Regression: a non-PLAYING state (QUEUED/…) used to hit a reference to
        the non-existent ``TrackState.PAUSED_AUDIO`` and raise AttributeError."""
        np, _ = self._make_now_playing()
        np._player = MagicMock()
        for st in (TrackState.QUEUED_AUDIO, TrackState.QUEUED_VIDEO,
                   TrackState.QUEUED_AUDIOSERVICE, TrackState.DISAMBIGUATION):
            np.status = TrackState.PLAYING_AUDIO  # force a transition each time
            np.handle_track_state_change(
                Message("ovos.common_play.track.state", {"state": int(st)}))
        # queued/disambiguation never change the player state
        np._player.set_player_state.assert_not_called()

    def test_handle_track_state_change_playing_sets_player_playing(self):
        """Every PLAYING_* track state (incl. AUDIOSERVICE and MPRIS) marks the
        player PLAYING — a paused track stays PLAYING_*, pause is a PlayerState."""
        from ovos_utils.ocp import PlayerState
        for st in (TrackState.PLAYING_AUDIO, TrackState.PLAYING_VIDEO,
                   TrackState.PLAYING_WEBVIEW, TrackState.PLAYING_SKILL,
                   TrackState.PLAYING_AUDIOSERVICE, TrackState.PLAYING_MPRIS):
            np, _ = self._make_now_playing()
            np._player = MagicMock()
            np.status = TrackState.DISAMBIGUATION
            np.handle_track_state_change(
                Message("ovos.common_play.track.state", {"state": int(st)}))
            np._player.set_player_state.assert_called_once_with(PlayerState.PLAYING)

    def test_handle_track_state_change_raises_on_bad_type(self):
        np, _ = self._make_now_playing()
        msg = Message("ovos.common_play.track.state", {"state": "playing"})
        with self.assertRaises(ValueError):
            np.handle_track_state_change(msg)

    def test_handle_track_state_noop_when_same(self):
        np, _ = self._make_now_playing()
        # TrackState defaults to DISAMBIGUATION
        original = np.status
        msg = Message("ovos.common_play.track.state",
                      {"state": int(original)})
        np.handle_track_state_change(msg)
        self.assertEqual(np.status, original)

    def test_handle_media_state_end_triggers_reset(self):
        np, _ = self._make_now_playing()
        np.title = "Something"
        msg = Message("ovos.common_play.media.state",
                      {"state": int(MediaState.END_OF_MEDIA)})
        np.handle_media_state_change(msg)
        # reset() should have cleared title
        self.assertEqual(np.title, "")

    def test_handle_media_state_raises_on_missing(self):
        np, _ = self._make_now_playing()
        msg = Message("ovos.common_play.media.state", {})
        with self.assertRaises(ValueError):
            np.handle_media_state_change(msg)


class TestNowPlayingTrackStateChange(unittest.TestCase):
    """Test NowPlaying.handle_track_state_change with various states."""

    def _make_now_playing(self):
        from ovos_media.player import NowPlaying
        bus = FakeBus()
        with patch("ovos_media.player.now_playing.load_stream_extractors"):
            np = NowPlaying(bus)
        return np, bus

    def test_playing_video_sets_player_state(self):
        """TrackState.PLAYING_VIDEO should set player state to PLAYING."""
        np, bus = self._make_now_playing()
        mock_player = MagicMock()
        np._player = mock_player
        np.status = TrackState.QUEUED_AUDIO  # different state

        np.handle_track_state_change(Message("ovos.common_play.track.state",
                                             {"state": TrackState.PLAYING_VIDEO}))

        mock_player.set_player_state.assert_called_with(PlayerState.PLAYING)

    def test_playing_skill_sets_player_state(self):
        """TrackState.PLAYING_SKILL should set player state to PLAYING."""
        np, bus = self._make_now_playing()
        mock_player = MagicMock()
        np._player = mock_player

        np.handle_track_state_change(Message("ovos.common_play.track.state",
                                             {"state": TrackState.PLAYING_SKILL}))

        mock_player.set_player_state.assert_called_with(PlayerState.PLAYING)

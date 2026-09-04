"""Tests for ovos_media.player.streams.extract_stream.

The extractor turns a ``{SEI}//{uri}`` reference into a real stream. Whatever
it returns is validated BEFORE any of it reaches the entry, so a hostile or
broken plugin cannot poison the uri or the metadata that was already there.
"""
import unittest
from unittest.mock import MagicMock, patch

from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import MediaEntry, PlaybackType

from ovos_media.player import streams

from player_fixture import make_player


class _Entry(MediaEntry):
    """A MediaEntry with the two NowPlaying traits extract_stream relies on:
    an original_uri, and an update() that lets a resolved uri through even in
    newonly mode."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.original_uri = self.uri

    def update(self, entry, skipkeys=None, newonly=False):
        super().update(entry, skipkeys, newonly)
        if newonly and entry.get("uri"):
            super().update({"uri": entry["uri"]})


def _extract(entry, returns, video=False):
    xtract = MagicMock()
    xtract.extract_stream.return_value = returns
    streams.extract_stream(entry, video, xtract)
    return xtract


class TestExtractStream(unittest.TestCase):
    """The module level function, called without a NowPlaying."""

    def test_missing_uri_refuses_before_calling_the_extractor(self):
        xtract = MagicMock()
        with self.assertRaises(ValueError):
            streams.extract_stream(_Entry(uri=""), False, xtract)
        xtract.extract_stream.assert_not_called()

    def test_video_flag_is_forwarded_to_the_extractor(self):
        entry = _Entry(uri="ocp://original", playback=PlaybackType.VIDEO)
        xtract = _extract(entry, {"uri": "http://example.com/x.mp4"}, video=True)
        xtract.extract_stream.assert_called_once_with("ocp://original", True)

    def test_resolved_uri_replaces_the_reference_and_is_remembered(self):
        entry = _Entry(uri="ocp://original")
        _extract(entry, {"uri": "http://example.com/x.mp3", "title": "Song"})
        self.assertEqual(entry.uri, "http://example.com/x.mp3")
        self.assertEqual(entry.original_uri, "ocp://original")
        self.assertEqual(entry.title, "Song")

    def test_empty_extractor_result_keeps_the_entry_and_validates_its_uri(self):
        entry = _Entry(uri="http://example.com/direct.mp3", title="kept")
        _extract(entry, {})
        self.assertEqual(entry.uri, "http://example.com/direct.mp3")
        self.assertEqual(entry.title, "kept")

    def test_entry_uri_without_a_playable_prefix_is_refused(self):
        entry = _Entry(uri="ocp://original")
        with self.assertRaises(ValueError):
            _extract(entry, {})

    def test_metadata_is_not_applied_when_the_resolved_uri_is_refused(self):
        entry = _Entry(uri="http://example.com/ok.mp3", title="original title")
        with self.assertRaises(ValueError):
            _extract(entry, {"uri": ["not", "a", "str"], "title": "poisoned"})
        self.assertEqual(entry.uri, "http://example.com/ok.mp3")
        self.assertEqual(entry.title, "original title")

    def test_resolved_uri_carrying_a_newline_is_refused(self):
        entry = _Entry(uri="http://example.com/ok.mp3")
        with self.assertRaises(ValueError):
            _extract(entry, {"uri": "http://evil.example/\nSet-Cookie: x=1"})
        self.assertEqual(entry.uri, "http://example.com/ok.mp3")

    def test_entry_uri_carrying_a_newline_is_refused_without_an_extractor_result(self):
        entry = _Entry(uri="http://example.com/\nSet-Cookie: x=1")
        with self.assertRaises(ValueError):
            _extract(entry, None)

    def test_scheme_claimed_by_a_loaded_backend_is_accepted(self):
        # library:// is not a builtin prefix, but a backend that claims it
        # via supported_uris() (eg. ovos-media-plugin-mass claiming
        # "library") must not be refused before roster selection even gets
        # a chance to route to it
        entry = _Entry(uri="library://radio/1")
        xtract = MagicMock()
        xtract.extract_stream.return_value = {}
        streams.extract_stream(entry, False, xtract, allowed_schemes={"library"})
        self.assertEqual(entry.uri, "library://radio/1")

    def test_scheme_nobody_claims_is_still_refused(self):
        # the garbage-rejection behaviour is unchanged: a scheme not
        # built-in and not claimed by any loaded backend still drops
        entry = _Entry(uri="bogus://x")
        xtract = MagicMock()
        xtract.extract_stream.return_value = {}
        with self.assertRaises(ValueError):
            streams.extract_stream(entry, False, xtract, allowed_schemes={"library"})

    def test_no_allowed_schemes_behaves_like_before(self):
        entry = _Entry(uri="library://radio/1")
        xtract = MagicMock()
        xtract.extract_stream.return_value = {}
        with self.assertRaises(ValueError):
            streams.extract_stream(entry, False, xtract)

    def test_extractor_loaded_on_demand_when_none_is_given(self):
        entry = _Entry(uri="ocp://original")
        with patch("ovos_media.player.streams.load_stream_extractors") as loader:
            loader.return_value.extract_stream.return_value = {
                "uri": "http://example.com/x.mp3"}
            streams.extract_stream(entry, False)
        loader.assert_called_once()
        self.assertEqual(entry.uri, "http://example.com/x.mp3")


class TestNowPlayingExtractStream(unittest.TestCase):
    """NowPlaying delegates to the module function and keeps its contract."""

    def _make_now_playing(self):
        from ovos_media.player import NowPlaying
        bus = FakeBus()
        with patch("ovos_media.player.now_playing.load_stream_extractors"):
            np = NowPlaying(bus)
        return np, bus

    def test_extract_stream_whitespace_uri_raises_with_bad_value_quoted(self):
        np, _ = self._make_now_playing()
        np.uri = "ocp://original"
        np.title = "original title"
        np.stream_xtract = MagicMock()
        np.stream_xtract.extract_stream.return_value = {"uri": "   ",
                                                         "title": "poisoned"}
        with self.assertRaises(ValueError) as cm:
            np.extract_stream()
        # the raised message quotes the BAD value, not the original uri
        self.assertIn("'   '", str(cm.exception))
        # state must be untouched - no poisoning before the refusal
        self.assertEqual(np.uri, "ocp://original")
        self.assertEqual(np.title, "original title")

    def test_extract_stream_non_string_uri_raises_and_leaves_uri_untouched(self):
        np, _ = self._make_now_playing()
        np.uri = "ocp://original"
        np.stream_xtract = MagicMock()
        # extractor plugin returns a garbage non-string uri
        np.stream_xtract.extract_stream.return_value = {"uri": ["not", "a", "str"]}
        with self.assertRaises(ValueError):
            np.extract_stream()
        # self.uri must not have been poisoned by the garbage value
        self.assertEqual(np.uri, "ocp://original")

    def test_extract_stream_valid_uri_updates_normally(self):
        np, _ = self._make_now_playing()
        np.uri = "ocp://original"
        np.stream_xtract = MagicMock()
        np.stream_xtract.extract_stream.return_value = {"uri": "http://example.com/x.mp3"}
        np.extract_stream()  # must not raise
        self.assertEqual(np.uri, "http://example.com/x.mp3")

    def test_extract_stream_control_char_uri_raises_and_leaves_uri_untouched(self):
        # a uri that passes the prefix check may still embed control
        # characters (eg. a newline for header/log injection) - it must be
        # refused, same as a non-string or whitespace-only uri
        np, _ = self._make_now_playing()
        np.uri = "ocp://original"
        np.title = "original title"
        np.stream_xtract = MagicMock()
        np.stream_xtract.extract_stream.return_value = {
            "uri": "http://evil.example/\nSet-Cookie: x=1",
            "title": "poisoned"}
        with self.assertRaises(ValueError) as cm:
            np.extract_stream()
        self.assertIn("Set-Cookie", str(cm.exception))
        self.assertEqual(np.uri, "ocp://original")
        self.assertEqual(np.title, "original title")

    def test_extract_stream_line_separator_uri_raises_and_leaves_uri_untouched(self):
        # U+2028 (LINE SEPARATOR) is not < 0x20 or 0x7f but acts as a
        # newline-equivalent in log viewers/some HTTP stacks - must be
        # refused like the ASCII control-character case above
        np, _ = self._make_now_playing()
        np.uri = "ocp://original"
        np.title = "original title"
        np.stream_xtract = MagicMock()
        np.stream_xtract.extract_stream.return_value = {
            "uri": "http://evil.example/ Set-Cookie: x=1",
            "title": "poisoned"}
        with self.assertRaises(ValueError) as cm:
            np.extract_stream()
        self.assertIn("Set-Cookie", str(cm.exception))
        self.assertEqual(np.uri, "ocp://original")
        self.assertEqual(np.title, "original title")

    def test_extract_stream_next_line_uri_raises_and_leaves_uri_untouched(self):
        # U+0085 (NEXT LINE) is the same class of newline-equivalent
        np, _ = self._make_now_playing()
        np.uri = "ocp://original"
        np.title = "original title"
        np.stream_xtract = MagicMock()
        np.stream_xtract.extract_stream.return_value = {
            "uri": "http://evil.example/Set-Cookie: x=1",
            "title": "poisoned"}
        with self.assertRaises(ValueError) as cm:
            np.extract_stream()
        self.assertIn("Set-Cookie", str(cm.exception))
        self.assertEqual(np.uri, "ocp://original")
        self.assertEqual(np.title, "original title")

    def test_extract_stream_normal_http_uri_still_passes(self):
        # control: a normal http uri must not be rejected by the widened
        # unicode-category check
        np, _ = self._make_now_playing()
        np.uri = "ocp://original"
        np.stream_xtract = MagicMock()
        np.stream_xtract.extract_stream.return_value = {
            "uri": "http://example.com/song.mp3"}
        np.extract_stream()  # must not raise
        self.assertEqual(np.uri, "http://example.com/song.mp3")

    def test_extract_stream_soft_hyphen_and_zwj_uri_still_passes(self):
        # D5: U+00AD (SOFT HYPHEN) and U+200D (ZERO WIDTH JOINER) are
        # category Cf and appear in real-world filenames - a file:// uri
        # containing them must be accepted, not refused as "injection".
        uri = "file:///music/so­ft‍join.mp3"
        np, _ = self._make_now_playing()
        np.uri = "ocp://original"
        np.stream_xtract = MagicMock()
        np.stream_xtract.extract_stream.return_value = {"uri": uri}
        np.extract_stream()  # must not raise
        self.assertEqual(np.uri, uri)


class TestPlayerValidateStreamException(unittest.TestCase):
    """Test validate_stream with extraction exception."""

    def test_validate_stream_exception_returns_false(self):
        """validate_stream should return False if extract_stream raises."""
        p = make_player()
        p.now_playing.playback = PlaybackType.AUDIO
        p.now_playing.extract_stream = MagicMock(side_effect=Exception("fail"))

        result = p.validate_stream()

        self.assertFalse(result)


class TestPlayerValidateStreamClaimedSchemes(unittest.TestCase):
    """validate_stream() must widen extract_stream's whitelist with every
    scheme a loaded backend across the three services claims."""

    def test_claimed_schemes_from_all_three_services_are_forwarded(self):
        p = make_player()
        p.now_playing.playback = PlaybackType.AUDIO
        p.audio_service.claimed_schemes.return_value = {"library"}
        p.video_service.claimed_schemes.return_value = {"cast"}
        p.web_service.claimed_schemes.return_value = set()

        p.validate_stream()

        p.now_playing.extract_stream.assert_called_once_with(
            allowed_schemes={"library", "cast"})

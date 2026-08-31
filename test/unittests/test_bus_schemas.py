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
"""Unit tests for ovos_media.bus.schemas — the payload validation layer.

Every function here guards a value that arrives off the bus from an
untrusted peer, so each is exercised directly with valid, invalid and
edge-case input rather than only through the handlers that call it.
"""
import unittest

from ovos_utils.ocp import MediaEntry, MediaType, PlaybackType, Playlist

from ovos_media.bus.schemas import (decode_media, decode_playback_time,
                                    flatten_media_types, is_injection_char,
                                    is_real_number, sanitize_nested_playlist,
                                    sanitize_raw_playlist_dicts,
                                    validated_entries)

NAN = float("nan")
INF = float("inf")


def _entry(uri, title="t"):
    return MediaEntry(uri=uri, title=title, playback=PlaybackType.AUDIO)


class TestIsRealNumber(unittest.TestCase):
    """Finite real numbers only: bool is an int subclass but never a
    legitimate ms value, and NaN/inf are valid floats that raise out of
    int() (ValueError/OverflowError) instead of merely being wrong."""

    CASES = [
        (0, True), (1, True), (-1, True), (1.5, True), (-0.0, True),
        (10 ** 30, True),
        (NAN, False), (INF, False), (-INF, False),
        (True, False), (False, False),
        (None, False), ("5", False), ("", False), (b"5", False),
        ([5], False), ({"n": 5}, False), (object(), False),
    ]

    def test_cases(self):
        for value, expected in self.CASES:
            with self.subTest(value=value):
                self.assertEqual(is_real_number(value), expected)


class TestIsInjectionChar(unittest.TestCase):
    """Cc/Zl/Zp act as newline-equivalents in log viewers and HTTP stacks
    and are refused; Cf format chars appear in real filenames and are not."""

    # Cc controls, plus the Zl/Zp line and paragraph separators
    REJECTED = ["\n", "\r", "\t", "\x00", "\x1b", "\x7f", "\x85",
                "\u2028", "\u2029"]
    # printable text, and the Cf format chars that appear in real
    # filenames: soft hyphen, zero-width joiner, BOM, bidi override,
    # zero-width space
    ALLOWED = ["a", "0", " ", "/", ":", "\u00e9", "\u65e5", "\u00ad",
               "\u200d", "\ufeff", "\u202e", "\u200b"]

    def test_rejected(self):
        for c in self.REJECTED:
            with self.subTest(char=repr(c)):
                self.assertTrue(is_injection_char(c))

    def test_allowed(self):
        for c in self.ALLOWED:
            with self.subTest(char=repr(c)):
                self.assertFalse(is_injection_char(c))


class TestDecodePlaybackTime(unittest.TestCase):

    def test_both_fields_valid(self):
        self.assertEqual(decode_playback_time({"length": 1000, "position": 5}),
                         {"length": 1000, "position": 5})

    def test_zero_is_valid(self):
        self.assertEqual(decode_playback_time({"length": 0, "position": 0}),
                         {"length": 0, "position": 0})

    def test_floats_are_truncated_to_int(self):
        decoded = decode_playback_time({"length": 10.9, "position": 5.9})
        self.assertEqual(decoded, {"length": 10, "position": 5})
        self.assertIsInstance(decoded["length"], int)

    def test_missing_fields_are_absent(self):
        self.assertEqual(decode_playback_time({}), {})

    def test_invalid_position_does_not_discard_valid_length(self):
        # each field is decided on its own; the caller applies only what
        # came back, so nothing partial is written for the rejected one
        self.assertEqual(decode_playback_time({"length": 100, "position": "x"}),
                         {"length": 100})

    def test_rejected_values(self):
        for value in (NAN, INF, -INF, -1, -0.5, True, False, None, "5",
                      [1], {"a": 1}):
            with self.subTest(value=value):
                self.assertEqual(
                    decode_playback_time({"length": value, "position": value}),
                    {})


class TestFlattenMediaTypes(unittest.TestCase):
    """A skill can announce media_types as any iterable, or nest them;
    wrapping such a value as [value] makes every membership check False."""

    def test_scalar_is_wrapped(self):
        self.assertEqual(flatten_media_types(MediaType.MUSIC),
                         [MediaType.MUSIC])

    def test_string_is_scalar_not_iterated_character_wise(self):
        self.assertEqual(flatten_media_types("music"), ["music"])
        self.assertEqual(flatten_media_types(b"music"), [b"music"])

    def test_none_is_wrapped(self):
        self.assertEqual(flatten_media_types(None), [None])

    def test_empty_iterables(self):
        for value in ([], set(), (), {}):
            with self.subTest(value=value):
                self.assertEqual(flatten_media_types(value), [])

    def test_flat_iterables(self):
        for value in ([MediaType.MUSIC], (MediaType.MUSIC,),
                      {MediaType.MUSIC}, frozenset({MediaType.MUSIC}),
                      iter([MediaType.MUSIC])):
            with self.subTest(value=type(value).__name__):
                self.assertEqual(flatten_media_types(value),
                                 [MediaType.MUSIC])

    def test_dict_flattens_to_its_keys(self):
        d = {MediaType.ADULT: True, MediaType.MUSIC: True}
        self.assertCountEqual(flatten_media_types(d),
                              [MediaType.ADULT, MediaType.MUSIC])
        self.assertCountEqual(flatten_media_types(d.keys()),
                              [MediaType.ADULT, MediaType.MUSIC])

    def test_generator_is_consumed(self):
        def gen():
            yield MediaType.ADULT
            yield MediaType.MUSIC

        self.assertEqual(flatten_media_types(gen()),
                         [MediaType.ADULT, MediaType.MUSIC])

    def test_nesting_is_recursive(self):
        value = [{MediaType.ADULT}, [(MediaType.MUSIC,), [MediaType.PODCAST]]]
        self.assertCountEqual(
            flatten_media_types(value),
            [MediaType.ADULT, MediaType.MUSIC, MediaType.PODCAST])


class TestValidatedEntries(unittest.TestCase):

    def test_non_list_payload_yields_nothing(self):
        for payload in ("notalist", None, 5, {"uri": "file:///a.mp3"},
                        {MediaType.MUSIC}):
            with self.subTest(payload=payload):
                self.assertEqual(validated_entries(payload), [])

    def test_empty_list(self):
        self.assertEqual(validated_entries([]), [])
        self.assertEqual(validated_entries(()), [])

    def test_dicts_are_deserialized(self):
        entries = validated_entries([{"uri": "file:///a.mp3", "title": "a"}])
        self.assertEqual(len(entries), 1)
        self.assertIsInstance(entries[0], MediaEntry)
        self.assertEqual(entries[0].uri, "file:///a.mp3")

    def test_bad_entry_is_skipped_good_ones_kept(self):
        entries = validated_entries(["notatrack", 5, None,
                                     _entry("file:///ok.mp3")])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].uri, "file:///ok.mp3")

    def test_nan_and_inf_length_are_coerced_to_zero(self):
        # NaN/inf are valid floats and pass a bare isinstance check but
        # must never leak into Playlist.length (sum() over all entries).
        entries = validated_entries([_entry("file:///a.mp3", "a"),
                                     _entry("file:///b.mp3", "b")])
        entries[0].length = NAN
        entries[1].length = INF
        entries = validated_entries(entries)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].length, 0)
        self.assertEqual(entries[1].length, 0)

    def test_garbage_and_none_length_are_coerced_to_zero(self):
        for bad in ("garbage", None, True, [1]):
            with self.subTest(value=bad):
                entry = _entry("file:///a.mp3")
                entry.length = bad
                entry.position = bad
                result = validated_entries([entry])
                self.assertEqual(result[0].length, 0)
                self.assertEqual(result[0].position, 0)

    def test_non_string_uri_is_skipped(self):
        # a non-string 'uri' passes dict2entry's truthiness check and
        # would otherwise reach MediaEntry unvalidated.
        self.assertEqual(validated_entries([{"uri": 123, "title": "a"}]), [])

    def test_empty_string_uri_is_skipped(self):
        self.assertEqual(validated_entries([{"uri": "", "title": "a"}]), [])

    def test_pluginstream_shaped_dict_without_uri_is_kept(self):
        entries = validated_entries([{"extractor_id": "ovos.some.extractor",
                                       "stream": "some_stream_id"}])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].extractor_id, "ovos.some.extractor")

    def test_playlist_shaped_dict_without_uri_is_kept(self):
        entries = validated_entries([{"playlist": [
            {"uri": "file:///a.mp3", "title": "a"}]}])
        self.assertEqual(len(entries), 1)
        self.assertIsInstance(entries[0], Playlist)


class TestDecodeMedia(unittest.TestCase):

    def test_absent_or_empty_media_yields_none(self):
        for data in ({}, {"media": None}, {"media": {}}):
            with self.subTest(data=data):
                self.assertIsNone(decode_media(data))

    def test_non_dict_media_is_refused(self):
        for media in ("file:///a.mp3", ["file:///a.mp3"], 5):
            with self.subTest(media=media):
                self.assertIsNone(decode_media({"media": media}))

    def test_non_string_uri_is_refused(self):
        self.assertIsNone(decode_media({"media": {"uri": 123}}))

    def test_empty_string_uri_is_refused(self):
        self.assertIsNone(decode_media({"media": {"uri": ""}}))

    def test_valid_dict_passes(self):
        media = {"uri": "file:///a.mp3", "title": "a"}
        self.assertEqual(decode_media({"media": media}), media)

    def test_pluginstream_shaped_dict_without_uri_passes(self):
        media = {"extractor_id": "ovos.some.extractor",
                  "stream": "some_stream_id"}
        self.assertEqual(decode_media({"media": media}), media)

    def test_nested_playlist_entries_are_also_sanitized(self):
        # bad values inside a NESTED Playlist's tracks must not reach the
        # outer Playlist.length unsanitized - recurse into nested tracks
        nested = Playlist(title="nested")
        nested.add_entry(_entry("file:///n1.mp3", "n1"))
        nested.add_entry(_entry("file:///n2.mp3", "n2"))
        nested.entries[0].length = INF
        nested.entries[1].length = NAN

        entries = validated_entries([nested])
        self.assertEqual(len(entries), 1)
        self.assertIsInstance(entries[0], Playlist)
        for track in entries[0].entries:
            self.assertEqual(track.length, 0)
        total = entries[0].length
        self.assertTrue(total == total)  # not NaN
        self.assertNotEqual(total, INF)

    def test_playlist_nested_in_playlist_is_sanitized(self):
        # Playlist.entries is a computed property that builds a NEW
        # filtered list and drops nested Playlist members entirely, so
        # recursing over track.entries never reaches a Playlist nested
        # inside a Playlist. The raw list members (Playlist subclasses
        # list) must be walked instead.
        inner = Playlist(title="inner")
        inner.add_entry(_entry("file:///n1.mp3", "n1"))
        inner[0].length = "garbage"
        outer = Playlist(title="outer")
        outer.add_entry(inner)

        entries = validated_entries([outer])
        self.assertEqual(len(entries), 1)
        inner_result = entries[0][0]
        self.assertIsInstance(inner_result, Playlist)
        # inner.length must not raise TypeError from "garbage" + int, and
        # the sanitized value must be visible
        self.assertEqual(inner_result[0].length, 0)
        self.assertEqual(inner_result.length, 0)

    def test_raw_dict_member_garbage_length_is_sanitized(self):
        # Playlist.entries calls dict2entry/MediaEntry.from_dict fresh on
        # every read with NO numeric sanitization - a raw dict appended
        # directly (bypassing add_entry/from_dict) must still be sanitized
        # by walking the raw list members, or Playlist.length's sum()
        # blows up on the unsanitized "garbage" string.
        outer = Playlist(title="outer")
        outer.add_entry(_entry("file:///ok.mp3", "ok"))
        list.append(outer, {"uri": "file://y.mp3", "length": "garbage"})

        result = validated_entries([outer])[0]
        total = result.length  # must not raise
        self.assertIsInstance(total, (int, float))
        self.assertEqual(total, result.entries[0].length)

    def test_raw_dict_member_inf_length_is_coerced_to_zero(self):
        outer = Playlist(title="outer")
        list.append(outer, {"uri": "file://y.mp3", "length": INF})

        result = validated_entries([outer])[0]
        self.assertEqual(list.__getitem__(result, 0)["length"], 0)
        self.assertNotEqual(result.length, INF)

    def test_non_list_playlist_key_on_raw_dict_member_is_left_untouched(self):
        # recursing into ANY truthy "playlist" value raised a TypeError out
        # of the per-track guard, discarding the WHOLE top-level entry.
        # Sanitizing leaves non-list values alone, it does not lose data.
        outer = Playlist(title="outer")
        list.append(outer, {"uri": "file://y.mp3", "length": "garbage",
                            "playlist": 5})

        entries = validated_entries([outer])
        self.assertEqual(len(entries), 1)  # entry survives, not discarded
        raw_member = list.__getitem__(entries[0], 0)
        self.assertEqual(raw_member["playlist"], 5)  # left untouched
        self.assertEqual(raw_member["length"], 0)  # still coerced

    def test_list_playlist_key_on_raw_dict_member_still_recursed(self):
        outer = Playlist(title="outer")
        list.append(outer, {"uri": "file://y.mp3",
                            "playlist": [{"uri": "file://z.mp3",
                                          "length": "garbage"}]})

        result = validated_entries([outer])[0]
        raw_member = list.__getitem__(result, 0)
        self.assertEqual(raw_member["playlist"][0]["length"], 0)

    def test_self_referential_playlist_does_not_recurse_forever(self):
        cycle = Playlist(title="cycle")
        cycle.add_entry(_entry("file:///a.mp3", "a"))
        list.append(cycle, cycle)  # self-reference via raw list append

        entries = validated_entries([cycle])
        self.assertEqual(len(entries), 1)


class TestSanitizeNestedPlaylist(unittest.TestCase):
    """The recursion is also reachable directly, without a visited set."""

    def test_visited_set_defaults_to_empty(self):
        playlist = Playlist(title="p")
        playlist.add_entry(_entry("file:///a.mp3"))
        playlist.entries[0].length = NAN

        sanitize_nested_playlist(playlist)

        self.assertEqual(playlist.entries[0].length, 0)

    def test_mutual_reference_between_two_playlists_terminates(self):
        a = Playlist(title="a")
        b = Playlist(title="b")
        list.append(a, b)
        list.append(b, a)
        list.append(a, {"uri": "file:///x.mp3", "length": "garbage"})

        sanitize_nested_playlist(a)  # must not recurse forever

        self.assertEqual(list.__getitem__(a, 1)["length"], 0)


class TestSanitizeRawPlaylistDicts(unittest.TestCase):

    def test_dict_and_entry_members_are_both_coerced(self):
        entry = _entry("file:///a.mp3")
        entry.position = INF
        members = [entry, {"uri": "file:///b.mp3", "length": "garbage"}]

        sanitize_raw_playlist_dicts(members)

        self.assertEqual(entry.position, 0)
        self.assertEqual(members[1]["length"], 0)

    def test_absent_fields_are_not_added_to_dict_members(self):
        member = {"uri": "file:///a.mp3"}

        sanitize_raw_playlist_dicts([member])

        self.assertEqual(member, {"uri": "file:///a.mp3"})

    def test_self_referential_member_list_terminates(self):
        members = [{"uri": "file:///a.mp3", "length": "garbage"}]
        members[0]["playlist"] = members  # list containing its own owner

        sanitize_raw_playlist_dicts(members)  # must not recurse forever

        self.assertEqual(members[0]["length"], 0)

    def test_nested_playlist_member_is_delegated(self):
        nested = Playlist(title="nested")
        nested.add_entry(_entry("file:///n.mp3"))
        nested.entries[0].length = NAN

        sanitize_raw_playlist_dicts([nested])

        self.assertEqual(nested.entries[0].length, 0)


if __name__ == "__main__":
    unittest.main()

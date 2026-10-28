"""Every locale resource base name must satisfy OVOS-INTENT-2 §2.

    A resource base name MUST consist only of lowercase ASCII letters,
    digits, and underscores, and MUST NOT contain whitespace; file
    extensions are likewise lowercase.

The dialog files were named with dots -- ``now.playing.song.dialog`` -- so
the base name ``now.playing.song`` carried characters the clause does not
allow. Nothing was broken at runtime: the loader resolves a dotted base name
and a lower-case language directory. This is a conformance test, and it
guards the whole tree rather than the one batch that was renamed, so a new
resource added with a dot or a capital fails here rather than shipping.

The language directory names are NOT checked. Architecture ruled on
2026-09-28 that lower-case directories are conformant: §2's tag paragraph
carries no RFC 2119 keyword where the paragraph above it carries two, and
INTENT-1 §3 fixes a language slot's case to LOWER, so `en-US` in §2 is an
illustration rather than a requirement. There is no canonical spelling for a
language directory to be measured against.

The ``.intent`` files are covered too. An intent file's base name becomes the
dispatch topic ``<skill_id>:<intent_name>`` (OVOS-MSG-1 §2.1.1), so renaming
them moved ``ovos-media:WhatSong`` to ``ovos-media:what_song``. The topic FORM
is unchanged and still conformant; only the instance string moved, and a code
search finds no user of the old strings.
"""
import re
import unittest
from pathlib import Path

LOCALE = Path(__file__).parent.parent.parent / "ovos_media" / "locale"

# OVOS-INTENT-2 §2: lowercase ASCII letters, digits and underscores only.
BASE_NAME = re.compile(r"^[a-z0-9_]+$")

EXEMPT_EXTENSIONS = set()

# §2.3 exempts .blacklist and .prompt from parity; neither is used here, but
# the set is named so a later resource of those roles does not red this test.
PARITY_EXEMPT_EXTENSIONS = {"blacklist", "prompt"}


class TestLocaleResourceNames(unittest.TestCase):

    def _resources(self):
        self.assertTrue(LOCALE.is_dir(), f"no locale tree at {LOCALE}")
        found = []
        for path in sorted(LOCALE.rglob("*")):
            if path.is_file():
                found.append(path)
        self.assertTrue(found, "the locale tree is empty")
        return found

    def test_every_base_name_is_lowercase_and_underscored(self):
        bad = []
        for path in self._resources():
            base, _, ext = path.name.rpartition(".")
            if ext in EXEMPT_EXTENSIONS:
                continue
            if not BASE_NAME.match(base):
                bad.append(f"{path.relative_to(LOCALE)} (base name {base!r})")
        self.assertEqual([], bad,
                         "OVOS-INTENT-2 §2: a resource base name must be "
                         "lowercase ASCII letters, digits and underscores")

    def test_every_extension_is_lowercase(self):
        bad = [str(p.relative_to(LOCALE)) for p in self._resources()
               if p.suffix != p.suffix.lower()]
        self.assertEqual([], bad, "OVOS-INTENT-2 §2: extensions are lowercase")

    def test_no_two_files_share_a_base_name_and_extension_in_one_language(self):
        """§2: two files with the same extension MUST NOT share a base name
        anywhere within one language directory tree."""
        for lang_dir in sorted(p for p in LOCALE.iterdir() if p.is_dir()):
            seen = {}
            for path in sorted(lang_dir.rglob("*")):
                if not path.is_file():
                    continue
                key = path.name.lower()
                self.assertNotIn(
                    key, seen,
                    f"{lang_dir.name}: {path} duplicates {seen.get(key)}")
                seen[key] = path


    def test_every_language_ships_the_same_resource_set(self):
        """§2.3: a (role, base name) pair present in any one language
        directory MUST be present in every other. A rename wave that touches
        both languages is exactly where one half slips."""
        by_lang = {}
        for lang_dir in sorted(p for p in LOCALE.iterdir() if p.is_dir()):
            pairs = set()
            for path in lang_dir.rglob("*"):
                if not path.is_file():
                    continue
                base, _, ext = path.name.rpartition(".")
                if ext in PARITY_EXEMPT_EXTENSIONS:
                    continue
                pairs.add((ext, base))
            by_lang[lang_dir.name] = pairs
        self.assertGreater(len(by_lang), 1, "only one language to compare")
        reference = sorted(by_lang)[0]
        for lang, pairs in sorted(by_lang.items()):
            self.assertEqual(
                by_lang[reference], pairs,
                f"{lang} and {reference} do not ship the same resource set")


if __name__ == "__main__":
    unittest.main()

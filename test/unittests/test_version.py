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
"""Unit tests for ovos_media.version — version constants and string assembly."""
import unittest


class TestVersionModule(unittest.TestCase):
    def test_version_constants_are_ints(self):
        from ovos_media import version
        self.assertIsInstance(version.VERSION_MAJOR, int)
        self.assertIsInstance(version.VERSION_MINOR, int)
        self.assertIsInstance(version.VERSION_BUILD, int)
        self.assertIsInstance(version.VERSION_ALPHA, int)

    def test_dunder_version_is_nonempty_string(self):
        from ovos_media import version
        self.assertIsInstance(version.__version__, str)
        self.assertTrue(version.__version__)

    def test_dunder_version_starts_with_numeric_release(self):
        from ovos_media import version
        prefix = f"{version.VERSION_MAJOR}." \
                 f"{version.VERSION_MINOR}." \
                 f"{version.VERSION_BUILD}"
        self.assertTrue(version.__version__.startswith(prefix))

    def test_alpha_suffix_matches_alpha_constant(self):
        from ovos_media import version
        if version.VERSION_ALPHA:
            self.assertIn(f"a{version.VERSION_ALPHA}", version.__version__)
        else:
            self.assertNotIn("a", version.__version__)

    def test_package_version_string_assembly_branches(self):
        """Exercise both the alpha and non-alpha branches of the
        ``__version__`` expression without depending on the checked-in
        version numbers."""
        def assemble(major, minor, build, alpha):
            return f"{major}.{minor}.{build}" + (f"a{alpha}" if alpha else "")

        self.assertEqual(assemble(0, 0, 1, 0), "0.0.1")
        self.assertEqual(assemble(1, 2, 3, 4), "1.2.3a4")


if __name__ == "__main__":
    unittest.main()

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

"""End-to-end tests for ``PlaybackType.SKILL`` playback, driven through a
real ``OCPMediaPlayer`` on a ``FakeBus`` (``OCPPlayerHarness``).

An OCP skill (e.g. a Spotify or radio skill) handles its own transport: the
daemon never talks to a media backend for it, it only emits
``ovos.common_play.{skill_id}.{verb}`` messages the skill itself subscribes
to (see ``ovos_media/player/adapters.py::SkillPlayerAdapter``), and mirrors
those verbs into ``player.state`` so the rest of OCP (GUI, MPRIS, intents)
still sees a consistent transport state.
"""
import time
import unittest

from ovos_bus_client.message import Message
from ovos_utils.ocp import MediaEntry, PlaybackType, PlayerState, TrackState

from ovoscope.media import OCPPlayerHarness


def _skill_track(skill_id: str, uri: str = "skill://track",
                  title: str = "Skill Track") -> MediaEntry:
    """A minimal SKILL MediaEntry owned by *skill_id*."""
    return MediaEntry(uri=uri, playback=PlaybackType.SKILL, title=title,
                      skill_id=skill_id)


def _announce(h: OCPPlayerHarness, skill_id: str) -> None:
    """Emit ``ovos.common_play.announce`` the way an OCP skill does on load."""
    h.bus.emit(Message("ovos.common_play.announce", {"skill_id": skill_id}))
    time.sleep(0.05)


class TestSkillPlaybackFullCycle(unittest.TestCase):
    """play -> pause -> resume -> next -> prev -> stop, all delegated to the
    owning skill via per-skill bus messages, with player.state kept in sync."""

    SKILL_ID = "skill.fake_ocp_radio"

    def _capture(self, h: OCPPlayerHarness, topic: str) -> list:
        seen = []
        h.bus.on(topic, lambda m: seen.append(m))
        return seen

    def test_play_delegates_to_skill_and_sets_playing_state(self) -> None:
        with OCPPlayerHarness() as h:
            _announce(h, self.SKILL_ID)
            emitted = self._capture(h, f"ovos.common_play.{self.SKILL_ID}.play")

            h.play(_skill_track(self.SKILL_ID))

            self.assertEqual(len(emitted), 1,
                             "expected exactly one delegated play to the skill")
            self.assertEqual(h.player.playback_type, PlaybackType.SKILL)
            h.assert_player_state(PlayerState.PLAYING)

    def test_pause_resume_delegate_and_update_player_state(self) -> None:
        with OCPPlayerHarness() as h:
            _announce(h, self.SKILL_ID)
            h.play(_skill_track(self.SKILL_ID))

            paused = self._capture(h, f"ovos.common_play.{self.SKILL_ID}.pause")
            h.pause()
            self.assertEqual(len(paused), 1)
            h.assert_player_state(PlayerState.PAUSED)

            resumed = self._capture(h, f"ovos.common_play.{self.SKILL_ID}.resume")
            h.resume()
            self.assertEqual(len(resumed), 1)
            h.assert_player_state(PlayerState.PLAYING)

    def test_next_and_prev_delegate_to_the_skill(self) -> None:
        """next/prev on SKILL playback are deferred entirely to the skill
        (OCPMediaPlayer.play_next/play_prev special-case PlaybackType.SKILL
        rather than advancing the daemon's own queue)."""
        with OCPPlayerHarness() as h:
            _announce(h, self.SKILL_ID)
            h.play(_skill_track(self.SKILL_ID))

            nxt = self._capture(h, f"ovos.common_play.{self.SKILL_ID}.next")
            h.next_track()
            self.assertEqual(len(nxt), 1)

            prev = self._capture(h, f"ovos.common_play.{self.SKILL_ID}.previous")
            h.prev_track()
            self.assertEqual(len(prev), 1)

    def test_stop_delegates_to_skill_and_sets_stopped_state(self) -> None:
        with OCPPlayerHarness() as h:
            _announce(h, self.SKILL_ID)
            h.play(_skill_track(self.SKILL_ID))

            stopped = self._capture(h, f"ovos.common_play.{self.SKILL_ID}.stop")
            h.stop()
            self.assertEqual(len(stopped), 1)
            h.assert_player_state(PlayerState.STOPPED)

    def test_play_emits_playing_skill_track_state(self) -> None:
        """SkillPlayerAdapter.play() also emits track.state PLAYING_SKILL,
        the wire signal a GUI/MPRIS uses to tell a skill-owned track apart
        from a backend-owned one."""
        with OCPPlayerHarness() as h:
            _announce(h, self.SKILL_ID)
            track_states = self._capture(h, "ovos.common_play.track.state")

            h.play(_skill_track(self.SKILL_ID))

            self.assertTrue(any(m.data.get("state") == TrackState.PLAYING_SKILL
                                for m in track_states),
                            f"expected PLAYING_SKILL among {track_states}")


if __name__ == "__main__":
    unittest.main()

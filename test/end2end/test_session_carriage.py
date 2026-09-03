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

"""End-to-end: a satellite-session play is answered on ITS session.

OCP-1 §4/§4.4 requires every state report to carry a session, and a report
tied to a specific playback must carry that playback's ORIGINATING
session. A HiveMind satellite whose sessions are not NAT'd to "default"
(``media.validate_source: false``) drives ovos-media through
'ovos.common_play.play' stamped with its own session id; the player's
state reports for that playback must come back stamped with the SAME
session id, not the local/default one.

Uses ovoscope.media.OCPPlayerHarness (real OCPMediaPlayer on a FakeBus)
so the message travels through the real 'ovos.common_play.play' bus
subscription (OCPBusApi -> OCPMediaPlayer.handle_play_request), exactly
as it would from a real satellite.
"""
import time
import unittest

from ovos_bus_client.message import Message
from ovos_bus_client.session import Session
from ovos_utils.ocp import MediaEntry, PlaybackType, PlayerState

from ovoscope.media import OCPPlayerHarness

SATELLITE_SESSION = "satellite-42"


def _play_msg(uri: str, session_id: str) -> Message:
    track = MediaEntry(uri=uri, playback=PlaybackType.AUDIO, title="Test Track")
    context = {"session": Session(session_id).serialize()}
    return Message("ovos.common_play.play",
                   {"media": track.as_dict, "playlist": [track.as_dict]},
                   context)


class TestSatelliteSessionPlayIsAnsweredOnItsOwnSession(unittest.TestCase):

    def test_player_state_report_carries_the_satellite_session(self) -> None:
        with OCPPlayerHarness() as h:
            # a satellite embedding ovos-media directly acts on every
            # session, not just "default" (see OCPMediaPlayer.validate_source)
            h.player.validate_source = False

            seen = []
            h.bus.on("ovos.common_play.player.state", lambda m: seen.append(m))

            h.bus.emit(_play_msg("http://example.com/song.mp3", SATELLITE_SESSION))
            time.sleep(0.05)

            h.assert_player_state(PlayerState.PLAYING)
            playing = [m for m in seen if m.data.get("state") == PlayerState.PLAYING]
            self.assertTrue(playing, "no player.state PLAYING report was emitted")
            session = playing[-1].context.get("session", {})
            self.assertEqual(session.get("session_id"), SATELLITE_SESSION)

    def test_status_broadcast_carries_the_satellite_session(self) -> None:
        """The full-status broadcast set_player_state triggers
        (OCP-1 §4.4's daemon-initiated status report) must carry the same
        session as the state change that provoked it."""
        with OCPPlayerHarness() as h:
            h.player.validate_source = False

            seen = []
            h.bus.on("ovos.common_play.status.response", lambda m: seen.append(m))

            h.bus.emit(_play_msg("http://example.com/song.mp3", SATELLITE_SESSION))
            time.sleep(0.05)

            self.assertTrue(seen, "no status broadcast was emitted")
            session = seen[-1].context.get("session", {})
            self.assertEqual(session.get("session_id"), SATELLITE_SESSION)

    def test_default_session_play_still_reports_on_default(self) -> None:
        """Regression guard: a local/default-session play must keep
        reporting on the default session."""
        with OCPPlayerHarness() as h:
            seen = []
            h.bus.on("ovos.common_play.player.state", lambda m: seen.append(m))

            h.bus.emit(_play_msg("http://example.com/song.mp3", "default"))
            time.sleep(0.05)

            h.assert_player_state(PlayerState.PLAYING)
            playing = [m for m in seen if m.data.get("state") == PlayerState.PLAYING]
            self.assertTrue(playing)
            session = playing[-1].context.get("session", {})
            self.assertEqual(session.get("session_id"), "default")


if __name__ == "__main__":
    unittest.main()

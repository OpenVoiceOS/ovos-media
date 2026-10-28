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

"""End-to-end tests for the default/local session filter.

ovos-media is a single player bound to its own device — the ``"default"``
session.  Playback-executing handlers are gated by
``ovos_media.utils.require_default_session``: a command stamped with a
*non-default* session id (e.g. a HiveMind satellite session forwarded by the
server-side OCP pipeline) must be IGNORED, while a ``"default"`` (or
session-less) command must EXECUTE.  When ``validate_source`` is False the
player acts on every session.

Uses ``ovoscope.media.OCPPlayerHarness`` (real ``OCPMediaPlayer`` on a FakeBus
with a MagicMock AudioService) so we can assert both player state and whether
the backend was driven.
"""
import unittest

from ovos_bus_client.message import Message
from ovos_bus_client.session import Session, SessionManager
from ovos_utils.ocp import MediaEntry, PlaybackType, PlayerState

from ovoscope.media import OCPPlayerHarness


def _audio_entry(uri: str, title: str = "Test Track") -> MediaEntry:
    """Return a minimal AUDIO MediaEntry."""
    return MediaEntry(uri=uri, playback=PlaybackType.AUDIO, title=title)


def _play_msg(uri: str, session_id: str = None) -> Message:
    """Build an ``ovos.common_play.play`` message, optionally session-stamped.

    Args:
        uri: track URI to play.
        session_id: if given, stamp ``context["session"]`` with this id; if
            None, the message carries no session (resolves to "default").
    """
    track = _audio_entry(uri)
    context = {}
    if session_id is not None:
        context["session"] = Session(session_id).serialize()
    return Message("ovos.common_play.play",
                   {"media": track.as_dict, "playlist": [track.as_dict]},
                   context)


class TestDefaultSessionFilter(unittest.TestCase):
    """A non-default session is ignored; default / session-less executes."""

    def tearDown(self) -> None:
        # Tests register sessions on the global SessionManager; reset so they
        # do not leak into each other.
        SessionManager.sessions = {"default": SessionManager.get_default_session()}

    def test_default_session_play_executes(self) -> None:
        """A play with session_id 'default' must drive the backend and PLAY."""
        with OCPPlayerHarness() as h:
            h.bus.emit(_play_msg("http://example.com/song.mp3", "default"))
            import time
            time.sleep(0.05)
            h.assert_player_state(PlayerState.PLAYING)
            h.player.audio_service.play.assert_called()
            h.assert_now_playing_uri("http://example.com/song.mp3")

    def test_sessionless_play_executes(self) -> None:
        """A play with no session context resolves to 'default' and PLAYS."""
        with OCPPlayerHarness() as h:
            h.bus.emit(_play_msg("http://example.com/song.mp3", None))
            import time
            time.sleep(0.05)
            h.assert_player_state(PlayerState.PLAYING)
            h.player.audio_service.play.assert_called()

    def test_satellite_session_play_ignored(self) -> None:
        """A play from a non-default session must be IGNORED (no state change,
        no backend call)."""
        with OCPPlayerHarness() as h:
            self.assertTrue(h.player.validate_source)
            h.bus.emit(_play_msg("http://example.com/song.mp3", "satellite-x"))
            import time
            time.sleep(0.05)
            # player stays STOPPED, backend never driven, nothing now-playing
            h.assert_player_state(PlayerState.STOPPED)
            h.player.audio_service.play.assert_not_called()
            self.assertFalse(bool(h.player.now_playing.uri))

    def test_satellite_pause_stop_ignored_while_default_plays(self) -> None:
        """A satellite pause/stop must not disturb default-session playback."""
        with OCPPlayerHarness() as h:
            # default session starts playing
            h.bus.emit(_play_msg("http://example.com/song.mp3", "default"))
            import time
            time.sleep(0.05)
            h.assert_player_state(PlayerState.PLAYING)

            # satellite tries to pause then stop — both ignored
            h.bus.emit(Message("ovos.common_play.pause", {},
                               {"session": Session("satellite-x").serialize()}))
            time.sleep(0.05)
            h.assert_player_state(PlayerState.PLAYING)

            h.bus.emit(Message("ovos.common_play.stop", {},
                               {"session": Session("satellite-x").serialize()}))
            time.sleep(0.05)
            h.assert_player_state(PlayerState.PLAYING)

    def test_default_pause_stop_execute(self) -> None:
        """Default-session pause then stop must execute normally."""
        with OCPPlayerHarness() as h:
            import time
            h.bus.emit(_play_msg("http://example.com/song.mp3", "default"))
            time.sleep(0.05)
            h.bus.emit(Message("ovos.common_play.pause", {},
                               {"session": Session("default").serialize()}))
            time.sleep(0.05)
            h.assert_player_state(PlayerState.PAUSED)
            h.bus.emit(Message("ovos.common_play.stop", {},
                               {"session": Session("default").serialize()}))
            time.sleep(0.05)
            h.assert_player_state(PlayerState.STOPPED)


class TestValidateSourceDisabled(unittest.TestCase):
    """With validate_source=False, a non-default session also executes."""

    def tearDown(self) -> None:
        SessionManager.sessions = {"default": SessionManager.get_default_session()}

    def test_satellite_session_play_executes_when_disabled(self) -> None:
        """A satellite who disables the filter must act on its own session."""
        with OCPPlayerHarness() as h:
            # simulate a satellite that is NOT getting default-NAT'd sessions
            h.player.validate_source = False
            import time
            h.bus.emit(_play_msg("http://example.com/song.mp3", "satellite-x"))
            time.sleep(0.05)
            h.assert_player_state(PlayerState.PLAYING)
            h.player.audio_service.play.assert_called()
            h.assert_now_playing_uri("http://example.com/song.mp3")


if __name__ == "__main__":
    unittest.main()

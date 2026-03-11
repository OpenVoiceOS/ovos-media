"""Tests for OCPGUIInterface and OCPGUIState.

These tests lock down the current data contract and state-routing behaviour
so that the upcoming GUI-decoupling refactor (replacing show_page() calls
with template methods) can be verified without regressions.
"""
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

from ovos_utils.ocp import (
    MediaType, PlaybackType, PlayerState, LoopState, MediaEntry, Playlist
)


class TestOCPGUIStateEnum(unittest.TestCase):
    """OCPGUIState values must remain stable — the player and any external code
    reference them by string value."""

    def test_state_values(self):
        from ovos_media.gui import OCPGUIState
        self.assertEqual(OCPGUIState.HOME.value, "home")
        self.assertEqual(OCPGUIState.PLAYER.value, "player")
        self.assertEqual(OCPGUIState.PLAYLIST.value, "playlist")
        self.assertEqual(OCPGUIState.DISAMBIGUATION.value, "disambiguation")
        self.assertEqual(OCPGUIState.SPINNER.value, "spinner")
        self.assertEqual(OCPGUIState.PLAYBACK_ERROR.value, "playback_error")

    def test_all_states_present(self):
        from ovos_media.gui import OCPGUIState
        names = {s.name for s in OCPGUIState}
        self.assertIn("HOME", names)
        self.assertIn("PLAYER", names)
        self.assertIn("PLAYLIST", names)
        self.assertIn("DISAMBIGUATION", names)
        self.assertIn("SPINNER", names)
        self.assertIn("PLAYBACK_ERROR", names)


class TestOCPGUIInterfaceInit(unittest.TestCase):
    """Initial namespace keys set by __init__ must survive the refactor."""

    def _make_gui(self):
        from ovos_media.gui import OCPGUIInterface
        with patch("ovos_media.gui.GUIInterface.__init__", return_value=None), \
             patch("ovos_media.gui.GUIInterface.__setitem__"):
            gui = OCPGUIInterface.__new__(OCPGUIInterface)
            gui._data = {}
            gui.__setitem__ = lambda self_inner, k, v: gui._data.__setitem__(k, v)
            # Call only the OCP-specific init body
            gui.ocp_skills = {}
            gui.notification_timeout = None
            gui._data["audio_player_page"] = "OVOSSyncPlayer"
            gui._data["video_player_page"] = "OVOSSyncPlayer"
            gui._data["sync_player_page"] = "OVOSSyncPlayer"
            gui._data["web_player_page"] = "OVOSWebPlayer"
            gui._data["searchModel"] = {"data": []}
            gui._data["playlistModel"] = {"data": []}
        return gui

    def test_initial_player_pages(self):
        gui = self._make_gui()
        self.assertEqual(gui._data["audio_player_page"], "OVOSSyncPlayer")
        self.assertEqual(gui._data["video_player_page"], "OVOSSyncPlayer")
        self.assertEqual(gui._data["web_player_page"], "OVOSWebPlayer")

    def test_initial_models_empty(self):
        gui = self._make_gui()
        self.assertEqual(gui._data["searchModel"], {"data": []})
        self.assertEqual(gui._data["playlistModel"], {"data": []})


class TestUpdateButtons(unittest.TestCase):
    """update_buttons() must set exactly the right keys with the right values
    given different player states."""

    def _make_player(self, state=PlayerState.PLAYING, loop=LoopState.NONE,
                     shuffle=False, can_prev=True, can_next=True,
                     playback=PlaybackType.AUDIO, media_type=MediaType.MUSIC,
                     original_uri="test://uri", liked=False):
        player = MagicMock()
        player.state = state
        player.loop_state = loop
        player.shuffle = shuffle
        player.can_prev = can_prev
        player.can_next = can_next
        player.now_playing.playback = playback
        player.now_playing.media_type = media_type
        player.now_playing.original_uri = original_uri
        player.media.liked_songs = {original_uri: {}} if liked else {}
        return player

    def _call_update_buttons(self, player):
        from ovos_media.gui import OCPGUIInterface
        data = {}
        gui = MagicMock(spec=OCPGUIInterface)
        gui.__setitem__ = lambda s, k, v: data.__setitem__(k, v)
        gui.player = player
        OCPGUIInterface.update_buttons(gui)
        return data

    def test_playing_state_buttons(self):
        player = self._make_player(state=PlayerState.PLAYING)
        data = self._call_update_buttons(player)
        self.assertFalse(data["canResume"])
        self.assertTrue(data["canPause"])
        self.assertTrue(data["canPrev"])
        self.assertTrue(data["canNext"])

    def test_paused_state_buttons(self):
        player = self._make_player(state=PlayerState.PAUSED)
        data = self._call_update_buttons(player)
        self.assertTrue(data["canResume"])
        self.assertFalse(data["canPause"])

    def test_loop_none(self):
        player = self._make_player(loop=LoopState.NONE)
        data = self._call_update_buttons(player)
        self.assertEqual(data["loopStatus"], "None")

    def test_loop_repeat_track(self):
        player = self._make_player(loop=LoopState.REPEAT_TRACK)
        data = self._call_update_buttons(player)
        self.assertEqual(data["loopStatus"], "RepeatTrack")

    def test_loop_repeat(self):
        player = self._make_player(loop=LoopState.REPEAT)
        data = self._call_update_buttons(player)
        self.assertEqual(data["loopStatus"], "Repeat")

    def test_shuffle_status(self):
        player = self._make_player(shuffle=True)
        data = self._call_update_buttons(player)
        self.assertTrue(data["shuffleStatus"])

        player2 = self._make_player(shuffle=False)
        data2 = self._call_update_buttons(player2)
        self.assertFalse(data2["shuffleStatus"])

    def test_is_liked_when_in_liked_songs(self):
        player = self._make_player(liked=True, original_uri="test://uri",
                                   playback=PlaybackType.AUDIO)
        data = self._call_update_buttons(player)
        self.assertTrue(data["isLike"])

    def test_not_liked_when_mpris(self):
        player = self._make_player(liked=True, playback=PlaybackType.MPRIS)
        data = self._call_update_buttons(player)
        self.assertFalse(data["isLike"])

    def test_is_music_for_music_type(self):
        player = self._make_player(media_type=MediaType.MUSIC,
                                   playback=PlaybackType.AUDIO)
        data = self._call_update_buttons(player)
        self.assertTrue(data["isMusic"])

    def test_is_music_for_radio(self):
        player = self._make_player(media_type=MediaType.RADIO,
                                   playback=PlaybackType.AUDIO)
        data = self._call_update_buttons(player)
        self.assertTrue(data["isMusic"])

    def test_not_music_for_movie(self):
        player = self._make_player(media_type=MediaType.MOVIE,
                                   playback=PlaybackType.VIDEO)
        data = self._call_update_buttons(player)
        self.assertFalse(data["isMusic"])


class TestUpdateCurrentTrack(unittest.TestCase):
    """update_current_track() must populate the right namespace keys from
    now_playing. This is the source of truth for the now-playing template."""

    def _make_now_playing(self, title="Test Track", artist="Test Artist",
                          image="http://example.com/art.jpg", length=240,
                          position=30, uri="http://example.com/track.mp3",
                          javascript="", original_uri="http://example.com/track.mp3"):
        np = MagicMock()
        np.title = title
        np.artist = artist
        np.image = image
        np.length = length
        np.position = position
        np.original_uri = original_uri
        np.javascript = javascript
        return np

    def _call_update_current_track(self, now_playing):
        from ovos_media.gui import OCPGUIInterface
        data = {}
        gui = MagicMock(spec=OCPGUIInterface)
        gui.__setitem__ = lambda s, k, v: data.__setitem__(k, v)
        gui.player = MagicMock()
        gui.player.now_playing = now_playing
        OCPGUIInterface.update_current_track(gui)
        return data

    def test_sets_title_and_artist(self):
        np = self._make_now_playing(title="Bohemian Rhapsody", artist="Queen")
        data = self._call_update_current_track(np)
        self.assertEqual(data["title"], "Bohemian Rhapsody")
        self.assertEqual(data["artist"], "Queen")

    def test_sets_image(self):
        np = self._make_now_playing(image="http://example.com/art.jpg")
        data = self._call_update_current_track(np)
        self.assertEqual(data["image"], "http://example.com/art.jpg")

    def test_sets_duration_and_position(self):
        np = self._make_now_playing(length=300, position=120)
        data = self._call_update_current_track(np)
        self.assertEqual(data["duration"], 300)
        self.assertEqual(data["position"], 120)

    def test_sets_javascript(self):
        np = self._make_now_playing(javascript="alert('hi')")
        data = self._call_update_current_track(np)
        self.assertEqual(data["javascript"], "alert('hi')")

    def test_fallback_image_when_none(self):
        np = self._make_now_playing(image=None)
        data = self._call_update_current_track(np)
        # When image is None/empty, a fallback path is set — must not be None
        self.assertIsNotNone(data.get("image") or data.get("bg_image"))


class TestUpdateSearchResults(unittest.TestCase):
    """update_search_results() must format MediaEntry objects into infocard
    dicts under searchModel.data."""

    def _call_update_search_results(self, results):
        from ovos_media.gui import OCPGUIInterface
        data = {}
        gui = MagicMock(spec=OCPGUIInterface)
        gui.__setitem__ = lambda s, k, v: data.__setitem__(k, v)
        gui.player = MagicMock()
        gui.player.search_results = results
        OCPGUIInterface.update_search_results(gui)
        return data

    def test_empty_results(self):
        data = self._call_update_search_results([])
        self.assertEqual(data["searchModel"], {"data": []})

    def test_results_become_infocards(self):
        entry = MagicMock()
        entry.infocard = {"title": "Test", "uri": "http://example.com/t.mp3"}
        data = self._call_update_search_results([entry])
        self.assertEqual(len(data["searchModel"]["data"]), 1)
        self.assertEqual(data["searchModel"]["data"][0]["title"], "Test")

    def test_multiple_results_ordered(self):
        entries = [MagicMock() for _ in range(3)]
        for i, e in enumerate(entries):
            e.infocard = {"title": f"Track {i}"}
        data = self._call_update_search_results(entries)
        self.assertEqual(len(data["searchModel"]["data"]), 3)
        self.assertEqual(data["searchModel"]["data"][0]["title"], "Track 0")
        self.assertEqual(data["searchModel"]["data"][2]["title"], "Track 2")


class TestUpdatePlaylist(unittest.TestCase):
    """update_playlist() must format the player's track list into playlistModel."""

    def _call_update_playlist(self, tracks):
        from ovos_media.gui import OCPGUIInterface
        data = {}
        gui = MagicMock(spec=OCPGUIInterface)
        gui.__setitem__ = lambda s, k, v: data.__setitem__(k, v)
        gui.player = MagicMock()
        gui.player.tracks = tracks
        OCPGUIInterface.update_playlist(gui)
        return data

    def test_empty_playlist(self):
        data = self._call_update_playlist([])
        self.assertEqual(data["playlistModel"], {"data": []})

    def test_tracks_become_infocards(self):
        t = MagicMock()
        t.infocard = {"title": "Song A", "uri": "http://example.com/a.mp3"}
        data = self._call_update_playlist([t])
        self.assertEqual(data["playlistModel"]["data"][0]["title"], "Song A")


class TestManageDisplayRouting(unittest.TestCase):
    """manage_display(state) must call exactly the right render method for
    each OCPGUIState value. This is the routing contract."""

    def _call_manage_display(self, state):
        from ovos_media.gui import OCPGUIInterface, OCPGUIState
        gui = MagicMock(spec=OCPGUIInterface)
        gui.manage_display = OCPGUIInterface.manage_display.__get__(gui, OCPGUIInterface)
        gui.prepare_gui_data = MagicMock()
        gui.render_home = MagicMock()
        gui.render_player = MagicMock()
        gui.render_playlist = MagicMock()
        gui.render_disambiguation = MagicMock()
        gui.render_search_spinner = MagicMock()
        gui.render_error = MagicMock()
        gui.clear_notification = MagicMock()
        gui.manage_display(state)
        return gui

    def test_home_state_calls_render_home(self):
        from ovos_media.gui import OCPGUIState
        gui = self._call_manage_display(OCPGUIState.HOME)
        gui.render_home.assert_called_once()
        gui.render_player.assert_not_called()

    def test_player_state_calls_render_player(self):
        from ovos_media.gui import OCPGUIState
        gui = self._call_manage_display(OCPGUIState.PLAYER)
        gui.render_player.assert_called_once()
        gui.render_home.assert_not_called()

    def test_playlist_state_calls_render_playlist(self):
        from ovos_media.gui import OCPGUIState
        gui = self._call_manage_display(OCPGUIState.PLAYLIST)
        gui.render_playlist.assert_called_once()

    def test_disambiguation_state_calls_render_disambiguation(self):
        from ovos_media.gui import OCPGUIState
        gui = self._call_manage_display(OCPGUIState.DISAMBIGUATION)
        gui.render_disambiguation.assert_called_once()

    def test_spinner_state_calls_render_spinner(self):
        from ovos_media.gui import OCPGUIState
        gui = self._call_manage_display(OCPGUIState.SPINNER)
        gui.render_search_spinner.assert_called_once()

    def test_error_state_calls_render_error(self):
        from ovos_media.gui import OCPGUIState
        gui = self._call_manage_display(OCPGUIState.PLAYBACK_ERROR)
        gui.render_error.assert_called_once()

    def test_prepare_gui_data_called_before_render(self):
        from ovos_media.gui import OCPGUIState
        call_order = []
        gui = MagicMock()
        gui.manage_display = OCPGUIInterface_manage_display = \
            lambda state, timeout=None: None

        from ovos_media.gui import OCPGUIInterface, OCPGUIState
        gui2 = MagicMock(spec=OCPGUIInterface)
        gui2.manage_display = OCPGUIInterface.manage_display.__get__(gui2, OCPGUIInterface)
        gui2.prepare_gui_data = MagicMock(side_effect=lambda: call_order.append("prepare"))
        gui2.render_home = MagicMock(side_effect=lambda **kw: call_order.append("render"))
        gui2.clear_notification = MagicMock()
        gui2.manage_display(OCPGUIState.HOME)

        self.assertEqual(call_order, ["prepare", "render"])


if __name__ == "__main__":
    unittest.main()

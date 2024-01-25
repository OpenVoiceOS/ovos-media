import enum
from os.path import join, dirname
from threading import Timer

from ovos_bus_client.apis.gui import GUIInterface
from ovos_utils.ocp import *

from ovos_config import Configuration


class OCPGUIState(str, enum.Enum):
    HOME = "home"
    PLAYER = "player"  # show playback metadata
    PLAYLIST = "playlist"
    DISAMBIGUATION = "disambiguation"
    SPINNER = "spinner"
    PLAYBACK_ERROR = "playback_error"


class OCPGUIInterface(GUIInterface):
    def __init__(self):
        # the skill_id is chosen so the namespace matches the regular bus api
        # ie, the gui event "XXX" is sent in the bus as "ovos.common_play.XXX"
        super(OCPGUIInterface, self).__init__(skill_id=OCP_ID,
                                              ui_directories={"qt5": f"{dirname(__file__)}/qt5"})
        self.ocp_skills = {}  # skill_id: meta

        self.active_extension = Configuration().get("gui", {}).get("extension", "generic")
        self.notification_timeout = None
        self.search_mode_is_app = False
        self.persist_home_display = False
        self.state = OCPGUIState.SPINNER


    def bind(self, player):
        self.player = player
        super().set_bus(self.bus)
        self.player.add_event("ovos.common_play.playback_time",
                              self.handle_sync_seekbar)
        self.player.add_event('ovos.common_play.playlist.play',
                              self.handle_play_from_playlist)
        self.player.add_event('ovos.common_play.search.play',
                              self.handle_play_from_search)
        self.player.add_event('ovos.common_play.skill.play',
                              self.handle_play_skill_featured_media)

    def release(self):
        self.clear()
        super().release()

    # OCPMediaPlayer interface
    def update_ocp_skills(self):
        skills_cards = [
            {"skill_id": skill["skill_id"],
             "title": skill["skill_name"],
             "image": skill["thumbnail"],
             "media_type": skill.get("media_type") or [MediaType.GENERIC]
             } for skill in self.player.media.get_featured_skills()]
        self["skillCards"] = skills_cards

    def update_seekbar_capabilities(self):
        self["canResume"] = True
        self["canPause"] = True
        self["canPrev"] = self.player.can_prev
        self["canNext"] = self.player.can_next

        if self.player.loop_state == LoopState.NONE:
            self["loopStatus"] = "None"
        elif self.player.loop_state == LoopState.REPEAT_TRACK:
            self["loopStatus"] = "RepeatTrack"
        elif self.player.loop_state == LoopState.REPEAT:
            self["loopStatus"] = "Repeat"

        if self.player.now_playing.playback == PlaybackType.MPRIS:
            self["loopStatus"] = "None"
            self["shuffleStatus"] = False
        else:
            self["shuffleStatus"] = self.player.shuffle

    def update_current_track(self):
        self.update_seekbar_capabilities()

        self["media"] = self.player.now_playing.infocard
        self["uri"] = self.player.now_playing.uri
        self["title"] = self.player.now_playing.title
        self["image"] = self.player.now_playing.image or \
                        join(dirname(__file__), "res/qt5/images/ocp.png")
        self["artist"] = self.player.now_playing.artist
        self["bg_image"] = self.player.now_playing.image or \
                           join(dirname(__file__), "res/qt5/images/ocp_bg.png")
        self["duration"] = self.player.now_playing.length
        self["position"] = self.player.now_playing.position
        # options below control the web player
        # javascript can be executed on page load and page behaviour modified
        # default values provide crude protection against ads and popups
        # TODO default permissive or restrictive?
        self["javascript"] = self.player.now_playing.javascript
        self["javascriptCanOpenWindows"] = False  # TODO allow to be defined per track
        self["allowUrlChange"] = False  # TODO allow to be defined per track

    def update_search_results(self):
        self["searchModel"] = {
            "data": [e.infocard for e in self.player.disambiguation]
        }

    def update_playlist(self):
        self["playlistModel"] = {
            "data": [e.infocard for e in self.player.tracks]
        }

    # GUI
    def manage_display(self, state: OCPGUIState, timeout=None):
        # handle any state management needed before render
        if state == OCPGUIState.HOME:
            self.prepare_home()
            self.render_home()
        elif state == OCPGUIState.PLAYER:
            self.prepare_playlist()
            self.prepare_search()
            self.prepare_player()
            self.render_player()
        elif state == OCPGUIState.PLAYLIST:
            self.prepare_playlist()
            if self.state != state:
                self.render_playlist(timeout)
        elif state == OCPGUIState.DISAMBIGUATION:
            self.prepare_search()
            if self.state != state:
                self.render_disambiguation(timeout)
        elif state == OCPGUIState.SPINNER:
            self.render_search_spinner()
        elif state == OCPGUIState.PLAYBACK_ERROR:
            self.render_playback_error()
        self.state = state

    def remove_homescreen(self):
        self.release()

    # OCP pre-rendering
    def prepare_home(self):
        self.persist_home_display = True
        self.update_ocp_skills()  # populate self["skillCards"]

    def prepare_player(self):
        self.persist_home_display = True
        self.remove_search_spinner()
        self.clear_notification()
        self.update_current_track()  # populate now_playing metadata

    def prepare_playlist(self):
        self.update_playlist()  # populate self["playlistModel"]

    def prepare_search(self):
        self.update_search_results()  # populate self["searchModel"]

    # OCP rendering
    def render_pages(self, timeout=None, index=0):
        pages = ["Home", "OVOSSyncPlayer", "PlaylistView"]
        self.show_pages(pages, index,
                        override_idle=timeout or True,
                        override_animations=True)

    def render_home(self):
        self["homepage_index"] = 0
        self["displayBottomBar"] = False
        # Check if the skills page has anything to show, only show it if it does
        if self["skillCards"]:
            self["displayBottomBar"] = True
        self.render_pages(index=0)

    def render_player(self):
        self.send_event("ocp.gui.hide.busy.overlay")  # remove search spinner

        self.render_pages(index=1)

        if len(self.player.tracks):
            self.send_event("ocp.gui.show.suggestion.view.playlist")
        elif len(self.player.disambiguation):
            self.send_event("ocp.gui.show.suggestion.view.disambiguation")

    def render_playlist(self, timeout=None):
        self.render_pages(timeout, index=2)
        self.send_event("ocp.gui.show.suggestion.view.playlist")

    def render_disambiguation(self, timeout=None):
        self.render_pages(timeout, index=2)
        self.send_event("ocp.gui.show.suggestion.view.disambiguation")

    def render_playback_error(self):
        self.display_notification("Sorry, An error occurred while playing media")
        self["footer_text"] = "Sorry, An error occurred while playing media"
        self.remove_search_spinner()

    def render_search_spinner(self, persist_home=False):
        self.persist_home_display = persist_home
        self.display_notification("Searching...Your query is being processed")
        self["footer_text"] = "Querying Skills\n\n"
        self.send_event("ocp.gui.show.busy.overlay")

    def remove_search_spinner(self):
        self.send_event("ocp.gui.hide.busy.overlay")
        self.start_timeout_notification()

    # notification / spinner
    def display_notification(self, text, style="info"):
        """ Display a notification on the screen instead of spinner on platform that support it """
        self.show_controlled_notification(text, style=style)
        self.reset_timeout_notification()

    def clear_notification(self):
        """ Remove the notification on the screen """
        if self.notification_timeout:
            self.notification_timeout.cancel()
        self.remove_controlled_notification()

    def start_timeout_notification(self):
        """ Remove the notification on the screen after 1 minute of inactivity """
        self.notification_timeout = Timer(60, self.clear_notification).start()

    def reset_timeout_notification(self):
        """ Reset the timer to remove the notification """
        if self.notification_timeout:
            self.notification_timeout.cancel()
        self.start_timeout_notification()

    # gui <-> playlists
    def handle_play_from_playlist(self, message):
        LOG.debug("Playback requested from playlist results")
        media = message.data["playlistData"]
        for track in self.player.playlist:
            if track == media:  # found track
                self.player.play_media(track)
                break
        else:
            LOG.error("Track is not part of loaded playlist!")

    def handle_play_from_search(self, message):
        LOG.debug("Playback requested from search results")
        media = message.data["playlistData"]
        for track in self.player.disambiguation:
            if track == media:  # found track
                self.player.play_media(track)
                break
        else:
            LOG.error("Track is not part of search results!")

    def handle_play_skill_featured_media(self, message):
        skill_id = message.data["skill_id"]
        LOG.debug(f"Featured Media request: {skill_id}")
        playlist = message.data["playlist"]

        self.player.playlist.clear()
        self.player.media.replace(playlist)

        self.manage_display(OCPGUIState.DISAMBIGUATION)

    # player -> gui
    def handle_sync_seekbar(self, message):
        """ event sent by ovos audio_only backend plugins """
        self["length"] = message.data["length"]
        self["position"] = message.data["position"]

    def handle_end_of_playback(self, message=None):
        show_results = False
        try:
            if len(self["searchModel"]["data"]):
                show_results = True
        except:
            pass

        # show search results, release screen after 60 seconds
        if show_results:
            self.manage_display(OCPGUIState.PLAYLIST, timeout=60)

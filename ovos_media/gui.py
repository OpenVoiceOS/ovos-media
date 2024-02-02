import enum
import random
from os.path import join, dirname
from threading import Timer

from ovos_bus_client.apis.gui import GUIInterface
from ovos_utils.ocp import *


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
        self.notification_timeout = None

        # other components may interact with this via their own
        # GUIInterface if they share OCP_ID
        self["audio_player_page"] = "OVOSSyncPlayer"
        self["video_player_page"] = "OVOSSyncPlayer"
        self["sync_player_page"] = "OVOSSyncPlayer"
        self["web_player_page"] = "OVOSWebPlayer"
        self["searchModel"] = {"data": []}
        self["playlistModel"] = {"data": []}

    def bind(self, player):
        self.player = player
        super().set_bus(self.bus)
        self.player.add_event('ovos.common_play.playlist.play',
                              self.handle_play_from_playlist)
        self.player.add_event('ovos.common_play.liked_tracks.play',
                              self.handle_play_from_liked_tracks)
        self.player.add_event('ovos.common_play.search.play',
                              self.handle_play_from_search)
        self.player.add_event('ovos.common_play.skill.play',
                              self.handle_play_skill_featured_media)
        self.player.add_event('ovos.common_play.home',
                              self.handle_home)

    def handle_home(self, message):
        self.manage_display(OCPGUIState.HOME)

    def release(self):
        self.clear()
        super().release()

    # OCPMediaPlayer interface
    def update_ocp_cards(self):
        skills_cards = [
            {"skill_id": skill["skill_id"],
             "title": skill["skill_name"],
             "image": skill.get("image") or skill.get("thumbnail") or f"{dirname(__file__)}/qt5/images/placeholder.png"
             } for skill in self.player.media.get_featured_skills()]
        self["skillCards"] = skills_cards
        liked_cards = sorted([
            {"uri": uri,
             "title": song["title"],
             "image": song.get("image") or song.get("thumbnail") or f"{dirname(__file__)}/qt5/images/placeholder.png"
             } for uri, song in self.player.media.liked_songs.items()
            if song["title"] and song.get("image")],
            key=lambda k: k.get("play_count", 0),
            reverse=True)
        self["showLiked"] = len(liked_cards) >= 1
        self["likedCards"] = liked_cards

    def update_buttons(self):
        self["canResume"] = self.player.state == PlayerState.PAUSED
        self["canPause"] = self.player.state == PlayerState.PLAYING
        self["canPrev"] = self.player.can_prev
        self["canNext"] = self.player.can_next
        self["isLike"] = self.player.now_playing.original_uri in self.player.media.liked_songs and \
                         self.player.now_playing.playback != PlaybackType.MPRIS
        self["isMusic"] = self.player.now_playing.media_type in [MediaType.MUSIC, MediaType.RADIO] and \
                          self.player.now_playing.playback != PlaybackType.MPRIS

        if self.player.loop_state == LoopState.NONE:
            self["loopStatus"] = "None"
        elif self.player.loop_state == LoopState.REPEAT_TRACK:
            self["loopStatus"] = "RepeatTrack"
        elif self.player.loop_state == LoopState.REPEAT:
            self["loopStatus"] = "Repeat"

        self["shuffleStatus"] = self.player.shuffle

    def update_current_track(self):
        self["media"] = self.player.now_playing.infocard
        self["uri"] = self.player.now_playing.original_uri
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
            "data": [e.infocard for e in self.player.search_results]
        }

    def update_playlist(self):
        self["playlistModel"] = {
            "data": [e.infocard for e in self.player.tracks]
        }

    # GUI
    def manage_display(self, state: OCPGUIState, timeout=None):
        self.prepare_gui_data()
        # handle any state management needed before render
        if state == OCPGUIState.HOME:
            self.render_home(timeout=timeout)
        elif state == OCPGUIState.PLAYER:
            self.clear_notification()
            self.render_player(timeout=timeout)
        elif state == OCPGUIState.PLAYLIST:
            self.render_playlist(timeout=timeout)
        elif state == OCPGUIState.DISAMBIGUATION:
            self.render_disambiguation(timeout=timeout)
        elif state == OCPGUIState.SPINNER:
            self.render_search_spinner()
        elif state == OCPGUIState.PLAYBACK_ERROR:
            self.render_error()

    def remove_homescreen(self):
        self.release()

    # OCP pre-rendering
    def prepare_gui_data(self):
        self.update_buttons()
        self.update_current_track()  # populate now_playing metadata
        self.update_playlist()  # populate self["playlistModel"]
        self.update_search_results()  # populate self["searchModel"]

    # OCP rendering
    def render_pages(self, timeout=None, index=0):

        pages = ["Home"]

        if self.player.state != PlayerState.STOPPED:
            # the audio/video plugins can define what page to show
            # this is done by using GUIInterface with OCP_ID to share data
            if self.player.now_playing.playback == PlaybackType.AUDIO:
                p = self["audio_player_page"]
            elif self.player.now_playing.playback == PlaybackType.VIDEO:
                p = self["video_player_page"]
            elif self.player.now_playing.playback == PlaybackType.WEBVIEW:
                p = self["web_player_page"]
            else:
                p = self["sync_player_page"]

            pages.append(p)

        if len(self["playlistModel"]["data"]) or len(self["searchModel"]["data"]):
            pages.append("PlaylistView")
        if index == -1:
            index = len(pages) - 1
        self.show_pages(pages, index,
                        override_idle=timeout or True,
                        override_animations=True,
                        remove_others=True)

    def render_home(self, timeout=None):
        self.update_ocp_cards()  # populate self["skillCards"]
        self["homepage_index"] = 0
        self["displayBottomBar"] = False
        # Check if the skills page has anything to show, only show it if it does
        if self["skillCards"]:
            self["displayBottomBar"] = True
        self.render_pages(index=0, timeout=timeout)

    def render_player(self, timeout=None):
        self.render_pages(index=1, timeout=timeout)
        if len(self.player.tracks):
            self.send_event("ocp.gui.show.suggestion.view.playlist")
        elif len(self.player.search_results):
            self.send_event("ocp.gui.show.suggestion.view.disambiguation")

    def render_playlist(self, timeout=None):
        self.render_pages(timeout, index=-1)
        self.send_event("ocp.gui.show.suggestion.view.playlist")

    def render_disambiguation(self, timeout=None):
        self.render_pages(timeout, index=-1)
        self.send_event("ocp.gui.show.suggestion.view.disambiguation")

    def render_error(self, error="Playback Error"):
        self["error"] = error
        self["animation"] = f"animations/{random.choice(['error', 'error2', 'error3', 'error4'])}.json"
        self["image"] = join(dirname(__file__), "qt5/images/fail.svg")
        self.display_notification("Sorry, An error occurred while playing media")
        self.show_page("StreamError", override_idle=30,
                       override_animations=True, remove_others=True)

    def render_search_spinner(self, persist_home=False):
        self.display_notification("Searching...Your query is being processed")
        self.show_page("SearchingMedia", override_idle=True,
                       override_animations=True, remove_others=True)

    # notifications
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
    def handle_play_from_liked_tracks(self, message):
        LOG.info("Playback requested for liked tracks")
        uri = message.data.get("uri")

        # liked songs playlist
        pl = self.player.media.liked_songs_playlist

        if not len(pl):
            LOG.error("No liked tracks")
            self.render_error("No liked tracks")
            self.bus.emit(message.forward("mycroft.audio.play_sound",
                                          {"uri": "snd/error.mp3"}))
            return

        # uri2track
        track = None
        if uri:
            track = self.player.media.liked_songs.get(uri)
            if track:
                # inject data for playback not present in GUI
                track["media_type"] = MediaType.MUSIC
                track["playback"] = PlaybackType.AUDIO
            else:
                LOG.error("Track is not part of liked songs!")

        track = track or pl[0]
        self.player.play_media(track, disambiguation=pl)

    def handle_play_from_playlist(self, message):
        LOG.info("Playback requested from playlist results")
        media = message.data["playlistData"]
        # if media is a playlist, it doesnt have a uri assigned

        for track in self.player.search_results:
            if isinstance(track, dict):
                track = MediaEntry.from_dict(track)

            if isinstance(track, MediaEntry) and \
                    track.uri == media.get("uri"):  # found track
                self.player.play_media(track)
                break
            elif isinstance(track, Playlist) and \
                    track.title == media.get("track"):  # found playlist
                self.player.play_media(track)
                break
        else:
            LOG.error("Track is not part of loaded playlist!")

    def handle_play_from_search(self, message):
        LOG.info("Playback requested from search results")
        media = message.data["playlistData"]
        # if media is a playlist, it doesnt have a uri assigned

        for track in self.player.search_results:
            if isinstance(track, dict):
                track = MediaEntry.from_dict(track)

            if isinstance(track, MediaEntry) and \
                    track.uri == media.get("uri"):  # found track
                self.player.play_media(track)
                break
            elif isinstance(track, Playlist) and \
                    track.title == media.get("track"):  # found playlist
                self.player.play_media(track)
                break
        else:
            LOG.error("Track is not part of search results!")

    def handle_play_skill_featured_media(self, message):
        skill_id = message.data["skill_id"]
        LOG.info(f"Featured Media request: {skill_id}")
        playlist = message.data["playlist"]

        self.player.playlist.clear()
        self.player.media.replace(playlist)

        self.manage_display(OCPGUIState.DISAMBIGUATION)

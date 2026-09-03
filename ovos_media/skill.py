"""The voice front-end of the media daemon.

Everything spoken by ovos-media is spoken here. The player and the
catalog only notify; this skill owns the dialogs, the "what's playing"
and shuffle intents, and the liked-songs search results the OCP pipeline
asks for.
"""
from os.path import dirname
from typing import Optional

from ovos_bus_client.message import Message
from ovos_utils.ocp import MediaType, PlaybackType
from ovos_workshop.decorators.ocp import ocp_search
from ovos_workshop.skills.common_play import OVOSCommonPlaybackSkill

from ovos_media.catalog import KeywordRegistrar, LikedSongsStore, MediaCatalog
from ovos_media.utils import is_default_session

# locale/ and qt5/ live in the ovos_media package, next to this module;
# the skill reads its dialogs, intents and icons from there
RESOURCES_DIR = dirname(__file__)


class OCPVoiceSkill(OVOSCommonPlaybackSkill):
    """Intents, dialogs and liked-songs search for the media daemon."""

    def __init__(self, *args, likes: LikedSongsStore,
                 catalog: Optional[MediaCatalog] = None,
                 validate_source: bool = True, **kwargs):
        kwargs.setdefault("resources_dir", RESOURCES_DIR)
        super().__init__(*args, **kwargs)
        # mirrors the bus edge's session gate: keeps playback-affecting
        # intent handlers (shuffle on/off) on the local/"default" session,
        # unless the owning service was configured with
        # media.validate_source: false (satellite acting on everything)
        self.validate_source = validate_source
        self.skill_icon = f"{RESOURCES_DIR}/qt5/images/liked.svg"

        # the same store the player writes likes and play counts into
        self.likes = likes
        self.catalog = catalog
        if catalog is not None:
            catalog.add_dialog_listener(self.handle_dialog_notification)

        KeywordRegistrar(self.bus, self.skill_id, self.native_langs,
                         self.ocp_cache_dir,
                         self.register_ocp_keyword).register_liked_songs(self.likes)

        # intents about the currently playing media, see issue #23
        self.register_intent_file("WhatSong.intent", self.handle_what_song)
        self.register_intent_file("WhatAlbum.intent", self.handle_what_album)
        self.register_intent_file("WhatArtist.intent", self.handle_what_artist)
        self.register_intent_file("ShuffleOn.intent", self.handle_shuffle_on)
        self.register_intent_file("ShuffleOff.intent", self.handle_shuffle_off)

    def handle_dialog_notification(self, dialog: str,
                                   data: Optional[dict] = None,
                                   message: Optional[Message] = None) -> None:
        """Speak a dialog the player asked the catalog to announce.

        END_OF_MEDIA/INVALID_MEDIA-derived announcements tied to a specific
        playback (track.failed, queue.finished) carry *message*: the
        'ovos.common_play.play' Message that started that playback,
        stashed by OCPMediaPlayer at play time (see OCPMediaPlayer.
        _play_message/_notify_dialog) and forwarded here through
        MediaCatalog.notify_dialog. speak_dialog()'s underlying speak()
        digs the call stack for a Message to carry context/session from
        (dig_for_message) - naming this parameter *message* and holding it
        as a local here is what makes that lookup find it, so the dialog is
        spoken back on the ORIGINATING session instead of the default one.

        Announcements with no playback of their own to tie to
        (no.playback.backend, nothing.playing) - or a notify_dialog call
        from a plain in-process caller that passed no message - land on the
        default session (*message* is None).
        """
        self.speak_dialog(dialog, data)

    def _get_status(self, message: Message) -> Optional[dict]:
        """Query current player status via the existing status bus API.

        Reuses the 'ovos.common_play.status' request/response messages that
        OCPMediaPlayer.handle_status already answers; this avoids adding any
        new bus message types or coupling this skill directly to the
        OCPMediaPlayer instance.

        The request is forwarded from the triggering intent message so the
        session context (session_id, lang, etc) is preserved on the wire.

        Returns None if no response was received within the timeout (player
        not responding), as opposed to an empty dict, which means the player
        answered but nothing is currently playing.
        """
        response = self.bus.wait_for_response(message.forward("ovos.common_play.status"),
                                              timeout=3)
        return response.data if response else None

    # WhatSong/WhatAlbum/WhatArtist are deliberately UN-gated by session:
    # they mirror OCPMediaPlayer.handle_status, which itself answers every
    # session's "ovos.common_play.status" query with the single shared
    # player's state (its bus topic is not gated).
    # The consistency rule this repo follows is that each intent front-end
    # mirrors its backing handler's own gating - handle_status is global
    # read-only state, so these read handlers stay global too. Only the
    # shuffle on/off handlers below are gated, because they mirror
    # handle_set_shuffle/handle_unset_shuffle, which ARE gated.
    def handle_what_song(self, message):
        status = self._get_status(message)
        if status is None:
            self.speak_dialog("player.not.responding")
            return
        title = status.get("title")
        artist = status.get("artist")
        if not title:
            self.speak_dialog("nothing.playing")
        elif artist:
            self.speak_dialog("now.playing.song", {"title": title, "artist": artist})
        else:
            self.speak_dialog("now.playing.song.no.artist", {"title": title})

    def handle_what_album(self, message):
        status = self._get_status(message)
        if status is None:
            self.speak_dialog("player.not.responding")
            return
        if not status.get("title"):
            self.speak_dialog("nothing.playing")
        else:
            # NowPlaying/MediaEntry does not track album metadata, so this
            # always falls back gracefully instead of guessing or crashing.
            self.speak_dialog("no.album.info")

    def handle_what_artist(self, message):
        status = self._get_status(message)
        if status is None:
            self.speak_dialog("player.not.responding")
            return
        title = status.get("title")
        artist = status.get("artist")
        if not title:
            self.speak_dialog("nothing.playing")
        elif artist:
            self.speak_dialog("now.playing.artist", {"artist": artist})
        else:
            self.speak_dialog("no.artist.info")

    def _is_default_session(self, message: Message) -> bool:
        """Whether the player will act on a request forwarded from this
        message, using the same rule the bus edge applies to
        'ovos.common_play.shuffle.set'/'.unset'. On a non-default (e.g.
        HiveMind satellite) session with validate_source left True the
        emitted message is silently dropped by the player - this must not be
        reported back to the user as a success. When validate_source is
        False the player WILL act on it, so this front-end must agree."""
        return is_default_session(message, self.validate_source)

    def handle_shuffle_on(self, message):
        if not self._is_default_session(message):
            self.speak_dialog("cannot.control.device")
            return
        self.bus.emit(message.forward("ovos.common_play.shuffle.set"))
        self.speak_dialog("shuffle.on")

    def handle_shuffle_off(self, message):
        if not self._is_default_session(message):
            self.speak_dialog("cannot.control.device")
            return
        self.bus.emit(message.forward("ovos.common_play.shuffle.unset"))
        self.speak_dialog("shuffle.off")

    @ocp_search()
    def search_db(self, phrase, media_type):
        base_score = 15 if media_type == MediaType.MUSIC else 0
        entities = self.ocp_voc_match(phrase)
        base_score += 30 * len(entities)

        if entities.get("playlist_name"):
            if phrase.lower() == entities["playlist_name"]:
                base_score = 100
            yield {
                "match_confidence": min(base_score + 35, 100),
                "media_type": MediaType.MUSIC,
                "playback": PlaybackType.AUDIO,
                "playlist": [e.as_dict for e in self.likes.as_entries()],
                "skill_icon": self.skill_icon,
                "title": "Liked Songs",
                "skill_id": self.skill_id
            }

        if entities.get("song_name"):
            title = entities["song_name"].lower()
            for entry in self.likes.as_entries():
                if title not in entry.title.lower():
                    continue
                result = entry.as_dict
                result["match_confidence"] = min(base_score + 40, 100)
                result["skill_id"] = self.skill_id
                result["skill_icon"] = self.skill_icon
                yield result

    def default_shutdown(self):
        if self.catalog is not None:
            self.catalog.remove_dialog_listener(self.handle_dialog_notification)
        super().default_shutdown()

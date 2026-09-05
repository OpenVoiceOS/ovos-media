"""The voice front-end of the media daemon.

Everything spoken by ovos-media is spoken here. The player and the
catalog only notify; this skill owns the dialogs, the "what's playing"
and shuffle intents, and the liked-songs search results the OCP pipeline
asks for.
"""
import re
from os.path import dirname
from typing import Optional

from ovos_bus_client.message import Message
from ovos_config import Configuration
from ovos_number_parser import extract_number
from ovos_utils.ocp import MediaType, PlaybackType, PlayerState
from ovos_workshop.decorators.ocp import ocp_search
from ovos_workshop.skills.common_play import OVOSCommonPlaybackSkill

from ovos_media.catalog import KeywordRegistrar, LikedSongsStore, MediaCatalog
from ovos_media.catalog.keywords import (MOST_PLAYED_KEYWORDS,
                                         PLAYLIST_KEYWORDS,
                                         RECENTLY_PLAYED_KEYWORDS)
from ovos_media.utils import is_default_session

# locale/ and qt5/ live in the ovos_media package, next to this module;
# the skill reads its dialogs, intents and icons from there
RESOURCES_DIR = dirname(__file__)

# matches the bare "a minute"/"a second" idiom (understood as 1), not any
# other "a ..." ("a bit", "a moment") which carries no amount at all
_A_UNIT = re.compile(r"\ba (minute|second)s?\b")


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
            catalog.add_likes_listener(self.handle_likes_changed)

        # read the same way the player reads its sibling history knobs
        # (see OCPMediaPlayer.play/__init__)
        self.history_enabled = Configuration().get("media", {}) \
            .get("history", {}).get("enabled", True)

        self._keyword_registrar = KeywordRegistrar(
            self.bus, self.skill_id, self.native_langs,
            self.ocp_cache_dir, self.register_ocp_keyword)
        self._keyword_registrar.register_liked_songs(self.likes)
        # history-gated: no point advertising "recently played"/"most
        # played" if the feature itself is disabled
        if self.history_enabled:
            self._keyword_registrar.register_history_playlists()

        # intents about the currently playing media, see issue #23
        self.register_intent_file("WhatSong.intent", self.handle_what_song)
        self.register_intent_file("WhatAlbum.intent", self.handle_what_album)
        self.register_intent_file("WhatArtist.intent", self.handle_what_artist)
        self.register_intent_file("ShuffleOn.intent", self.handle_shuffle_on)
        self.register_intent_file("ShuffleOff.intent", self.handle_shuffle_off)
        self.register_intent_file("WhatShuffle.intent", self.handle_what_shuffle)
        self.register_intent_file("WhatNext.intent", self.handle_what_next)

        # like/unlike, repeat and seek are the player-owned counterparts of
        # the ocp-pipeline intents of the same purpose - the pipeline
        # deliberately leaves its own play_favorites/like_song intents
        # disabled ("handled by ovos-media not ovos-audio") so this skill is
        # the only place they are registered
        self.register_intent_file("Like.intent", self.handle_like)
        self.register_intent_file("Unlike.intent", self.handle_unlike)
        self.register_intent_file("RepeatOn.intent", self.handle_repeat_on)
        self.register_intent_file("RepeatOff.intent", self.handle_repeat_off)
        self.register_intent_file("RepeatTrack.intent", self.handle_repeat_track)
        self.register_intent_file("SeekForward.intent", self.handle_seek_forward)
        self.register_intent_file("SeekBackward.intent", self.handle_seek_backward)

    def handle_likes_changed(self) -> None:
        """Refresh the song-title keywords after a like/unlike, so a song
        liked this session is findable by name without a restart.

        Delta registration only (see KeywordRegistrar.register_new_titles):
        replaying the full store here would grow the unbounded upstream
        sample list on every single like/unlike."""
        self._keyword_registrar.register_new_titles(self.likes)

    def handle_dialog_notification(self, dialog: str,
                                   data: Optional[dict] = None) -> None:
        """Speak a dialog the player asked the catalog to announce.

        speak_dialog() routes over the session it finds by walking the
        thread's message stack (dig_for_message()), not one this method
        chooses. A notification fired from inside a handler that is
        currently processing a session-carrying message (eg. handle_unlike
        acting on a satellite's request) follows THAT session, so it
        announces back to the satellite that triggered it. A notification
        fired from a context with no message on the stack (a bus event with
        no session of its own — END_OF_MEDIA, INVALID_MEDIA, the delayed
        invalid-stream retry timer) falls back to the default session and
        announces locally instead, regardless of which session's playback
        actually failed.
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
    # read-only state, so these read handlers stay global too - WhatShuffle
    # and WhatNext below join them for the same reason. Every handler that
    # actually changes player state (shuffle, like/unlike, repeat, seek) is
    # gated instead, because each mirrors a bus handler that is itself
    # gated at the edge (see ovos_media.bus.api).
    def handle_what_song(self, message):
        status = self._get_status(message)
        if status is None:
            self.speak_dialog("player.not.responding")
            return
        title = status.get("title")
        artist = status.get("artist")
        if not title:
            # PAUSED counts as "something is loaded" too, not just PLAYING -
            # a paused untitled stream is not "nothing playing"; a missing
            # player_state key (older/incomplete status payloads) defaults
            # to STOPPED so this stays "nothing playing" as before
            if status.get("player_state", PlayerState.STOPPED) != PlayerState.STOPPED:
                self.speak_dialog("no.track.info")
            else:
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
            if status.get("player_state", PlayerState.STOPPED) != PlayerState.STOPPED:
                self.speak_dialog("no.track.info")
            else:
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
        if artist:
            self.speak_dialog("now.playing.artist", {"artist": artist})
        elif title:
            self.speak_dialog("no.artist.info")
        elif status.get("player_state", PlayerState.STOPPED) != PlayerState.STOPPED:
            self.speak_dialog("no.track.info")
        else:
            self.speak_dialog("nothing.playing")

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

    # WhatShuffle is a read query, same rationale as WhatSong/WhatAlbum/
    # WhatArtist above: it mirrors handle_status, which is global read-only
    # state, so it stays ungated.
    def handle_what_shuffle(self, message):
        status = self._get_status(message)
        if status is None:
            self.speak_dialog("player.not.responding")
            return
        if status.get("shuffle"):
            self.speak_dialog("shuffle.state.on")
        else:
            self.speak_dialog("shuffle.state.off")

    def handle_what_next(self, message):
        status = self._get_status(message)
        if status is None:
            self.speak_dialog("player.not.responding")
            return
        # same idle check WhatSong/WhatAlbum/WhatArtist make - nothing ever
        # loaded means there is no "next" to preview, not an empty queue
        if status.get("player_state", PlayerState.STOPPED) == PlayerState.STOPPED:
            self.speak_dialog("nothing.playing")
            return
        hint = status.get("next_track_hint")
        if hint == "shuffle":
            # shuffle draws its pick at play time (see
            # OCPMediaPlayer.next_track_preview) - there is nothing honest
            # to announce beyond that
            self.speak_dialog("next.track.shuffle")
            return
        if hint == "external":
            # play_next() itself defers to MPRIS/an OCP skill here - this
            # player has no queue of its own to preview
            self.speak_dialog("next.track.external")
            return
        next_entry = status.get("next_track")
        if not next_entry:
            self.speak_dialog("next.nothing")
            return
        title = next_entry.get("title")
        artist = next_entry.get("artist")
        if artist:
            self.speak_dialog("next.track", {"title": title, "artist": artist})
        else:
            self.speak_dialog("next.track.no.artist", {"title": title})

    def handle_like(self, message):
        if not self._is_default_session(message):
            self.speak_dialog("cannot.control.device")
            return
        self.bus.emit(message.forward("ovos.common_play.like"))

    def handle_unlike(self, message):
        if not self._is_default_session(message):
            self.speak_dialog("cannot.control.device")
            return
        self.bus.emit(message.forward("ovos.common_play.unlike"))

    def handle_repeat_on(self, message):
        if not self._is_default_session(message):
            self.speak_dialog("cannot.control.device")
            return
        self.bus.emit(message.forward("ovos.common_play.repeat.set"))
        self.speak_dialog("repeat.on")

    def handle_repeat_off(self, message):
        if not self._is_default_session(message):
            self.speak_dialog("cannot.control.device")
            return
        self.bus.emit(message.forward("ovos.common_play.repeat.unset"))
        self.speak_dialog("repeat.off")

    def handle_repeat_track(self, message):
        if not self._is_default_session(message):
            self.speak_dialog("cannot.control.device")
            return
        self.bus.emit(message.forward("ovos.common_play.repeat.set",
                                      {"mode": "track"}))
        self.speak_dialog("repeat.track.on")

    def _extract_seconds(self, message: Message) -> float:
        """How far to seek, parsed from the utterance that triggered
        *message*.

        Only a single number+unit is understood ("30 seconds", "two
        minutes"), plus the bare "a minute"/"a second" idiom (worth 1).
        A composite duration ("2 minutes 30 seconds") keeps only the first
        number and picks the larger of the two units mentioned (minutes),
        the same way "2 minutes" alone would. Anything else - no amount at
        all ("skip forward"), or an "a ..." that isn't immediately followed
        by a unit ("skip ahead a bit") - falls back to a plain 10 second
        nudge, never a silent near-zero seek.
        """
        utterance = message.data.get("utterance", "")
        amount = extract_number(utterance, lang=self.lang)
        if amount is False or amount is None:
            match = _A_UNIT.search(utterance)
            if match is None:
                return 10
            amount = 1
            return amount * 60 if match.group(1) == "minute" else amount
        return amount * 60 if "minute" in utterance else amount

    def handle_seek_forward(self, message):
        if not self._is_default_session(message):
            self.speak_dialog("cannot.control.device")
            return
        seconds = self._extract_seconds(message)
        self.bus.emit(message.forward("ovos.common_play.seek", {"seconds": seconds}))

    def handle_seek_backward(self, message):
        if not self._is_default_session(message):
            self.speak_dialog("cannot.control.device")
            return
        seconds = self._extract_seconds(message)
        self.bus.emit(message.forward("ovos.common_play.seek", {"seconds": -seconds}))

    @ocp_search()
    def search_db(self, phrase, media_type):
        base_score = 15 if media_type == MediaType.MUSIC else 0
        entities = self.ocp_voc_match(phrase)
        base_score += 30 * len(entities)

        matched_playlist = entities.get("playlist_name")
        # ocp_voc_match returns the ORIGINAL-cased span from the utterance
        # (whisper-family STT capitalizes), while every *_KEYWORDS list is
        # lowercase - comparisons against those lists, and against `phrase`,
        # must lower() both sides or a capitalized utterance ("Liked
        # songs") matches nothing.
        matched_playlist_lower = matched_playlist.lower() if matched_playlist else None
        # PLAYLIST_KEYWORDS, RECENTLY_PLAYED_KEYWORDS and MOST_PLAYED_KEYWORDS
        # all register under the same "playlist_name" label, so the matched
        # keyword itself decides which intrinsic playlist is meant - a bare
        # `entities.get("playlist_name")` check would fire the wrong
        # playlist (e.g. "recently played" would also yield Liked Songs).
        if matched_playlist_lower and matched_playlist_lower in PLAYLIST_KEYWORDS \
                and len(self.likes) > 0:
            if phrase.lower() == matched_playlist_lower:
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

        history = self.catalog.history if self.catalog is not None else None
        if self.history_enabled and history is not None and matched_playlist_lower:
            phrase_matches_playlist = phrase.lower() == matched_playlist_lower
            if matched_playlist_lower in RECENTLY_PLAYED_KEYWORDS:
                recent = history.recent()
                if recent:
                    yield {
                        "match_confidence": 100 if phrase_matches_playlist else min(base_score + 35, 100),
                        "media_type": MediaType.MUSIC,
                        "playback": PlaybackType.AUDIO,
                        "playlist": [e.as_dict for e in recent],
                        "skill_icon": self.skill_icon,
                        "title": "Recently Played",
                        "skill_id": self.skill_id
                    }
            elif matched_playlist_lower in MOST_PLAYED_KEYWORDS:
                most_played = history.most_played()
                if most_played:
                    yield {
                        "match_confidence": 100 if phrase_matches_playlist else min(base_score + 35, 100),
                        "media_type": MediaType.MUSIC,
                        "playback": PlaybackType.AUDIO,
                        "playlist": [e.as_dict for e in most_played],
                        "skill_icon": self.skill_icon,
                        "title": "Most Played",
                        "skill_id": self.skill_id
                    }

        if entities.get("song_name"):
            # entities["song_name"] can be a stale NER match - an unliked
            # title stays in the local matcher until restart (see
            # KeywordRegistrar.register_new_titles) - but this loop is keyed
            # by the CURRENT store contents, so a stale match yields nothing
            # here rather than a wrong result; OCP falls back to other
            # skills/results the normal way a search handler always might.
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
            self.catalog.remove_likes_listener(self.handle_likes_changed)
        super().default_shutdown()

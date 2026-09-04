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
"""Regression tests for silent playback dead ends on an audio-only device,
where a missing notify_dialog() call is a total loss of feedback, not just
a missing log line.

Covers:
 - play_next()'s three terminal "give up" branches (repeat-track,
   shuffle, sequential AllFailed) now announce playback.failed instead of
   going silent forever.
 - handle_unlike() acknowledges a successful unlike and speaks
   nothing.playing when there is nothing to target, mirroring handle_like.
 - search_db() never offers the empty liked-songs playlist at forced
   confidence.
 - WhatSong/WhatAlbum/WhatArtist distinguish "playing but untitled" from
   "nothing playing" using player_state, not the title proxy.
 - a like/unlike refreshes the song-title keyword registration so a song
   liked this session is findable by name without a restart.
 - seek() announces cannot.seek when no adapter supports it, instead of
   only logging.
"""
import unittest
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import LoopState, MediaType, PlaybackType, PlayerState

from player_fixture import make_player


# ---------------------------------------------------------------------------
# UX-001: play_next()'s three terminal "give up" branches must announce
# playback.failed instead of stopping silently.
# ---------------------------------------------------------------------------

class TestPlaybackFailedAnnouncedOnGiveUp(unittest.TestCase):

    def test_repeat_track_giveup_announces_playback_failed(self):
        p = make_player(PlaybackType.AUDIO)
        p.loop_state = LoopState.REPEAT_TRACK
        p.now_playing.uri = "http://a.mp3"
        p._failed_uris.add("http://a.mp3")
        with patch.object(p, "play") as mock_play:
            p.play_next()
        mock_play.assert_not_called()
        p.media.notify_dialog.assert_called_once_with("playback.failed")

    def test_shuffle_giveup_announces_playback_failed(self):
        from ovos_utils.ocp import MediaEntry, Playlist
        p = make_player(PlaybackType.AUDIO)
        p.shuffle = True
        p.loop_state = LoopState.REPEAT
        p.playlist = Playlist()
        tracks = [MediaEntry(uri=f"http://{i}.mp3", title=f"T{i}",
                             playback=PlaybackType.AUDIO) for i in range(3)]
        for t in tracks:
            p.playlist.add_entry(t)
        p.now_playing.uri = tracks[0].uri
        for t in tracks:
            p._failed_uris.add(t.uri)
        p.media.search_playlist.entries = []
        with patch.object(p, "play") as mock_play, \
             patch.object(p, "set_player_state"):
            p.play_next()
        mock_play.assert_not_called()
        p.media.notify_dialog.assert_called_once_with("playback.failed")

    def test_sequential_all_failed_announces_playback_failed(self):
        from ovos_utils.ocp import MediaEntry, Playlist
        p = make_player(PlaybackType.AUDIO)
        p.loop_state = LoopState.REPEAT
        p.playlist = Playlist()
        tracks = [MediaEntry(uri=f"http://{i}.mp3", title=f"T{i}",
                             playback=PlaybackType.AUDIO) for i in range(2)]
        for t in tracks:
            p.playlist.add_entry(t)
        # at the last track, wholly failed - repeat wrap-around must refuse
        # to restart, not keep looping forever
        p.now_playing.uri = tracks[-1].uri
        for t in tracks:
            p._queue.mark_failed(t.uri)
        p.media.search_playlist.entries = []
        with patch.object(p, "play") as mock_play, \
             patch.object(p, "set_player_state"):
            p.play_next()
        mock_play.assert_not_called()
        p.media.notify_dialog.assert_called_once_with("playback.failed")


# ---------------------------------------------------------------------------
# UX-003: handle_unlike must acknowledge success and speak when there is
# nothing to target, instead of discarding LikedSongsStore.unlike()'s bool.
# ---------------------------------------------------------------------------

class TestHandleUnlikeFeedback(unittest.TestCase):

    def test_successful_unlike_plays_acknowledge_sound(self):
        p = make_player()
        p.media.likes.unlike.return_value = True
        emitted = []
        p.bus.on("mycroft.audio.play_sound", lambda m: emitted.append(m.data))
        uri = "http://example.com/song.mp3"
        p.now_playing.original_uri = uri
        p.handle_unlike(Message("ovos.common_play.unlike", {"uri": uri}))
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0]["uri"], "snd/acknowledge.mp3")

    def test_unlike_with_no_uri_speaks_nothing_playing(self):
        p = make_player()
        p.now_playing.original_uri = ""
        p.handle_unlike(Message("ovos.common_play.unlike", {}))
        p.media.notify_dialog.assert_called_once_with("nothing.playing")
        p.media.likes.unlike.assert_not_called()

    def test_failed_unlike_with_uri_present_does_not_speak_or_play_sound(self):
        """A uri that was never liked returns False from unlike() - this is
        not the "nothing playing" case the brief calls out, so it stays
        silent rather than guessing at a dialog."""
        p = make_player()
        p.media.likes.unlike.return_value = False
        emitted = []
        p.bus.on("mycroft.audio.play_sound", lambda m: emitted.append(m.data))
        uri = "http://example.com/never-liked.mp3"
        p.handle_unlike(Message("ovos.common_play.unlike", {"uri": uri}))
        self.assertEqual(emitted, [])
        p.media.notify_dialog.assert_not_called()


# ---------------------------------------------------------------------------
# UX-002: search_db must not offer the empty liked-songs playlist.
# ---------------------------------------------------------------------------

class _FakeStore(dict):
    def store(self):
        pass


def _likes(entries=None):
    from ovos_media.catalog import LikedSongsStore
    return LikedSongsStore(_FakeStore(entries or {}))


def _make_skill(bus, **kwargs):
    from ovos_media.skill import OCPVoiceSkill
    kwargs.setdefault("likes", _likes())
    return OCPVoiceSkill(bus=bus, skill_id="ovos.common_play.favorites", **kwargs)


class TestSearchDbEmptyLikes(unittest.TestCase):

    def test_liked_songs_playlist_not_offered_when_empty(self):
        bus = FakeBus()
        skill = _make_skill(bus, likes=_likes())
        skill.ocp_voc_match = MagicMock(return_value={"playlist_name": "liked songs"})
        results = list(skill.search_db("play my liked songs", MediaType.MUSIC))
        self.assertEqual(results, [])

    def test_liked_songs_playlist_offered_when_not_empty(self):
        bus = FakeBus()
        skill = _make_skill(bus, likes=_likes({
            "http://x.mp3": {"title": "X", "artist": "Y"}
        }))
        skill.ocp_voc_match = MagicMock(return_value={"playlist_name": "liked songs"})
        results = list(skill.search_db("play my liked songs", MediaType.MUSIC))
        self.assertTrue(any(r["title"] == "Liked Songs" for r in results))


# ---------------------------------------------------------------------------
# UX-004: WhatSong/WhatAlbum/WhatArtist must not answer "nothing is
# playing" for a playing-but-untitled stream.
# ---------------------------------------------------------------------------

def _make_wired_skill(status: dict):
    bus = FakeBus()
    bus.on("ovos.common_play.status",
          lambda m: bus.emit(m.response(dict(status))))
    skill = _make_skill(bus)
    spoken = []
    bus.on("speak", lambda m: spoken.append(m.data["utterance"]))
    return skill, bus, spoken


class TestPlayingButUntitledStream(unittest.TestCase):

    def test_what_song_speaks_no_track_info_when_playing_untitled(self):
        status = {"title": "", "artist": "", "player_state": PlayerState.PLAYING}
        skill, bus, spoken = _make_wired_skill(status)
        skill.handle_what_song(Message("recognizer_loop:utterance", {}))
        self.assertEqual(len(spoken), 1)
        self.assertNotIn(spoken[0].lower(),
                        {"nothing is playing right now",
                         "there's nothing playing at the moment",
                         "i'm not playing anything right now"})

    def test_what_song_still_speaks_nothing_playing_when_stopped(self):
        status = {"title": "", "artist": "", "player_state": PlayerState.STOPPED}
        skill, bus, spoken = _make_wired_skill(status)
        skill.handle_what_song(Message("recognizer_loop:utterance", {}))
        self.assertEqual(len(spoken), 1)
        self.assertIn(spoken[0].lower(),
                     {"nothing is playing right now",
                      "there's nothing playing at the moment",
                      "i'm not playing anything right now"})

    def test_what_artist_answers_artist_only_stream(self):
        status = {"title": "", "artist": "Some Artist",
                  "player_state": PlayerState.PLAYING}
        skill, bus, spoken = _make_wired_skill(status)
        skill.handle_what_artist(Message("recognizer_loop:utterance", {}))
        self.assertEqual(len(spoken), 1)
        self.assertNotIn(spoken[0].lower(),
                        {"nothing is playing right now",
                         "there's nothing playing at the moment",
                         "i'm not playing anything right now"})

    def test_what_song_speaks_no_track_info_when_paused_untitled(self):
        """A paused-but-untitled stream is not 'nothing playing' either -
        PAUSED is still something loaded, not an empty player."""
        status = {"title": "", "artist": "", "player_state": PlayerState.PAUSED}
        skill, bus, spoken = _make_wired_skill(status)
        skill.handle_what_song(Message("recognizer_loop:utterance", {}))
        self.assertEqual(len(spoken), 1)
        self.assertNotIn(spoken[0].lower(),
                        {"nothing is playing right now",
                         "there's nothing playing at the moment",
                         "i'm not playing anything right now"})

    def test_what_album_speaks_no_track_info_when_paused_untitled(self):
        status = {"title": "", "artist": "", "player_state": PlayerState.PAUSED}
        skill, bus, spoken = _make_wired_skill(status)
        skill.handle_what_album(Message("recognizer_loop:utterance", {}))
        self.assertEqual(len(spoken), 1)
        self.assertNotIn(spoken[0].lower(),
                        {"nothing is playing right now",
                         "there's nothing playing at the moment",
                         "i'm not playing anything right now"})

    def test_what_artist_speaks_no_track_info_when_paused_untitled(self):
        status = {"title": "", "artist": "", "player_state": PlayerState.PAUSED}
        skill, bus, spoken = _make_wired_skill(status)
        skill.handle_what_artist(Message("recognizer_loop:utterance", {}))
        self.assertEqual(len(spoken), 1)
        self.assertNotIn(spoken[0].lower(),
                        {"nothing is playing right now",
                         "there's nothing playing at the moment",
                         "i'm not playing anything right now"})


# ---------------------------------------------------------------------------
# UX-007: a like/unlike must refresh the song-title keyword registration,
# WITHOUT leaking. register_ocp_keyword APPENDS samples with no dedup
# upstream, so a wholesale re-registration on every like/unlike would grow
# self._ocp_ents (and the exported CSV / bus payload) without bound.
# ---------------------------------------------------------------------------

class TestLikeUnlikeRefreshesKeywords(unittest.TestCase):

    def test_like_reregisters_song_title_keywords(self):
        from ovos_media.catalog import LikedSongsStore, MediaCatalog

        with patch("ovos_workshop.skills.common_play.AhocorasickNER", None):
            bus = FakeBus()
            registered = []
            bus.on("ovos.common_play.register_keyword",
                  lambda m: registered.append(m.data))

            store = _FakeStore()
            likes = LikedSongsStore(store)
            catalog = MediaCatalog(bus, likes)
            _make_skill(bus, likes=likes, catalog=catalog)

            registered.clear()
            likes.like("http://new-song.mp3", title="Brand New Song")
            catalog.notify_likes_changed()

            song_name_msgs = [r for r in registered if r["label"] == "song_name"]
            self.assertEqual(len(song_name_msgs), 1)
            self.assertIn("Brand New Song", song_name_msgs[0]["samples"])

    def test_liking_the_same_song_twice_registers_it_exactly_once(self):
        """Same uri liked twice (eg. a duplicate 'like' bus message) must
        not append the title to _ocp_ents a second time."""
        from ovos_media.catalog import LikedSongsStore, MediaCatalog

        bus = FakeBus()
        store = _FakeStore()
        likes = LikedSongsStore(store)
        catalog = MediaCatalog(bus, likes)
        skill = _make_skill(bus, likes=likes, catalog=catalog)

        likes.like("http://x.mp3", title="Same Song")
        catalog.notify_likes_changed()
        likes.like("http://x.mp3", title="Same Song")
        catalog.notify_likes_changed()

        self.assertEqual(skill._ocp_ents["song_name"].count("Same Song"), 1)

    def test_three_likes_never_reappend_the_static_playlist_synonyms(self):
        """The playlist_name synonyms are fixed and registered once, at
        construction - a like/unlike refresh must never touch them again,
        or the sample list grows on every single like."""
        from ovos_media.catalog import LikedSongsStore, MediaCatalog
        from ovos_media.catalog.keywords import PLAYLIST_KEYWORDS

        bus = FakeBus()
        store = _FakeStore()
        likes = LikedSongsStore(store)
        catalog = MediaCatalog(bus, likes)
        skill = _make_skill(bus, likes=likes, catalog=catalog)

        before = list(skill._ocp_ents["playlist_name"])
        self.assertEqual(len(before), len(PLAYLIST_KEYWORDS))

        for i in range(3):
            likes.like(f"http://{i}.mp3", title=f"Song {i}")
            catalog.notify_likes_changed()

        after = skill._ocp_ents["playlist_name"]
        self.assertEqual(len(after), len(PLAYLIST_KEYWORDS))
        self.assertEqual(after, before)

    def test_unliking_leaves_the_title_registered_until_restart(self):
        """deregister_ocp_keyword is a no-op upstream - unliking must not
        attempt to remove the title from _ocp_ents, and must not raise."""
        from ovos_media.catalog import LikedSongsStore, MediaCatalog

        bus = FakeBus()
        store = _FakeStore()
        likes = LikedSongsStore(store)
        catalog = MediaCatalog(bus, likes)
        skill = _make_skill(bus, likes=likes, catalog=catalog)

        likes.like("http://x.mp3", title="Going Away")
        catalog.notify_likes_changed()
        self.assertIn("Going Away", skill._ocp_ents["song_name"])

        likes.unlike("http://x.mp3")
        catalog.notify_likes_changed()
        # still registered - accepted residue, not a regression
        self.assertIn("Going Away", skill._ocp_ents["song_name"])


# ---------------------------------------------------------------------------
# UX-008 (revised): cannot.seek must be rate-limited once per track, like
# track.failed - a GUI seekbar drag fires several seek requests in a row.
# ---------------------------------------------------------------------------

class TestCannotSeekRateLimited(unittest.TestCase):

    def test_repeated_seeks_on_unsupported_type_speak_once(self):
        p = make_player(PlaybackType.SKILL)
        with patch("ovos_media.player.LOG"):
            p.seek(1000)
            p.seek(2000)
        p.media.notify_dialog.assert_called_once_with("cannot.seek")

    def test_a_new_track_starting_clears_the_rate_limit(self):
        from ovos_utils.ocp import TrackState
        from ovos_media.player import NowPlaying
        p = make_player(PlaybackType.SKILL)
        p.now_playing = NowPlaying(p.bus, player=p)
        p.now_playing.playback = PlaybackType.SKILL
        with patch("ovos_media.player.LOG"):
            p.seek(1000)
        self.assertEqual(p.media.notify_dialog.call_count, 1)
        p.set_player_state = MagicMock()
        p.now_playing.handle_track_state_change(
            Message("ovos.common_play.track.state",
                    {"state": TrackState.PLAYING_SKILL}))
        with patch("ovos_media.player.LOG"):
            p.seek(1000)
        self.assertEqual(p.media.notify_dialog.call_count, 2)


# ---------------------------------------------------------------------------
# UX-006 (revised): a malformed 'ovos.common_play.play' payload never
# reaches handle_play_request/play_media at all - OCPBusApi._wrap already
# runs decode_media and drops the message before either is invoked (see
# ovos_media.bus.api.BusHandler.reject_dialog). The rejection notification
# therefore has to be proven at the bus edge, over a REAL FakeBus dispatch
# through the actually-registered listener, not by calling the player
# methods directly - a direct call bypasses the exact wrapper that owns
# the rejection and would pass even if the edge never notified anything.
# Not rate-limited: each is a direct, user-initiated request and deserves
# its own answer.
# ---------------------------------------------------------------------------

def _make_real_player(bus):
    """A real OCPMediaPlayer, with only its external service collaborators
    mocked out, wired to *bus* through its real OCPBusApi - so emitting on
    *bus* exercises the actual registered listener, decoder rejection
    included."""
    from ovos_media.player import OCPMediaPlayer
    with patch("ovos_media.player.AudioService"), \
         patch("ovos_media.player.VideoService"), \
         patch("ovos_media.player.WebService"), \
         patch("ovos_media.player.OcpMprisExporter"), \
         patch("ovos_media.player.Configuration", return_value={"media": {}}), \
         patch("ovos_media.player.OCPMediaCatalog"):
        return OCPMediaPlayer(bus, config={})


class TestInvalidRequestDialog(unittest.TestCase):

    def test_malformed_play_payload_over_the_real_bus_speaks(self):
        bus = FakeBus()
        player = _make_real_player(bus)
        player.media.notify_dialog.reset_mock()

        bus.emit(Message("ovos.common_play.play",
                         {"media": {"unrelated": "no uri, no playlist, no extractor_id"}}))

        player.media.notify_dialog.assert_called_once_with("invalid.request")
        player.shutdown()

    def test_empty_play_payload_over_the_real_bus_speaks(self):
        bus = FakeBus()
        player = _make_real_player(bus)
        player.media.notify_dialog.reset_mock()

        bus.emit(Message("ovos.common_play.play", {}))

        player.media.notify_dialog.assert_called_once_with("invalid.request")
        player.shutdown()

    def test_valid_play_payload_over_the_real_bus_does_not_speak_invalid_request(self):
        bus = FakeBus()
        player = _make_real_player(bus)
        player.media.notify_dialog.reset_mock()
        player.play_media = MagicMock()

        bus.emit(Message("ovos.common_play.play",
                         {"media": {"uri": "http://example.com/x.mp3",
                                   "title": "X"}}))

        self.assertNotIn(
            "invalid.request",
            [c.args[0] for c in player.media.notify_dialog.call_args_list])
        player.shutdown()

    def test_repeated_malformed_requests_each_speak(self):
        """Unlike cannot.seek/track.failed, invalid.request is not rate-
        limited - every direct request gets its own answer."""
        bus = FakeBus()
        player = _make_real_player(bus)
        player.media.notify_dialog.reset_mock()

        bus.emit(Message("ovos.common_play.play", {}))
        bus.emit(Message("ovos.common_play.play", {}))

        self.assertEqual(player.media.notify_dialog.call_count, 2)
        player.shutdown()

    def test_malformed_seek_payload_does_not_speak_invalid_request(self):
        """Documented choice: a rejected seek stays a silent drop - the
        current track keeps audibly playing, which is itself the signal a
        garbled seek request needs, unlike a rejected play request where
        silence is the only outcome and looks identical to nothing having
        happened yet."""
        bus = FakeBus()
        player = _make_real_player(bus)
        player.media.notify_dialog.reset_mock()

        bus.emit(Message("ovos.common_play.seek", {"seconds": "not-a-number"}))

        player.media.notify_dialog.assert_not_called()
        player.shutdown()

    def test_session_gate_drop_does_not_speak(self):
        """A satellite session drop on a gated topic is correct silent
        behavior pre-session-work, not a malformed request - it must not
        speak invalid.request either."""
        from ovos_bus_client.session import Session
        bus = FakeBus()
        player = _make_real_player(bus)
        player.media.notify_dialog.reset_mock()

        msg = Message("ovos.common_play.play",
                      {"media": {"uri": "http://example.com/x.mp3",
                                "title": "X"}},
                      context={"session": Session("not-default").serialize()})
        bus.emit(msg)

        player.media.notify_dialog.assert_not_called()
        player.shutdown()

    def test_malformed_play_from_a_non_default_session_does_not_speak(self):
        """The gate must be checked BEFORE deciding to notify a decoder
        rejection, not just before dispatch - the earlier version notified
        on message.data.get(...) rejection regardless of session, so a
        satellite's malformed play spoke locally even though the topic is
        gated. In a HiveMind split that double-speaks: the satellite's own
        embedded daemon rejects-and-speaks, AND the server daemon (which
        also gets the forwarded message) speaks too. The prior
        test_session_gate_drop_does_not_speak used a VALID payload, so it
        stayed green even with the bug - only a malformed payload on a
        gated, non-default session exercises the ordering."""
        from ovos_bus_client.session import Session
        bus = FakeBus()
        player = _make_real_player(bus)
        player.media.notify_dialog.reset_mock()

        msg = Message("ovos.common_play.play",
                      {"media": {"unrelated": "no uri, no playlist"}},
                      context={"session": Session("satellite-1").serialize()})
        bus.emit(msg)

        player.media.notify_dialog.assert_not_called()
        player.shutdown()

    def test_decoder_raising_valueerror_also_respects_the_session_gate(self):
        """decode_media only ever returns None, never raises - this drives
        the ValueError branch directly via a monkeypatched decoder so that
        branch's session-gate handling (added alongside the None-branch
        fix) is covered by something other than reading the diff. Not a
        claim that decode_media raises in practice."""
        from ovos_bus_client.session import Session
        from ovos_media.bus.api import BusHandler

        bus = FakeBus()
        player = _make_real_player(bus)
        player.media.notify_dialog.reset_mock()

        def _raising_decoder(data):
            raise ValueError("synthetic decode failure")

        for i, entry in enumerate(player.bus_api.table):
            if entry.topic == "ovos.common_play.play" and \
                    entry.target == player.handle_play_request:
                player.bus_api.table[i] = BusHandler(
                    entry.topic, entry.target, decoder=_raising_decoder,
                    gated=entry.gated, dispatch=entry.dispatch,
                    reject_dialog=entry.reject_dialog)
                replaced = player.bus_api.table[i]
                break
        else:
            self.fail("could not find the player's play entry to patch")

        listener = player.bus_api._wrap(replaced)

        # default session: the ValueError branch notifies
        listener(Message("ovos.common_play.play", {"media": {"uri": "x"}}))
        player.media.notify_dialog.assert_called_once_with("invalid.request")

        # non-default, gated session: the ValueError branch must NOT notify
        player.media.notify_dialog.reset_mock()
        listener(Message("ovos.common_play.play", {"media": {"uri": "x"}},
                        context={"session": Session("satellite-1").serialize()}))
        player.media.notify_dialog.assert_not_called()

        player.shutdown()


# ---------------------------------------------------------------------------
# UX-008: seek() must announce cannot.seek when unsupported, not just log.
# ---------------------------------------------------------------------------

class TestSeekUnsupportedAnnounces(unittest.TestCase):

    def test_seek_skill_type_announces_cannot_seek(self):
        p = make_player(PlaybackType.SKILL)
        with patch("ovos_media.player.LOG"):
            p.seek(60000)
        p.media.notify_dialog.assert_called_once_with("cannot.seek")

    def test_seek_audio_type_does_not_announce(self):
        p = make_player(PlaybackType.AUDIO)
        p.seek(60000)
        p.media.notify_dialog.assert_not_called()

    def test_seek_skill_type_delegates_when_can_seek_is_declared(self):
        # OCP-1 §4.3.1: a skill that declared `can_seek: true` gets the
        # delegated seek instead of the cannot.seek fallback
        p = make_player(PlaybackType.SKILL)
        p.media.can_seek.return_value = True
        emitted = []
        p.bus.on("message", lambda m: emitted.append(Message.deserialize(m)))
        p.seek(60000)
        p.media.notify_dialog.assert_not_called()
        types = [m.msg_type for m in emitted]
        self.assertIn("ovos.common_play.test.skill.seek", types)
        seek_msg = next(m for m in emitted
                         if m.msg_type == "ovos.common_play.test.skill.seek")
        self.assertEqual(seek_msg.data, {"seekValue": 60000})


if __name__ == "__main__":
    unittest.main()

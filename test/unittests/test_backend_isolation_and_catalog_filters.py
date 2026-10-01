"""Backend selection, skill-announce filtering, liked-songs locking and
playback navigation, and the isolation each of them owes the others.

handle_skill_announce stores media_types as a set of members, so the
parental filter in get_featured_skills() can actually see MediaType.ADULT
in it.
BaseMediaService._safe_supported_uris() normalises whatever
supported_uris() returns into a list, so a plugin returning a bare str
cannot give substring semantics downstream ("file" in "filesystem") and
select the wrong backend.
BaseMediaService.available_backends() guards the whole per-service body,
the .name access included, so one misbehaving backend cannot kill the
listing.
OCPMediaPlayer.play_prev routes to the skill only on
PlaybackType.SKILL — symmetric with play_next. An idle "previous", whose
playback type is the UNDEFINED default, reaches the merged-queue logic
instead of emitting a skill-control message.
"""
import threading
import unittest
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import MediaType, PlaybackType


# ---------------------------------------------------------------------------
# handle_skill_announce / get_featured_skills adult filter
# ---------------------------------------------------------------------------

def _make_catalog():
    from ovos_media.catalog import LikedSongsStore
    from ovos_media.player import OCPMediaCatalog

    class _FakeStore(dict):
        def store(self):
            pass

    bus = FakeBus()
    with patch("ovos_media.player.load_stream_extractors"):
        cat = OCPMediaCatalog(bus=bus, likes=LikedSongsStore(_FakeStore()))
    return cat, bus


class TestAdultFilterBypass(unittest.TestCase):

    def _announce(self, cat, skill_id, media_types):
        cat.handle_skill_announce(Message("ovos.common_play.announce", {
            "skill_id": skill_id,
            "skill_name": skill_id,
            "featured_tracks": ["some_track"],
            "media_types": media_types,
        }))

    def test_set_containing_adult_is_excluded_from_featured_skills(self):
        cat, bus = _make_catalog()
        self._announce(cat, "adult.skill", {MediaType.ADULT, MediaType.MUSIC})
        skills = cat.get_featured_skills(adult=False)
        self.assertNotIn("adult.skill", [s["skill_id"] for s in skills])
        # control: adult=True still returns it
        skills_adult = cat.get_featured_skills(adult=True)
        self.assertIn("adult.skill", [s["skill_id"] for s in skills_adult])

    def test_set_containing_hentai_is_excluded_from_featured_skills(self):
        cat, bus = _make_catalog()
        self._announce(cat, "hentai.skill", frozenset({MediaType.HENTAI}))
        skills = cat.get_featured_skills(adult=False)
        self.assertNotIn("hentai.skill", [s["skill_id"] for s in skills])

    def test_set_without_adult_is_included(self):
        cat, bus = _make_catalog()
        self._announce(cat, "music.skill", {MediaType.MUSIC})
        skills = cat.get_featured_skills(adult=False)
        self.assertIn("music.skill", [s["skill_id"] for s in skills])

    def test_scalar_media_type_still_normalizes(self):
        cat, bus = _make_catalog()
        self._announce(cat, "scalar.skill", MediaType.MUSIC)
        skills = cat.get_featured_skills(adult=False)
        self.assertIn("scalar.skill", [s["skill_id"] for s in skills])

    def test_list_media_type_still_works(self):
        cat, bus = _make_catalog()
        self._announce(cat, "list.skill", [MediaType.ADULT])
        skills = cat.get_featured_skills(adult=False)
        self.assertNotIn("list.skill", [s["skill_id"] for s in skills])

    def test_dict_keys_containing_adult_is_excluded_from_featured_skills(self):
        cat, bus = _make_catalog()
        d = {MediaType.ADULT: True, MediaType.MUSIC: True}
        self._announce(cat, "dictkeys.skill", d.keys())
        skills = cat.get_featured_skills(adult=False)
        self.assertNotIn("dictkeys.skill", [s["skill_id"] for s in skills])

    def test_generator_yielding_adult_is_excluded_from_featured_skills(self):
        cat, bus = _make_catalog()

        def gen():
            yield MediaType.ADULT
            yield MediaType.MUSIC

        self._announce(cat, "gen.skill", gen())
        skills = cat.get_featured_skills(adult=False)
        self.assertNotIn("gen.skill", [s["skill_id"] for s in skills])

    def test_nested_list_of_set_containing_adult_is_excluded_from_featured_skills(self):
        cat, bus = _make_catalog()
        self._announce(cat, "nested.skill", [{MediaType.ADULT}])
        skills = cat.get_featured_skills(adult=False)
        self.assertNotIn("nested.skill", [s["skill_id"] for s in skills])

    def test_flat_list_without_adult_is_included(self):
        cat, bus = _make_catalog()
        self._announce(cat, "safe.skill", [MediaType.MUSIC, MediaType.PODCAST])
        skills = cat.get_featured_skills(adult=False)
        self.assertIn("safe.skill", [s["skill_id"] for s in skills])


# ---------------------------------------------------------------------------
# _safe_supported_uris / available_backends
# ---------------------------------------------------------------------------

def _make_base_svc(services=None):
    from ovos_media.media_backends.base import BaseMediaService
    from ovos_utils.process_utils import MonotonicEvent
    bus = FakeBus()
    svc = BaseMediaService.__new__(BaseMediaService)
    svc._init_runtime_state()
    svc.bus = bus
    svc.namespace = "audio"
    svc.config = {}
    svc.plugin_loader = lambda: {}
    svc.default = None
    svc.services = services or []
    svc.current = None
    svc.play_start_time = 0
    svc.volume_is_low = False
    svc.service_lock = threading.Lock()
    svc._loaded = MonotonicEvent()
    svc._loaded.set()
    return svc, bus


class _StrUriBackend:
    """Malformed plugin: supported_uris() returns a bare str."""
    def __init__(self, name="badstr"):
        self.name = name

    def supported_uris(self):
        return "filesystem"


class _ListUriBackend:
    def __init__(self, name="good", uris=None):
        self.name = name
        self._uris = uris or ["file"]

    def supported_uris(self):
        return self._uris


class _NameRaisesBackend:
    """Malformed plugin: .name raises."""
    @property
    def name(self):
        raise RuntimeError("boom")

    def supported_uris(self):
        return ["http"]


class TestSafeSupportedUrisValidatesReturn(unittest.TestCase):

    def test_str_return_is_not_selected_via_substring(self):
        from ovos_media.media_backends.base import _safe_supported_uris
        b = _StrUriBackend()
        uris = _safe_supported_uris(b)
        self.assertEqual(uris, [])
        self.assertNotIn("file", uris)

    def test_list_return_is_used_normally(self):
        from ovos_media.media_backends.base import _safe_supported_uris
        b = _ListUriBackend(uris=["file"])
        self.assertEqual(_safe_supported_uris(b), ["file"])

    def test_backend_selection_skips_str_returning_backend(self):
        """End-to-end: play() must not select a backend whose
        supported_uris() returns "filesystem" for uri_type "file"."""
        from test_media_backends import _FullFakeBackend
        bad = _StrUriBackend(name="badstr")
        good = _FullFakeBackend(uris=["file"], name="goodfile")
        svc, bus = _make_base_svc(services=[bad, good])
        svc._play("file://track.mp3")
        # only the real list-returning backend should ever get selected
        self.assertIsNotNone(svc.current)
        self.assertEqual(svc.current.name, "goodfile")


class TestAvailableBackendsToleratesRaisingService(unittest.TestCase):

    def test_name_raising_backend_is_skipped_not_fatal(self):
        raiser = _NameRaisesBackend()
        good = _ListUriBackend(name="good", uris=["http"])
        svc, bus = _make_base_svc(services=[raiser, good])
        result = svc.available_backends()
        self.assertIn("good", result)
        self.assertEqual(len(result), 1)


class TestGetPreferredPlayersToleratesRaisingService(unittest.TestCase):

    def test_name_raising_backend_is_skipped_not_fatal(self):
        raiser = _NameRaisesBackend()
        good = _ListUriBackend(name="good", uris=["http"])
        svc, bus = _make_base_svc(services=[raiser, good])
        result = svc.get_preferred_players()
        self.assertEqual(result, ["good"])

    def test_all_good_backends_are_all_returned(self):
        a = _ListUriBackend(name="a", uris=["http"])
        b = _ListUriBackend(name="b", uris=["http"])
        svc, bus = _make_base_svc(services=[a, b])
        result = svc.get_preferred_players()
        self.assertEqual(set(result), {"a", "b"})


# ---------------------------------------------------------------------------
# play_prev must only defer to skills for PlaybackType.SKILL
# ---------------------------------------------------------------------------

def _make_player(playback_type=PlaybackType.AUDIO):
    from ovos_media.player import OCPMediaPlayer
    from ovos_utils.ocp import PlayerState, MediaState, LoopState

    with patch("ovos_media.player.AudioService"), \
         patch("ovos_media.player.VideoService"), \
         patch("ovos_media.player.WebService"), \
         patch("ovos_media.player.OcpMprisExporter"), \
         patch("ovos_media.player.Configuration", return_value={"media": {}}), \
         patch("ovos_media.player.OCPMediaCatalog"):
        p = OCPMediaPlayer.__new__(OCPMediaPlayer)
        p._init_runtime_state()
        p.ocp_config = {}
        p.state = PlayerState.STOPPED
        p.loop_state = LoopState.NONE
        p.media_state = MediaState.NO_MEDIA
        p.shuffle = False
        p.track_history = {}
        p._paused_on_duck = False
        p.now_playing = MagicMock()
        p.now_playing.playback = playback_type
        p.now_playing.skill_id = "test.skill"
        p.now_playing.uri = "http://example.com/track.mp3"
        p.playlist = MagicMock()
        p.playlist.entries = []
        p.media = MagicMock()
        p.media.search_playlist.entries = []
        p.current = None
        p.mpris = None
        p.bus = FakeBus()
    return p


class TestPlayPrevSkillOnly(unittest.TestCase):

    def test_undefined_does_not_defer_to_skill(self):
        p = _make_player(PlaybackType.UNDEFINED)
        emitted = []
        p.bus.emit = lambda m: emitted.append(m)
        p.ocp_config = {"merge_search": False}
        p.play_prev()
        prev_msgs = [m for m in emitted
                    if m.msg_type == f"ovos.common_play.{p.now_playing.skill_id}.previous"]
        self.assertEqual(prev_msgs, [])

    def test_skill_playback_type_still_defers_to_skill(self):
        p = _make_player(PlaybackType.SKILL)
        emitted = []
        p.bus.emit = lambda m: emitted.append(m)
        p.play_prev()
        prev_msgs = [m for m in emitted
                    if m.msg_type == f"ovos.common_play.{p.now_playing.skill_id}.previous"]
        self.assertEqual(len(prev_msgs), 1)


if __name__ == "__main__":
    unittest.main()

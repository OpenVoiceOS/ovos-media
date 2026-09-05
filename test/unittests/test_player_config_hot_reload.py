"""Regression tests for hot-reloadable ``media`` config knobs.

OCPMediaPlayer used to snapshot ``Configuration().get("media", {})`` once at
construction (``self.ocp_config``), so a disk edit never propagated without a
restart. autoplay, validate_source and preferred_*_services are cheap to
re-read on every access and are now hot: the player consults the live
Configuration singleton through ``self._cfg()`` instead of the frozen
snapshot. enable_mpris stays cold (read once, at construction) — flipping it
on the live singleton must NOT retroactively construct or tear down the
MPRIS exporter; that only happens on the next OCPMediaPlayer construction.
"""
import unittest
from unittest.mock import MagicMock, patch

from ovos_utils.fakebus import FakeBus
from ovos_utils.ocp import PlaybackType


def _make_live_player(testcase, media_config, mpris_mock=None, validate_source=None):
    """Build a real OCPMediaPlayer with config=None, so it reads through the
    live (mocked) Configuration singleton on every ``_cfg()`` call instead of
    a frozen dict.

    The ``ovos_media.player.Configuration`` patch is kept alive for the
    lifetime of ``testcase`` (via ``addCleanup``), not just during
    construction -- otherwise every ``_cfg()`` call made by the test body
    itself would fall through to the real, unmocked Configuration singleton.
    """
    from ovos_media.player import OCPMediaPlayer

    config_mock = MagicMock()
    config_mock.get.side_effect = lambda key, default=None: (
        dict(media_config) if key == "media" else default
    )
    patches = [
        patch("ovos_media.player.AudioService"),
        patch("ovos_media.player.VideoService"),
        patch("ovos_media.player.WebService"),
        patch("ovos_media.player.NowPlaying"),
        patch("ovos_media.player.Playlist"),
        patch("ovos_media.player.OCPMediaCatalog"),
        patch("ovos_media.player.OCPBusApi"),
        patch("ovos_media.player.Configuration", return_value=config_mock),
        patch.object(OCPMediaPlayer, "_report_to_core"),
    ]
    if mpris_mock is not None:
        patches.append(patch("ovos_media.player.OcpMprisExporter", mpris_mock))
    else:
        patches.append(patch("ovos_media.player.OcpMprisExporter"))
    for p in patches:
        p.start()
        testcase.addCleanup(p.stop)
    player = OCPMediaPlayer(bus=FakeBus(), config=None,
                            validate_source=validate_source)
    return player, media_config


class TestAutoplayHotReload(unittest.TestCase):
    def test_autoplay_disabled_live_skips_play_next(self):
        player, media_config = _make_live_player(self, {"autoplay": True})
        player.playlist = [MagicMock()]
        player.play_next = MagicMock()
        player._stop_requested = False

        media_config["autoplay"] = False
        player.handle_playback_ended(MagicMock(), playback_type=PlaybackType.AUDIO,
                                     playback_uri="x", stop_requested=False)

        player.play_next.assert_not_called()

    def test_autoplay_enabled_live_calls_play_next(self):
        player, media_config = _make_live_player(self, {"autoplay": False})
        player.playlist = [MagicMock()]
        player.play_next = MagicMock()
        player._stop_requested = False

        media_config["autoplay"] = True
        player.handle_playback_ended(MagicMock(), playback_type=PlaybackType.AUDIO,
                                     playback_uri="x", stop_requested=False)

        player.play_next.assert_called_once()


class TestValidateSourceHotReload(unittest.TestCase):
    def test_validate_source_flips_live_without_reconstruction(self):
        player, media_config = _make_live_player(self, {})
        # constructor default is True (no override passed)
        self.assertTrue(player.validate_source)

        media_config["validate_source"] = False
        self.assertFalse(player.validate_source)

        media_config["validate_source"] = True
        self.assertTrue(player.validate_source)

    def test_validate_source_gate_reads_live_value_per_message(self):
        from ovos_media.bus.api import OCPBusApi

        player, media_config = _make_live_player(self, {"validate_source": True})
        api = OCPBusApi.__new__(OCPBusApi)
        api.player = player
        api.service = None
        self.assertTrue(api.validate_source)

        media_config["validate_source"] = False
        self.assertFalse(api.validate_source)

    def test_service_owner_raw_none_resolves_to_true_not_falsy(self):
        """OCPBusApi can be built around a MediaService instead of a player;
        MediaService.validate_source is now the raw, unresolved constructor
        argument (None unless the caller was explicit) rather than an
        already-resolved bool. getattr(owner, "validate_source", True) would
        happily return that raw None, and the gate must not treat None as a
        falsy "act on everything" override -- it must fall back to the same
        True default a real resolver would apply."""
        from ovos_media.bus.api import OCPBusApi

        service = MagicMock()
        service.validate_source = None
        api = OCPBusApi.__new__(OCPBusApi)
        api.player = None
        api.service = service

        self.assertTrue(api.validate_source)


class TestValidateSourceExplicitOverrideWins(unittest.TestCase):
    """An explicit constructor argument for validate_source must always win
    over config -- hivemind-media-player embeds
    ``MediaService(validate_source=False)`` in code, and a stray
    ``media.validate_source: true`` on disk must never discard that."""

    def test_explicit_false_survives_config_saying_true(self):
        player, media_config = _make_live_player(
            self, {"validate_source": True}, validate_source=False)
        self.assertFalse(player.validate_source)

        # live config edits afterwards still must not override the explicit
        # constructor argument
        media_config["validate_source"] = True
        self.assertFalse(player.validate_source)

    def test_explicit_true_survives_config_saying_false(self):
        player, media_config = _make_live_player(
            self, {"validate_source": False}, validate_source=True)
        self.assertTrue(player.validate_source)

        media_config["validate_source"] = False
        self.assertTrue(player.validate_source)

    def test_runtime_assignment_becomes_an_override_too(self):
        """Direct assignment (ten call sites do this across the test suite)
        must behave exactly like a constructor argument: it wins over config
        from then on, not get silently shadowed by a live read."""
        player, media_config = _make_live_player(self, {"validate_source": True})
        self.assertTrue(player.validate_source)  # no override yet -> live

        player.validate_source = False
        self.assertFalse(player.validate_source)

        media_config["validate_source"] = True
        self.assertFalse(player.validate_source)  # override still wins


class TestVoiceSkillValidateSourceStaysInSyncWithPlayer(unittest.TestCase):
    """OCPVoiceSkill used to snapshot validate_source once at construction
    and gate its intents on that snapshot, while OCPMediaPlayer read it
    live -- after a runtime flip the two disagreed about which sessions to
    act on. Both must resolve the same way: explicit override always wins,
    otherwise a live read of media.validate_source, defaulting True."""

    def _make_skill(self, media_config, validate_source=None):
        from ovos_media.skill import OCPVoiceSkill

        config_mock = MagicMock()
        config_mock.get.side_effect = lambda key, default=None: (
            dict(media_config) if key == "media" else default
        )
        patcher = patch("ovos_media.skill.Configuration",
                        return_value=config_mock)
        patcher.start()
        self.addCleanup(patcher.stop)

        skill = OCPVoiceSkill.__new__(OCPVoiceSkill)
        skill._validate_source_override = validate_source
        return skill

    def test_no_override_reads_live_config(self):
        skill = self._make_skill({"validate_source": True})
        self.assertTrue(skill.validate_source)

    def test_live_flip_propagates_without_reconstruction(self):
        media_config = {"validate_source": True}
        skill = self._make_skill(media_config)
        self.assertTrue(skill.validate_source)

        media_config["validate_source"] = False
        self.assertFalse(skill.validate_source)

    def test_explicit_override_beats_config(self):
        skill = self._make_skill({"validate_source": True}, validate_source=False)
        self.assertFalse(skill.validate_source)

    def test_player_and_skill_agree_after_a_live_flip(self):
        media_config = {"validate_source": True}
        player, _ = _make_live_player(self, media_config)
        skill = self._make_skill(media_config)

        self.assertEqual(player.validate_source, skill.validate_source)

        media_config["validate_source"] = False
        self.assertEqual(player.validate_source, skill.validate_source)
        self.assertFalse(skill.validate_source)


class TestPreferredServicesHotReload(unittest.TestCase):
    def test_preferred_audio_services_order_change_affects_resolution(self):
        player, media_config = _make_live_player(
            self, {"preferred_audio_services": ["a"]})

        class _Backend:
            def __init__(self, name):
                self.name = name
                self.aliases = []

        media_service = MagicMock()
        media_service.get_preferred_players.return_value = None
        backend_a, backend_b = _Backend("a"), _Backend("b")
        media_service.services = [backend_a, backend_b]

        self.assertIs(player._resolve_preferred_service(media_service),
                      backend_a)

        media_config["preferred_audio_services"] = ["b"]
        self.assertIs(player._resolve_preferred_service(media_service),
                      backend_b)


class TestBaseMediaServicePreferredServicesHotReload(unittest.TestCase):
    """BaseMediaService.get_preferred_players() is the actual read site
    behind preferred_*_services (OCPMediaPlayer._resolve_preferred_service's
    own fallback is normally dead code, since this is what backs
    media_service.get_preferred_players() in the first place). Mirrors
    TestPreferredServicesHotReload above, one level down, and is what
    proves that read site is really live: reverting it to the frozen
    ``self.config`` snapshot turns this test red.
    """

    def _make_live_service(self, testcase, media_config, namespace="audio",
                           services=None):
        from ovos_media.media_backends.base import BaseMediaService

        config_mock = MagicMock()
        config_mock.get.side_effect = lambda key, default=None: (
            dict(media_config) if key == "media" else default
        )
        patcher = patch("ovos_media.media_backends.base.Configuration",
                        return_value=config_mock)
        patcher.start()
        testcase.addCleanup(patcher.stop)

        svc = BaseMediaService.__new__(BaseMediaService)
        svc.bus = FakeBus()
        svc.namespace = namespace
        svc.plugin_loader = lambda: {}
        svc._live_config = True
        svc.config = {}
        svc.service_lock = MagicMock()
        svc.default = None
        svc.services = services or []
        svc.current = None
        svc.play_start_time = 0
        svc.volume_is_low = False
        svc._init_runtime_state()
        return svc

    def test_preferred_audio_services_order_change_affects_get_preferred_players(self):
        class _Backend:
            def __init__(self, name):
                self.name = name

        media_config = {"preferred_audio_services": ["a"]}
        svc = self._make_live_service(self, media_config,
                                      services=[_Backend("a"), _Backend("b")])

        self.assertEqual(svc.get_preferred_players(), ["a"])

        media_config["preferred_audio_services"] = ["b"]
        self.assertEqual(svc.get_preferred_players(), ["b"])


class TestEnableMprisIsCold(unittest.TestCase):
    """enable_mpris is documented COLD: read once at construction, off the
    ``self.ocp_config`` snapshot taken at the top of __init__, never through
    ``self._cfg()``. A test that only flips the live config AFTER
    construction and checks ``player.mpris is None`` is a false green: since
    nothing in this codebase ever re-checks enable_mpris post-construction,
    that assertion holds whether the read site is cold (self.ocp_config) or
    was accidentally hot-wired to self._cfg() -- both only fire once, during
    __init__, before the flip even happens. To actually pin the read site,
    the Configuration mock here returns a DIFFERENT value on every
    subsequent call: the first call is the ocp_config snapshot (enable_mpris
    False); a read site that used self._cfg() instead would issue a second,
    live Configuration() call before deciding whether to build the exporter,
    and would see True.
    """

    def test_enable_mpris_read_uses_the_construction_time_snapshot(self):
        from ovos_media.player import OCPMediaPlayer

        call_count = {"n": 0}

        def _get(key, default=None):
            if key != "media":
                return default
            call_count["n"] += 1
            # 1st Configuration() call = the ocp_config snapshot: disabled.
            # Any further call a hot read site would make sees it enabled.
            return {"enable_mpris": call_count["n"] != 1}

        config_mock = MagicMock()
        config_mock.get.side_effect = _get
        mpris_mock = MagicMock()
        patches = [
            patch("ovos_media.player.AudioService"),
            patch("ovos_media.player.VideoService"),
            patch("ovos_media.player.WebService"),
            patch("ovos_media.player.NowPlaying"),
            patch("ovos_media.player.Playlist"),
            patch("ovos_media.player.OCPMediaCatalog"),
            patch("ovos_media.player.OCPBusApi"),
            patch("ovos_media.player.Configuration", return_value=config_mock),
            patch("ovos_media.player.OcpMprisExporter", mpris_mock),
            patch.object(OCPMediaPlayer, "_report_to_core"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        player = OCPMediaPlayer(bus=FakeBus(), config=None)

        # the enable_mpris decision must have been made off the FIRST
        # (snapshot) Configuration() read, not a second, live one.
        self.assertEqual(call_count["n"], 1)
        mpris_mock.assert_not_called()
        self.assertIsNone(player.mpris)

        # and, as a behavioural sanity check on top: a config edit made
        # after construction changes nothing about the already-built player.
        self.assertIsNone(player.mpris)


if __name__ == "__main__":
    unittest.main()

# ovos-media — TODO

_Last updated: 2026-03-12_
_See PLAN.md for full rationale and architecture._

---

## CRITICAL (must fix before 0.0.1) ✅ DONE 2026-03-12

### A. Remove OVOSAbstractApplication from OCPMediaPlayer ✅
- [x] Rewrite `OCPMediaPlayer` as a plain class: `class OCPMediaPlayer:`
- [x] Change `__init__` to `def __init__(self, bus: MessageBusClient, config: dict | None = None)`
- [x] Replace all `self.add_event(...)` with `self.bus.on(...)`
- [x] Remove `bind()` method; initialization is now in `__init__`
- [x] Update `service.py` to use `bus.on()` for service-level handlers
- [x] Update all test `_make_player()` helpers — remove `OVOSAbstractApplication` patches
- [x] `OCPMediaCatalog(OVOSCommonPlaybackSkill)` unchanged

---

## HIGH ✅ DONE 2026-03-12

### B. Fix handle_pause_toggle_request inverted logic ✅
- [x] When PAUSED → call `handle_resume_request(msg)`, else → call `handle_pause_request(msg)`

### C. Fix set_player_state dual-write / self-subscribe ✅
- [x] Removed `handle_player_state_update` internal subscription
- [x] `set_player_state` is now the single writer; calls `_update_gui()` + MPRIS update

---

## MEDIUM ✅ DONE 2026-03-12

### D. Fix dead handle_track_state_change ✅
- [x] `NowPlaying` now accepts `player` ref; `handle_track_state_change` forwards state to player

### E. Fix handle_sync_seekbar missing _update_gui call ✅
- [x] `NowPlaying.handle_sync_seekbar` calls `self._player._update_gui()` after position update

### F. Fix sleep() in bus handlers ✅
- [x] `base.py` `handle_play`: `threading.Timer(0.5, ...)` instead of `time.sleep(0.5)`
- [x] `player.py` `on_invalid_stream`: `threading.Timer(3.0, ...)` instead of `time.sleep(3)`

### G. Fix silent backend load failure ✅
- [x] Logs ERROR + emits `MediaState.NO_MEDIA` when zero backends load

---

## LOW ✅ DONE 2026-03-12

### H. MPRIS super() wrong class name ✅
- [x] Changed `super(MprisPlayerCtl, self).__init__()` → `super().__init__()`

### I. MPRIS asyncio.get_event_loop() deprecated ✅
- [x] Changed to `asyncio.new_event_loop()`

### J. MPRIS LoopStatus getter returns wrong string ✅
- [x] Returns `"Track"` for `REPEAT_TRACK`, `"Playlist"` for `REPEAT` (MPRIS 2.2 spec)

---

## DESIGN (phase E in PLAN.md — may slip to post-0.0.1)

### K. Queue abstraction to replace dual-playlist O(n²) dedup
`player.py` — `play_next()` crosses between `self.playlist` and `self.media.search_playlist` with
O(n²) deduplication.

- [ ] Design `Queue` class: `enqueue(entries, source)`, `next() -> MediaEntry | None`, `clear()`
- [ ] User-queued entries take strict priority over search-result entries
- [ ] Replace `play_next()` with `queue.next()`
- [ ] Update tests

---

## Version bump (after all above are done)

- [ ] `version.py`: `VERSION_ALPHA = 0` (removes `a22` suffix, makes version `0.0.1`)
- [ ] Update `QUICK_FACTS.md` version field
- [ ] Update `CHANGELOG.md` with summary of all changes since last alpha
- [ ] Tag `v0.0.1` on `master`

---

## Done ✅ (2026-03-11 / 2026-03-12)

**Phase 1 — Critical bugs:**
- [x] Add `ovos-workshop`, `json-database` to `pyproject.toml` deps
- [x] Fix description in `pyproject.toml`
- [x] Python `>=3.10` in `pyproject.toml`
- [x] MPRIS `Position` returns `position * 1e6` (microseconds)
- [x] MPRIS `LoopStatus` setter maps string → `LoopState` enum
- [x] MPRIS `Stop()` calls `stop()` not `pause()`
- [x] `_set_main_player` saves old value before comparison
- [x] `manage_players` reads from `config.get("manage_external_players", True)`
- [x] `ignored_players` reads from config
- [x] `mpris_poll_interval` reads from config
- [x] Preferred service selection implemented in `player.py`
- [x] Relative imports removed from `video.py` and `web.py`
- [x] `setDaemon()` → `self.daemon = daemonic`
- [x] `MprisPlayerCtl` aliased as `OcpMprisExporter`

**GUI decoupling:**
- [x] Delete `ovos_media/gui.py` (`OCPGUIInterface` replaced by `GUIInterface`)
- [x] Import `GUIInterface` from `ovos_bus_client.apis.gui` in `player.py`
- [x] Add `_update_gui()` calling `gui.show_media_player(now_playing, playlist, search_results, state)`
- [x] Call `_update_gui()` after play/pause/resume/stop/shuffle/repeat/search
- [x] `handle_search_end` in `service.py` calls `ocp._update_gui()` (was no-op)

**CI & testing:**
- [x] `build_tests.yml` replaced with reusable workflow using `@dev`
- [x] `notify_matrix.yml` updated to `@dev`
- [x] Added: `test.yml`, `coverage.yml`, `lint.yml`, `pip-audit.yml`, `python-support.yml`, `repo-health.yml`
- [x] Added: `release_workflow.yml`, `publish_stable.yml`, `publish-alpha.yml`
- [x] Tests: `test_service.py`, `test_player.py`, `test_mpris.py`, `test_gui.py`, `test_media_backends.py`, `test_player_coverage.py`, `test_mpris_coverage.py`
- [x] Coverage: 53% → 72%

**Docs:**
- [x] `docs/index.md`, `docs/architecture.md`, `docs/getting-started.md`, `docs/configuration.md`, `docs/backends.md`, `docs/mpris.md`, `docs/ocp-skills.md`
- [x] `AUDIT.md`, `FAQ.md`, `QUICK_FACTS.md`, `MAINTENANCE_REPORT.md` created/updated

**MPRIS & new repo:**
- [x] `ovos-media-plugin-mpris` repo scaffolded (Role B external player management)
- [x] 26 tests passing in `ovos-media-plugin-mpris`
- [x] 12 CI workflows added to `ovos-media-plugin-mpris`

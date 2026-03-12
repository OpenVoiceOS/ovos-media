# ovos-media — TODO

_Last updated: 2026-03-12_
_See PLAN.md for full rationale and architecture._

---

## CRITICAL (must fix before 0.0.1)

### A. Remove OVOSAbstractApplication from OCPMediaPlayer
`player.py` — `OCPMediaPlayer(OVOSAbstractApplication)` is wrong. `OVOSAbstractApplication` is an OVOSSkill
subclass; it drags in skill/intent machinery and causes `AttributeError: 'OCPMediaPlayer' object has no
attribute 'skill_id'` in every test teardown.

- [ ] Rewrite `OCPMediaPlayer` as a plain class: `class OCPMediaPlayer:`
- [ ] Change `__init__` signature to `def __init__(self, bus: MessageBusClient, config: dict | None = None) -> None:`
- [ ] Replace `self.add_event(...)` calls with `self.bus.on(...)` throughout
- [ ] Remove all `OVOSAbstractApplication.__init__`, `OVOSSkill`, `ovos_workshop` references from `player.py`
- [ ] Update `MediaService.__init__`: `self.ocp = OCPMediaPlayer(bus=self.bus, config=...)`
- [ ] Update `_make_player()` in all test files — remove `OVOSAbstractApplication.__init__` patch
- [ ] Run `uv run pytest test/ -v` — confirm zero `AttributeError: skill_id` failures
- [ ] Update `pyproject.toml`: remove `ovos-workshop` from deps if only used by OCPMediaPlayer

**Note:** `OCPMediaCatalog(OVOSCommonPlaybackSkill)` STAYS. Only `OCPMediaPlayer` base class changes.

---

## HIGH (should fix before 0.0.1)

### B. Fix handle_pause_toggle_request inverted logic
`player.py` — when `self.state == PlayerState.PAUSED`, current code calls `handle_pause_request` (pauses again).

- [ ] In `handle_pause_toggle_request`: when PAUSED → call `handle_resume_request(msg)`, else → call `handle_pause_request(msg)`

### C. Fix set_player_state dual-write / self-subscribe
`player.py` — `set_player_state` writes `self.state` AND emits `ovos.common_play.player.state`, then
`handle_player_state_update` subscribes to that same event and writes `self.state` again.

- [ ] Remove `handle_player_state_update` subscription inside `OCPMediaPlayer` (external consumers keep theirs)
- [ ] `set_player_state` becomes the single writer: `self.state = state; self._update_gui(); self.bus.emit(...)`
- [ ] Update tests that mock `handle_player_state_update` as an internal pathway

---

## MEDIUM (fix before 0.0.1)

### D. Fix dead handle_track_state_change
`player.py` — `NowPlaying.handle_track_state_change` subscribes to `ovos.common_play.track.state` but
every branch is `pass`. State changes from backends are silently dropped.

- [ ] Implement all branches: PLAYING_AUDIO/VIDEO/WEBVIEW → `PlayerState.PLAYING`; PAUSED → `PlayerState.PAUSED`; END_OF_MEDIA/ERROR → `play_next()`
- [ ] Add unit tests for each branch

### E. Fix handle_sync_seekbar missing _update_gui call
`player.py` — `handle_sync_seekbar` updates `self.now_playing.position` but never calls `_update_gui()`.
Scrubbar in GUI adapter never moves.

- [ ] Add `self._update_gui()` at end of `handle_sync_seekbar`
- [ ] Add test: after `handle_sync_seekbar`, `gui.show_media_player` is called with updated position

### F. Fix sleep() in bus handlers
Bus handlers must not block. Two violations:

- [ ] `media_backends/base.py` `handle_play`: replace `time.sleep(0.5)` with `threading.Timer(0.5, self.play, args=[uri, service]).start()`
- [ ] `player.py` `on_invalid_stream`: replace `time.sleep(3)` with `threading.Timer(3.0, self.play_next).start()`
- [ ] Remove `import time` if no longer needed
- [ ] Confirm tests still pass (mock `threading.Timer` where needed)

### G. Fix silent backend load failure
`media_backends/base.py` — when zero backends load, service silently continues. All playback calls
succeed with no error until play time.

- [ ] After backend loading loop: if `not self._loaded_backends`: log ERROR and emit `ovos.common_play.media.state` with `MediaState.NO_MEDIA`
- [ ] Add test: zero backends → error log + error state emitted

---

## LOW (fix before 0.0.1, trivial)

### H. MPRIS super() wrong class name
`mpris.py:74` — `super(MprisPlayerCtl, self).__init__()` uses old class name after rename to `OcpMprisExporter`.

- [ ] Replace with `super().__init__()`

### I. MPRIS asyncio.get_event_loop() deprecated
`mpris.py:77` — `asyncio.get_event_loop()` deprecated in Python 3.10+.

- [ ] Replace with `asyncio.new_event_loop()`

### J. MPRIS LoopStatus getter returns wrong string
`mpris.py` — `LoopStatus` getter returns `"RepeatTrack"` but MPRIS 2.2 spec requires `"Track"`.

- [ ] Return `"Track"` for `LoopState.REPEAT_TRACK`

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

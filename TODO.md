# ovos-media — TODO

_Last updated: 2026-03-11_
_See PLAN.md for full rationale and architecture._

---

## Blocking (must finish before 0.0.1)

### GUI Decoupling ✅ (2026-03-12)
- [x] Delete `ovos_media/gui.py` (entire file — `OCPGUIInterface` is replaced by `GUIInterface`)
- [x] In `ovos_media/player.py`: import `GUIInterface` from `ovos_bus_client.apis.gui` (pending `ovos-gui-api-client` PyPI release)
- [x] In `OCPMediaPlayer.bind`: create `self.gui = GUIInterface("ovos.common_play", bus=self.bus)` and `self._last_search_results = []`
- [x] Add `_update_gui()` method to `OCPMediaPlayer` calling `gui.show_media_player(now_playing, playlist, search_results, state)`
- [x] Call `_update_gui()` after: `play()`, `pause()`, `resume()`, `stop()`, track state/media state updates, shuffle/repeat changes, search start/end
- [x] Remove all `manage_display()`, `update_buttons()`, `update_ocp_cards()`, QML references from `player.py`
- [x] Update `test/unittests/test_gui.py` to mock `GUIInterface.show_media_player` instead of `OCPGUIInterface`
- [x] Update `test/unittests/test_player.py` and `test_player_state.py` for new mock targets
- [x] Update `docs/index.md` — removed OCPGUIInterface section, added GUIInterface + show_media_player docs
- [x] Update `FAQ.md` — added Q: "How does ovos-media update the display?" and Q: "Who calls show_video_player/show_url?"
- [ ] Switch import to `from ovos_gui_api_client import GUIInterface` once `ovos-gui-api-client>=0.1.0` is published to PyPI
- [ ] Add `ovos-gui-api-client>=0.1.0,<1.0.0` to `pyproject.toml` dependencies once PyPI package exists

### Upstream: ovos-gui-api-client
- [ ] Add `show_media_player(now_playing, playlist, search_results, state)` method to `GUIInterface`
- [ ] Add `PageTemplates.SYSTEM_media_player` constant
- [ ] Write all `ocp_*` session keys (see `GUI_DESIGN.md §4.3a`)
- [ ] Add `handle_show_media_player` to `AbstractGUIPlugin` in `ovos-plugin-manager` (default no-op)
- [ ] Add `"SYSTEM_media_player": "handle_show_media_player"` to `_TEMPLATE_HANDLERS`
- [ ] Implement `handle_show_media_player` in `ovos-legacy-mycroft-gui-plugin` (maps to OCP QML screen)
- [ ] Implement `handle_show_media_player` in `ovos-gui-plugin-pyhtmx` (multi-view HTML page)

### Version bump
- [ ] Change `version.py` from `0.0.1a22` to `0.0.1`
- [ ] Update `pyproject.toml` version to match
- [ ] Update `QUICK_FACTS.md` version field

---

## High Priority (should finish before 0.0.1)

### MPRIS split
- [ ] Rename/refactor `MprisPlayerCtl` → `OcpMprisExporter` keeping only Role A (D-Bus server, OCP state export)
- [ ] Strip all polling, external player detection, `manage_external_players` logic from `OcpMprisExporter`
- [ ] Add graceful D-Bus unavailability handling: `try/except DBusException` → `LOG.warning("MPRIS unavailable: no D-Bus session bus")`
- [ ] Create new repo: `ovos-media-plugin-mpris` (separate ticket/task — not blocking 0.0.1 if external player control is feature-flagged off)
- [ ] Until `ovos-media-plugin-mpris` exists: keep `manage_external_players` code behind a `config.get("manage_external_players", False)` guard (default OFF)
- [ ] Update `player.py` MPRIS dispatch: when `PlaybackType.MPRIS` and no MPRIS plugin installed, log warning and skip
- [ ] Update `AUDIT.md` — add "MPRIS external player management is stub until ovos-media-plugin-mpris is released"

### Tests
- [ ] Add test for `OcpMprisExporter` D-Bus unavailability graceful degradation
- [ ] Add test for `PlaybackType.MPRIS` dispatch with no plugin installed (should not crash)
- [ ] Reach ≥80% overall coverage (`uv run pytest --cov=ovos_media --cov-report=term-missing`)

---

## Normal Priority (post-0.0.1 / next alpha)

### `ovos-media-plugin-mpris` new repo
- [ ] Create repo with `MprisMediaPlugin(BaseMediaService)` implementing Role B
- [ ] Entry point: `opm.media.audio` → `MprisMediaPlugin`
- [ ] Config keys under `media.backends.mpris`: `manage_external_players`, `ignored_players`, `poll_interval`
- [ ] Implement `play(uri, preferred_service)` — route to matching external MPRIS player by bus name
- [ ] Implement auto-pause logic: when external player becomes active, emit `mycroft.audio.service.pause`
- [ ] Unit tests with mocked D-Bus external players

### `handle_search_end` implementation
- [ ] `ovos_media/service.py` — currently logs warning, implement actual search result merging/ranking logic
- [ ] File: `ovos_media/service.py` method `handle_search_end`

### Position live updates
- [ ] Evaluate: does `show_audio_player()` need periodic re-calls to update scrubbar, or does the GUI adapter poll MPRIS/bus?
- [ ] If push needed: add a periodic timer in `OCPMediaPlayer` that calls `_update_gui()` every 1s during playback
- [ ] Config key: `media.gui_update_interval` (default: 1)

### PipeWire / PulseAudio awareness
- [ ] Investigate whether external audio stream detection should complement MPRIS detection in `ovos-media-plugin-mpris`
- [ ] Add to SUGGESTIONS.md

---

## Repo creation needed

| New repo | Purpose | Blocks |
|---|---|---|
| `ovos-media-plugin-mpris` | External MPRIS player management as `BaseMediaService` | Full MPRIS feature parity |

---

## Done ✅ (2026-03-11)

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
- [x] `handle_search_end` logs warning
- [x] GUI JS policy reads from metadata/config (partial — superseded by full GUI decoupling)
- [x] `build_tests.yml` replaced with reusable workflow using `@dev`
- [x] `notify_matrix.yml` updated to `@dev`
- [x] Added: `test.yml`, `coverage.yml`, `lint.yml`, `pip-audit.yml`, `python-support.yml`, `repo-health.yml`
- [x] Tests: `test_service.py`, `test_player.py`, `test_mpris.py`, `test_gui.py`, `test_media_backends.py`
- [x] `docs/index.md`, `AUDIT.md`, `FAQ.md`, `QUICK_FACTS.md`, `MAINTENANCE_REPORT.md` created/updated

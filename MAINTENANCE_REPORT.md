
# Maintenance Report — `ovos-media`

## [2026-03-12] — GUI decoupling: replace OCPGUIInterface with show_media_player()

### Changes

#### Refactor
- **`ovos_media/gui.py`** — Deleted entirely. `OCPGUIInterface` and `OCPGUIState` are removed.
- **`ovos_media/player.py`** — Import changed from `from ovos_media.gui import OCPGUIInterface, OCPGUIState` to `from ovos_bus_client.apis.gui import GUIInterface`.
- **`ovos_media/player.py:390`** — `self.gui = OCPGUIInterface(); self.gui.bind(self)` replaced with `self.gui = GUIInterface("ovos.common_play", bus=self.bus)` and `self._last_search_results: list = []` initialised.
- **`ovos_media/player.py`** — Added `OCPMediaPlayer._update_gui()` method that calls `self.gui.show_media_player(now_playing, playlist, search_results, state)`.
- **`ovos_media/player.py`** — All `self.gui.manage_display(...)`, `self.gui.update_buttons()`, `self.gui.update_ocp_cards()` calls replaced with `self._update_gui()` or `self.gui.show_media_player(state="loading"/"error")`.
- **`ovos_media/service.py`** — Removed `from ovos_media.gui import OCPGUIState` import; `handle_home` now calls `self.ocp._update_gui()`; `handle_search_start` calls `self.ocp.gui.show_media_player(state="loading")`.

#### Tests
- **`test/unittests/test_gui.py`** — Fully rewritten. Tests now assert `show_media_player` is called with correct `state=` values on play/pause/stop/error/loading, and that `_update_gui()` is invoked by shuffle/repeat handlers.
- **`test/unittests/test_player.py`** — Updated patch target from `OCPGUIInterface` to `GUIInterface`; added `_last_search_results` and `playlist.as_list()` to mock setup.
- **`test/unittests/test_player_state.py`** — Same patch update; mock setup enriched.
- **`test/unittests/test_service.py`** — `test_handle_home_calls_manage_display` and `test_handle_search_start_shows_spinner` updated to match new API.

### Rationale
Decouples `ovos-media` from QML page management. `OCPMediaPlayer` now communicates intent
("show media player in state X") to GUI adapters via `show_media_player()`. Individual backend
plugins own their own rendering pages. This is a prerequisite for multi-GUI-backend support.

### Verification
```
uv run pytest test/ -v --tb=short
# 89 passed
```

### AI Transparency Report
- **AI Model**: claude-sonnet-4-6
- **Actions Taken**: Deleted `gui.py`; rewrote GUI integration in `player.py` and `service.py`; rewrote `test_gui.py`; updated 3 other test files; updated docs.
- **Oversight**: Human review and test run required before merging to `dev`.

---

## [2026-03-11] — Pre-release bug-fix and CI hardening

### Changes

#### Critical Bug Fixes
- **`ovos_media/mpris.py:803`** — `Position` property now returns `position * 1e6` (microseconds) instead of hardcoded `1`.
- **`ovos_media/mpris.py:775`** — `LoopStatus` setter now maps MPRIS strings (`"Track"`, `"Playlist"`, `"None"`) to `LoopState` enum values.
- **`ovos_media/mpris.py:840`** — `Stop()` now calls `self._ocp_player.stop()` (was `pause()`).
- **`ovos_media/mpris.py:229`** — `_set_main_player` fixed: saves old name before assignment, compares against old for LOG.
- **`ovos_media/mpris.py:93`** — `manage_players` now reads `config.get("manage_external_players", True)` instead of hardcoded `True`.
- **`ovos_media/player.py:736,749,754`** — Preferred backend service now resolved via `_resolve_preferred_service()` and passed to `play()`.
- **`ovos_media/media_backends/video.py`**, **`web.py`** — Relative imports replaced with absolute imports.

#### `pyproject.toml`
- Added `ovos-workshop>=0.0.15` and `json-database>=0.9.0` to `[project.dependencies]`.
- Fixed `description` (was copy-pasted from ovos-audio).
- Updated `requires-python` to `>=3.10`.

#### Important Improvements
- **`ovos_media/mpris.py:626,633`** — Poll interval now reads `config.get("mpris_poll_interval", 1)`.
- **`ovos_media/mpris.py:94`** — `ignored_players` now reads from config with sensible defaults.
- **`ovos_media/gui.py:112-115`** — `javascriptCanOpenWindows` and `allowUrlChange` now read from per-track infocard metadata with global config fallback.
- **`ovos_media/service.py:80`** — `handle_search_end` now logs a warning instead of silently passing.

#### CI Workflows
- **`build_tests.yml`** — Replaced broken inline workflow (YAML syntax error, Python 3.8) with reusable `gh-automations/build-tests.yml@dev`.
- **`notify_matrix.yml`** — Changed `@master` → `@dev`.
- Added 6 missing standard workflows: `test.yml`, `coverage.yml`, `lint.yml`, `pip-audit.yml`, `python-support.yml`, `repo-health.yml`.

#### Tests Added
- `test/unittests/test_service.py` — `MediaService` lifecycle, ping handler, search start/end handlers.
- `test/unittests/test_player.py` — `OCPMediaPlayer` state transitions, `_resolve_preferred_service` (name, alias, fallback, none).
- `test/unittests/test_mpris.py` — `Position` microseconds, `LoopStatus` setter enum mapping, `Stop()` calls stop, `manage_players` from config.

#### Documentation
- `FAQ.md` — Rewritten with migration guide, config reference, JS policy docs.
- `QUICK_FACTS.md` — Updated with correct description, Python 3.10+, key classes table, config keys.
- `AUDIT.md` — Updated with fixed issues and remaining open issues.

### Rationale
Pre-release checklist for `ovos-media` `0.0.1` stable. All critical bugs and CI blockers addressed.

### Verification
```bash
uv run pytest test/ -v --cov=ovos_media --cov-report=term-missing
python3 -c "from ovos_media.media_backends.video import VideoService; print('no relative imports')"
python3 -c "from ovos_media.media_backends.web import WebService; print('no relative imports')"
```

### AI Transparency Report
- **AI Model**: Claude Sonnet 4.6
- **Actions Taken**: Applied all critical bug fixes from pre-release checklist; added CI workflows; added unit tests; updated docs.
- **Oversight**: Human review and test run required before merging to `dev`.

---

## [2026-03-08] — Initial compliance scaffold

### Changes
- Created `QUICK_FACTS.md` with machine-readable package metadata.
- Created `FAQ.md` with common Q&A.
- Created `MAINTENANCE_REPORT.md` (this file) as the change log.
- Created `SUGGESTIONS.md` with initial improvement proposals.
- Created `docs/index.md` as the documentation entry point (if missing).

### Rationale
Establishing the required file set mandated by `AGENTS.md` for all active workspace repositories.

### AI Transparency Report
- **AI Model**: Claude Sonnet 4.6
- **Actions Taken**: Generated boilerplate compliance scaffold.
- **Oversight**: Files were stubs — human review required.


# Maintenance Report — `ovos-media`

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

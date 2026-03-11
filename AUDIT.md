
# ovos-media — Audit Report

## Documentation Status
- [x] QUICK_FACTS.md
- [x] FAQ.md
- [x] MAINTENANCE_REPORT.md
- [x] AUDIT.md
- [x] docs/index.md

## Fixed Issues (2026-03-11)
- `[CRITICAL]` **mpris**: `Position` property returned hardcoded `1` — fixed to return `position * 1e6` — `ovos_media/mpris.py:803`
- `[CRITICAL]` **mpris**: `LoopStatus` setter assigned raw string to `loop_state` instead of `LoopState` enum — `ovos_media/mpris.py:775`
- `[CRITICAL]` **mpris**: `Stop()` called `pause()` instead of `stop()` — `ovos_media/mpris.py:840`
- `[MAJOR]` **mpris**: `_set_main_player` always-False condition — LOG never fired on player change — `ovos_media/mpris.py:229`
- `[MAJOR]` **mpris**: `manage_players` hardcoded `True`, ignoring config — `ovos_media/mpris.py:93`
- `[MAJOR]` **player**: `play()` never passed preferred backend service — 3 TODO stubs — `ovos_media/player.py:736,749,754`
- `[MAJOR]` **deps**: `ovos-workshop` and `json-database` imported but not in `pyproject.toml`
- `[MAJOR]` **imports**: Relative imports in `video.py` and `web.py` — forbidden by CLAUDE.md
- `[MINOR]` **pyproject**: Wrong `description` (copy-paste from ovos-audio)
- `[MINOR]` **pyproject**: `requires-python = ">=3.9"` — updated to `>=3.10`
- `[MINOR]` **ci**: `build_tests.yml` had YAML syntax error and tested Python 3.8 (EOL)
- `[MINOR]` **ci**: `notify_matrix.yml` used `@master` ref
- `[MINOR]` **service**: `handle_search_end` was a no-op `pass`
- `[MINOR]` **mpris**: Poll interval hardcoded to `1` — now reads `mpris_poll_interval` from config
- `[MINOR]` **mpris**: `ignored_players` hardcoded — now reads from config
- `[MINOR]` **gui**: `javascriptCanOpenWindows` and `allowUrlChange` hardcoded `False` — now reads from per-track metadata and config

## Open Issues

### MPRIS
- `[MINOR]` `CanSeek` always returns `False` — seek position via MPRIS not implemented — `ovos_media/mpris.py:815`
- `[MINOR]` `Rate` property always returns `1` — playback speed control not exposed — `ovos_media/mpris.py:799`

### Service
- `[MAJOR]` `handle_search_end` still does not dismiss the GUI spinner — only logs a warning — `ovos_media/service.py:80`
- `[INFO]` `MediaService` class still has a `# TODO` comment — `ovos_media/service.py:32`

### CI
- `[MINOR]` `pypa/gh-action-pypi-publish` pinned to `@master` in publish workflows — should be `@release/v1`

## Missing Coverage (before 0.0.1 stable)
- `ovos_media/service.py` — `MediaService` — basic lifecycle tests added; handler tests added
- `ovos_media/player.py` — `OCPMediaPlayer` — preferred service, state transitions tested
- `ovos_media/mpris.py` — `MprisPlayerCtl`, `_MediaPlayer2PlayerInterface` — Position, LoopStatus, Stop, manage_players tested

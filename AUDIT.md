# Audit Report — `ovos-media`

Evidence-based record of known issues, technical debt, and security considerations.
All citations use `ClassName.method — path/to/file.py:LINE` format.

---

## Fixed Issues

### [2026-03-12] GUI Decoupling

| Severity | Issue | Fix |
|---|---|---|
| MAJOR | `OCPGUIInterface` (`ovos_media/gui.py`) coupled media service directly to QML page management | `gui.py` deleted; `OCPMediaPlayer` now calls `GUIInterface.show_media_player()` — `ovos_media/player.py:449` |

### [2026-03-12] MPRIS Refactor

| Severity | Issue | Fix |
|---|---|---|
| MAJOR | `MprisPlayerCtl` mixed D-Bus server (Role A) and external player management (Role B) with no separation | Renamed to `OcpMprisExporter`; `manage_external_players` defaults to `false`; alias kept for compat — `ovos_media/mpris.py:74` |
| MAJOR | `manage_players` was hardcoded `True`, ignoring config | Now reads `config.get("manage_external_players", False)` — `ovos_media/mpris.py:93` |
| MAJOR | D-Bus connect failure crashed the service | `try/except` added in `OcpMprisExporter.event_loop`; logs warning and returns cleanly — `ovos_media/mpris.py` |

### [2026-03-11] Pre-release Blockers

| Severity | Issue | Fix |
|---|---|---|
| CRITICAL | `Position` property returned hardcoded `1` | Returns `now_playing.position * 1e6` (MPRIS microseconds) — `ovos_media/mpris.py:803` |
| CRITICAL | `LoopStatus` setter assigned raw string to `loop_state` | Maps MPRIS strings to `LoopState` enum — `ovos_media/mpris.py:775` |
| CRITICAL | `Stop()` called `pause()` instead of `stop()` | Calls `self._ocp_player.stop()` — `ovos_media/mpris.py:840` |
| CRITICAL | `set_player_state` / `set_media_state` emitted old state (state machine always stuck in STOPPED) | Fixed to assign `self.state = state` before emitting — `ovos_media/player.py:547,559` |
| MAJOR | `_set_main_player` always-False condition — LOG never fired | Saves old name before assignment — `ovos_media/mpris.py:229` |
| MAJOR | `play()` never passed preferred backend service (3 TODO stubs) | `_resolve_preferred_service()` added — `ovos_media/player.py:736,749,754` |
| MAJOR | `ovos-workshop` and `json-database` imported but not in `pyproject.toml` | Added to `[project.dependencies]` |
| MAJOR | Relative imports in `video.py` and `web.py` | Changed to absolute — `ovos_media/media_backends/video.py:1`, `web.py:1` |
| MAJOR | `mycroft.audio.service.*` bus API not handled — legacy skills broken | `LegacyAudioServiceCompat` added — `ovos_media/legacy_api.py` |
| MAJOR | `recognizer_loop:audio_output_start/end` not handled — no TTS ducking | Ducking handlers added — `ovos_media/player.py` |
| MAJOR | `mycroft.stop` not handled — global stop had no effect | Handler added — `ovos_media/player.py` |
| MAJOR | `opm.audio.query` not handled — OPM discovery returned nothing | Handler added — `ovos_media/service.py:92` |
| MINOR | Wrong `description` in `pyproject.toml` (copied from ovos-audio) | Fixed |
| MINOR | `requires-python = ">=3.9"` | Bumped to `>=3.10` |
| MINOR | `build_tests.yml` YAML syntax error + Python 3.8 | Replaced with reusable `gh-automations/build-tests.yml@dev` |
| MINOR | `handle_search_end` was a no-op `pass` | Now logs a warning — `ovos_media/service.py:80` |

---

## Open Issues

### High Priority

| Severity | Location | Issue |
|---|---|---|
| MAJOR | `ovos_media/service.py:80` | `handle_search_end` still does not dismiss the GUI loading spinner — only logs a warning. Needs `_update_gui()` call after search completes. |

### Medium Priority

| Severity | Location | Issue |
|---|---|---|
| MINOR | `ovos_media/mpris.py` | `CanSeek` always returns `False` — seeking via external MPRIS controller not implemented |
| MINOR | `ovos_media/mpris.py` | `Rate` always returns `1.0` — playback speed control not exposed |
| MINOR | `ovos_media/player.py` | No periodic `_update_gui()` call during playback — scrubbar position only updated on state change events, not live |
| MINOR | `ovos_media/service.py:32` | `MediaService` class still has a `# TODO` comment |

### Architecture / Future Work

| Item | Description |
|---|---|
| `ovos-media-plugin-mpris` | Role B (external player management) should be extracted from `OcpMprisExporter` into a standalone `BaseMediaService` plugin repo |
| `handle_search_end` implementation | Needs real logic to merge search results and call `_update_gui()` |
| Position live updates | Evaluate periodic `_update_gui()` during playback for accurate scrubbar |

---

## Security Considerations

- No network-facing ports opened by `ovos-media` itself.
- D-Bus session bus is used for MPRIS; access is limited to the user session.
- `native_sources` config controls which MessageBus sources are trusted for playback commands — `MediaService.__init__` — `ovos_media/service.py:48`.
- `WebService` renders URLs in an embedded web view; `javascript_can_open_windows` and `allow_url_change` default to `false` to reduce exposure.

---

## Test Coverage Status (2026-03-12)

- 291 unit tests in `test/unittests/`
- 13 integration tests in `test/end2end/`
- Total coverage: **53%**
- Main uncovered areas: `mpris.py` (37%) — D-Bus async code; `player.py` (46%) — `__init__`, `search()`, `play_media()`

---

## Documentation Status

| File | Status |
|---|---|
| `docs/index.md` | Updated |
| `docs/getting-started.md` | Added 2026-03-12 |
| `docs/configuration.md` | Added 2026-03-12 |
| `docs/architecture.md` | Added 2026-03-12 |
| `docs/backends.md` | Added 2026-03-12 |
| `docs/mpris.md` | Added 2026-03-12 |
| `docs/ocp-skills.md` | Added 2026-03-12 |
| `QUICK_FACTS.md` | Updated 2026-03-12 |
| `FAQ.md` | Updated 2026-03-12 |
| `MAINTENANCE_REPORT.md` | Updated 2026-03-12 |
| `AUDIT.md` | This file |

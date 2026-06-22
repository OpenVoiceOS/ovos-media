# Audit Report — `ovos-media`

Evidence-based record of known issues, technical debt, and security considerations.
All citations use `ClassName.method — path/to/file.py:LINE` format.

Last updated: 2026-03-12 (rev 2 — post-Queue + async audit)

---

## Current Health

| Property | Value |
|---|---|
| **Version** | `0.0.1` (`ovos_media/version.py:2-5`) |
| **Test count** | 428 passed, 0 failed |
| **Overall coverage** | **72%** |
| **CI status** | All 428 tests pass locally; CI workflows present |
| **Open blockers** | 0 critical; 2 major; 5 minor (see Open Issues below) |

---

## Coverage Breakdown (2026-03-12)

| Module | Stmts | Miss | Cover | Key uncovered lines |
|---|---|---|---|---|
| `ovos_media/__init__.py` | 0 | 0 | 100% | — |
| `ovos_media/__main__.py` | 17 | 1 | 94% | 37 |
| `ovos_media/legacy_api.py` | 107 | 14 | 87% | 162–163, 205–264 (seek/length handlers) |
| `ovos_media/media_backends/__init__.py` | 3 | 0 | 100% | — |
| `ovos_media/media_backends/audio.py` | 9 | 0 | 100% | — |
| `ovos_media/media_backends/base.py` | 247 | 26 | 89% | 98, 131, 153, 163, 174, 187, 195, 201, 218, 223–225, 235, 244, 301, 314–315, 322–323, 333, 343, 352, 363, 377, 390, 403 |
| `ovos_media/media_backends/video.py` | 9 | 0 | 100% | — |
| `ovos_media/media_backends/web.py` | 9 | 0 | 100% | — |
| `ovos_media/mpris.py` | 646 | 204 | **68%** | D-Bus async loop, scan_players, query_player, property setters |
| `ovos_media/player.py` | 751 | 262 | **65%** | `__init__`, `bind`, `search`, `play_media`, `play`, `play_next/prev` |
| `ovos_media/service.py` | 62 | 7 | 89% | lifecycle callbacks (lines 12, 16, 20, 24, 28), `bus.run_in_thread` (53–54) |
| `ovos_media/utils.py` | 9 | 0 | 100% | — |
| `ovos_media/version.py` | 5 | 5 | **0%** | 2–8 — not imported by any test |
| **TOTAL** | **1874** | **519** | **72%** | |

---

## Fixed Issues

### [2026-03-12] GUI Decoupling

| Severity | Issue | Fix |
|---|---|---|
| MAJOR | `OCPGUIInterface` (`ovos_media/gui.py`) coupled media service directly to QML page management | `gui.py` deleted; `OCPMediaPlayer` now calls `GUIInterface.show_media_player()` — `ovos_media/player.py:447` |

### [2026-03-12] Async / Safety Audit Fixes

| Severity | Issue | Fix |
|---|---|---|
| CRITICAL | `time.sleep()` inside `async def event_loop()` — blocked entire asyncio loop, froze D-Bus | Changed to `await asyncio.sleep()` — `ovos_media/mpris.py:634,641,644` |
| CRITICAL | Busy-wait `while loop.is_running(): sleep(0.2)` in `shutdown()` — deadlock risk | Replaced with `loop.call_soon_threadsafe(loop.stop)` + `self.join(timeout=5)` — `ovos_media/mpris.py:682-689` |
| HIGH | `handle_play_request` passed `[None]` to `play_media()` when `message.data["media"]` absent | Early-return guard added — `ovos_media/player.py:1094-1097` |
| HIGH | `remove_listeners()` missing 3 deregistrations: `list_backends`, `duck`, `unduck` | All three added — `ovos_media/media_backends/base.py:437-439` |
| MEDIUM | `get_featured_skills()` used `time.sleep(0.2)` blocking the calling thread | Changed to `threading.Event().wait(timeout=0.2)` — `ovos_media/player.py:132` |

### [2026-03-12] MPRIS Refactor

| Severity | Issue | Fix |
|---|---|---|
| MAJOR | `MprisPlayerCtl` mixed D-Bus server (Role A) and external player management (Role B) with no separation | Renamed to `OcpMprisExporter`; `manage_external_players` defaults to `false`; alias kept for compat — `ovos_media/mpris.py:74` |
| MAJOR | `manage_players` was hardcoded `True`, ignoring config | Now reads `config.get("manage_external_players", False)` — `ovos_media/mpris.py:99` |
| MAJOR | D-Bus connect failure crashed the service | `try/except` added in `OcpMprisExporter.run`; logs warning and returns cleanly — `ovos_media/mpris.py:651` |

### [2026-03-11] Pre-release Blockers

| Severity | Issue | Fix |
|---|---|---|
| CRITICAL | `Position` property returned hardcoded `1` | Returns `now_playing.position * 1e6` (MPRIS microseconds) — `ovos_media/mpris.py:824` |
| CRITICAL | `LoopStatus` setter assigned raw string to `loop_state` | Maps MPRIS strings to `LoopState` enum — `ovos_media/mpris.py:790-796` |
| CRITICAL | `Stop()` called `pause()` instead of `stop()` | Calls `self._ocp_player.stop()` |
| CRITICAL | `set_player_state` / `set_media_state` emitted old state (state machine always stuck in STOPPED) | Fixed to assign `self.state = state` before emitting — `ovos_media/player.py:582,596` |
| MAJOR | `_set_main_player` always-False condition — LOG never fired | Saves old name before assignment — `ovos_media/mpris.py` |
| MAJOR | `play()` never passed preferred backend service | `_resolve_preferred_service()` added — `ovos_media/player.py` |
| MAJOR | `ovos-workshop` and `json-database` imported but not in `pyproject.toml` | Added to `[project.dependencies]` — `pyproject.toml:18-20` |
| MAJOR | Relative imports in `video.py` and `web.py` | Changed to absolute — `ovos_media/media_backends/video.py:1`, `web.py:1` |
| MAJOR | `mycroft.audio.service.*` bus API not handled — legacy skills broken | `LegacyAudioServiceCompat` added — `ovos_media/legacy_api.py` |
| MAJOR | `recognizer_loop:audio_output_start/end` not handled — no TTS ducking | Ducking handlers added — `ovos_media/player.py` |
| MAJOR | `mycroft.stop` not handled — global stop had no effect | Handler added — `ovos_media/player.py` |
| MAJOR | `opm.audio.query` not handled — OPM discovery returned nothing | Handler added — `ovos_media/service.py:92` |
| MINOR | Wrong `description` in `pyproject.toml` (copied from ovos-audio) | Fixed — `pyproject.toml:8` |
| MINOR | `requires-python = ">=3.9"` | Bumped to `>=3.10` — `pyproject.toml:11` |
| MINOR | `build_tests.yml` YAML syntax error + Python 3.8 | Replaced with reusable `gh-automations/build-tests.yml@dev` |
| MINOR | `handle_search_end` was a no-op `pass` | Now calls `_update_gui()` — `ovos_media/service.py:85-87` |

---

## Open Issues

### Major Priority

| Severity | Location | Issue |
|---|---|---|
| MAJOR | `BaseMediaService.get_preferred_players` — `ovos_media/media_backends/base.py:139` | Stub: always returns `[]`. No logic implemented. Backend preference selection is therefore non-functional. |
| MAJOR | `BaseMediaService.handle_media_state_change` — `ovos_media/media_backends/base.py:153` | Bare `pass` for unknown `namespace` values with only a `# ???` comment. Silently ignores state changes for custom namespace backends. |

### Minor Priority

| Severity | Location | Issue |
|---|---|---|
| MINOR | `_MediaPlayer2PlayerInterface.PlaybackStatus` — `ovos_media/mpris.py:773` | `# TODO validate strings` — the getter can return arbitrary strings if `PlayerState` enum gains new members. MPRIS spec requires exactly `"Playing"`, `"Paused"`, or `"Stopped"`. |
| MINOR | `_MediaPlayer2PlayerInterface.LoopStatus` — `ovos_media/mpris.py:782` | `# TODO validate strings` — same issue as PlaybackStatus getter. `"RepeatTrack"` is a non-standard value; MPRIS spec uses `"Track"`, `"Playlist"`, `"None"`. |
| MINOR | `OCPMediaCatalog.__init__` — `ovos_media/player.py:42` | `# TODO - add search results clear/replace events` — no bus handlers exist for clearing or replacing the active search results without starting new playback. |
| MINOR | `OCPMediaPlayer.__init__` — `ovos_media/player.py:357` | Commented-out `Playlist` constructor call with `# TODO icon` note. Playlist icon is never set, leaving the search results playlist without a display icon. |
| MINOR | `MediaService` — `ovos_media/service.py:31` | Bare `# TODO` comment on the class definition with no description. Intent unknown. |
| MINOR | `MediaService.shutdown` — `ovos_media/service.py:111` | `# TODO - update gui for no-media in now_playing page` — GUI is not reset on shutdown; now-playing page may display stale data after the service stops. |
| MINOR | `OCPMediaPlayer.play_shuffle` — `ovos_media/player.py:829` | `# TODO: does the 'last track' matter in this case?` — shuffle on last track falls through to `media.search_playlist.next_track()` without guard; `IndexError` possible if search playlist is also exhausted. |
| MINOR | `OCPMediaPlayer.play_prev` — `ovos_media/player.py:897` | `# TODO: Should skipping back get a random track instead of previous?` — behaviour when shuffle is active and user presses prev is undefined. Currently plays a random track rather than reversing shuffle history. |
| MINOR | `OCPMediaCatalog.liked_songs_playlist` — `ovos_media/player.py:95` | `# HACK to allow sort_by_conf to work` — `match_confidence` is injected as a synthetic field on liked-songs entries to make Playlist sorting work. This couples liked songs to the search-results sort key. |

### Deprecation Warnings (test output)

| Severity | Location | Issue |
|---|---|---|
| MINOR | `test/unittests/test_media_backends.py:191` | Test file imports `Message` from `ovos_utils.messagebus` which emits `DeprecationWarning: ovos_utils.messagebus has been deprecated since version 0.1.0`. Should import from `ovos_bus_client.message`. |

---

## Security Considerations

- **No network-facing ports**: `ovos-media` itself opens no TCP/UDP sockets. All communication is via the OVOS MessageBus WebSocket (localhost only by default).
- **D-Bus session bus exposure**: `OcpMprisExporter` registers `org.mpris.MediaPlayer2.OCP` on the session bus — `ovos_media/mpris.py:57-105`. Access is scoped to the user's D-Bus session. A `dbus_type: "system"` config option exists (`ovos_media/mpris.py:108-110`) that would expose the interface system-wide; this is dangerous on multi-user systems and should be documented as unsupported.
- **Bus source validation**: `native_sources` config limits which MessageBus sources are trusted for playback commands — `MediaService.__init__` — `ovos_media/service.py:48`. The `validate_source=True` default is correct; disabling it (e.g. for testing) bypasses all source checks.
- **Web player policy**: `WebService` renders URLs in an embedded web view. The `javascript_can_open_windows` and `allow_url_change` defaults should be verified against `ovos-gui-api-client`'s `show_media_player` implementation; ovos-media itself does not set these flags directly.
- **External MPRIS player control**: When `manage_external_players: true`, `OcpMprisExporter` scans all session-bus names matching `org.mpris.MediaPlayer2.*` and can pause/stop/skip them — `ovos_media/mpris.py:422-451`. A malicious or misbehaving player could register a crafted name and receive unexpected control signals. The `ignored_players` list mitigates this for known cases.
- **Hardcoded icon paths in MPRIS**: `OcpMprisExporter._update_ocp` constructs icon paths using `os.path.dirname(__file__)` — `ovos_media/mpris.py:165-178`. This is safe (no user-controlled input), but the paths assume the `qt5/images/` directory is present; missing files will cause a silent empty icon, not an exception.

---

## Technical Debt — Blockers for 0.1.0

| Item | Description | Effort |
|---|---|---|
| Implement `BaseMediaService.get_preferred_players` | `base.py:139` — stub always returns `[]`; backend selection is non-functional | Medium |
| Fix `version.py` coverage | `version.py` at 0% — import it in at least one test | Trivial |
| Raise overall coverage to ≥ 75% | Current: 72%. `player.py` (65%) and `mpris.py` (68%) are main gaps | Medium |
| Fix deprecated import in test file | `test_media_backends.py:191` — `ovos_utils.messagebus.Message` → `ovos_bus_client.message.Message` | Trivial |
| Add `publish-alpha.yml` workflow | No automatic alpha release on `dev` pushes | Low |
| Implement `BaseMediaService.handle_media_state_change` unknown-namespace branch | `base.py:153` — bare `pass` silently drops state for custom backends | Low |

---

## Architecture / Future Work

| Item | Description |
|---|---|
| `ovos-media-plugin-mpris` | Role B (external player management) should be extracted from `OcpMprisExporter` into a standalone plugin repo. Currently ~200 lines of D-Bus scanning run even when disabled via config. |
| Position live updates | `OCPMediaPlayer._update_gui()` — `ovos_media/player.py` — is only called on discrete state changes. A periodic update (1 Hz while `PLAYING`) is required for accurate scrubbar rendering. |
| `handle_search_end` result merging | Search results are not merged/ranked after `ovos.common_play.search.end`; the GUI only reflects whatever state was already set before the search completed. |
| Shuffle history tracking | `play_prev` in shuffle mode plays a random track instead of reversing the shuffle history — `ovos_media/player.py:896-898`. A deque of recently played tracks would fix this. |

---

## Documentation Status

| File | Status |
|---|---|
| `docs/index.md` | Present — updated 2026-03-12 |
| `docs/getting-started.md` | Present — added 2026-03-12 |
| `docs/configuration.md` | Present — added 2026-03-12 |
| `docs/architecture.md` | Present — added 2026-03-12 |
| `docs/backends.md` | Present — added 2026-03-12 |
| `docs/mpris.md` | Present — added 2026-03-12 |
| `docs/ocp-skills.md` | Present — added 2026-03-12 |
| `QUICK_FACTS.md` | Present — updated 2026-03-12 |
| `FAQ.md` | Present — updated 2026-03-12 |
| `MAINTENANCE_REPORT.md` | Present — updated 2026-03-12 |
| `AUDIT.md` | This file — updated 2026-03-12 |
| `SUGGESTIONS.md` | Present — updated 2026-03-12 |

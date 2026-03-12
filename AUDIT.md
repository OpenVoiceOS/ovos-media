
# ovos-media — Audit Report

## Documentation Status
- [x] QUICK_FACTS.md
- [x] FAQ.md
- [x] MAINTENANCE_REPORT.md
- [x] AUDIT.md
- [x] docs/index.md

## Fixed Issues (2026-03-11) — Batch 2: Feature Parity & Migration

### Critical State Machine Bugs
- `[CRITICAL]` **player**: `set_player_state` never assigned `self.state = state` and emitted the OLD state — `ovos_media/player.py:559`
- `[CRITICAL]` **player**: `set_media_state` never assigned `self.media_state = state` and emitted the OLD state — `ovos_media/player.py:547`
  (Both methods relied on the bus roundtrip handler to update state, but the
  emitted value matched the current state so the handler bailed early. Net
  effect: the player was perpetually stuck in STOPPED/NO_MEDIA.)

### Feature Parity with ovos-audio OCP Adapter
- `[MAJOR]` **ducking**: `recognizer_loop:audio_output_start/end` not handled — player was never ducked by TTS — `ovos_media/player.py`
- `[MAJOR]` **ducking**: `recognizer_loop:record_begin/end` not handled — player not corked during listening — `ovos_media/player.py`
- `[MAJOR]` **ducking**: `ovos.utterance.handled` not handled — volume not restored after handled utterance — `ovos_media/player.py`
- `[MAJOR]` **stop**: `mycroft.stop` not handled — global stop did not stop media playback — `ovos_media/player.py`
- `[MAJOR]` **legacy API**: `mycroft.audio.service.*` bus namespace not handled — skills using legacy API had no effect — `ovos_media/legacy_api.py` (new)
- `[MAJOR]` **opm**: `opm.audio.query` not handled — OPM plugin discovery returned nothing — `ovos_media/service.py`

## Fixed Issues (2026-03-11) — Batch 1: Pre-release blockers
- `[CRITICAL]` **mpris**: `Position` property returned hardcoded `1` — fixed to return `position * 1e6` — `ovos_media/mpris.py:803`
- `[CRITICAL]` **mpris**: `LoopStatus` setter assigned raw string to `loop_state` instead of `LoopState` enum — `ovos_media/mpris.py:775`
- `[CRITICAL]` **mpris**: `Stop()` called `pause()` instead of `stop()` — `ovos_media/mpris.py:840`
- `[MAJOR]` **mpris**: `_set_main_player` always-False condition — LOG never fired on player change — `ovos_media/mpris.py:229`
- `[MAJOR]` **mpris**: `manage_players` hardcoded `True`, ignoring config — `ovos_media/mpris.py:93`
- `[MAJOR]` **player**: `play()` never passed preferred backend service — 3 TODO stubs — `ovos_media/player.py:736,749,754`
- `[MAJOR]` **deps**: `ovos-workshop` and `json-database` imported but not in `pyproject.toml`
- `[MAJOR]` **imports**: Relative imports in `video.py` and `web.py` — forbidden by CLAUDE.md
- `[MINOR]` **pyproject**: Wrong `description`; `requires-python = ">=3.9"` bumped to `>=3.10`
- `[MINOR]` **ci**: `build_tests.yml` YAML error + Python 3.8; `notify_matrix.yml` `@master` ref
- `[MINOR]` **service**: `handle_search_end` was a no-op `pass`
- `[MINOR]` **mpris**: Poll interval and `ignored_players` hardcoded
- `[MINOR]` **gui**: `javascriptCanOpenWindows` / `allowUrlChange` hardcoded

## Open Issues

### Service
- `[MAJOR]` `handle_search_end` still does not dismiss the GUI spinner — only logs a warning — `ovos_media/service.py:80`
- `[INFO]` `MediaService` class still has a `# TODO` comment — `ovos_media/service.py:32`

### MPRIS
- `[MINOR]` `CanSeek` always returns `False` — seek via MPRIS not implemented — `ovos_media/mpris.py:815`
- `[MINOR]` `Rate` property always returns `1` — playback speed not exposed — `ovos_media/mpris.py:799`

### CI
- `[MINOR]` `pypa/gh-action-pypi-publish` pinned to `@master` in publish workflows

## Migration Readiness

All blocking gaps between ovos-media and the ovos-audio OCP adapter have been
addressed.  The following table summarises parity status:

| Feature | ovos-audio AudioService | ovos-media | Status |
|---------|------------------------|-----------|--------|
| `mycroft.audio.service.*` bus API (14 events) | YES | YES (via `LegacyAudioServiceCompat`) | ✅ |
| `recognizer_loop:*` ducking | YES | YES | ✅ |
| `mycroft.stop` handler | YES | YES | ✅ |
| `opm.audio.query` response | YES (deprecated stub) | YES | ✅ |
| State machine correctness | YES | YES (fixed) | ✅ |
| Shuffle / repeat (3-state loop) | NO | YES | ✅ |
| Liked songs / media curation | NO | YES | ✅ |
| MPRIS external player control | NO | YES | ✅ |
| GUI integration | NO | YES | ✅ |
| Video / web playback types | NO | YES | ✅ |

## Fixed Issues (2026-03-12) — GUI Decoupling

- `[MAJOR]` **gui**: `OCPGUIInterface` coupled media service directly to QML page management — now resolved. `ovos_media/gui.py` deleted; `OCPMediaPlayer` uses `GUIInterface.show_media_player()` — `ovos_media/player.py`

## Test Coverage
- `test/unittests/test_legacy_api.py` — 22 tests — `LegacyAudioServiceCompat`
- `test/unittests/test_player_state.py` — 13 tests — state machine, ducking, mycroft.stop
- `test/unittests/test_mpris.py` — 7 tests — MPRIS interface correctness
- `test/unittests/test_player.py` — 8 tests — preferred service resolution
- `test/unittests/test_service.py` — 4 tests — MediaService lifecycle
- `test/unittests/test_gui.py` — 22 tests — `show_media_player` state contract
- `test/unittests/test_media_backends.py` — 13 tests — backend loading and selection
Total: **89 tests passing**

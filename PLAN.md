# ovos-media — Strategic Plan

_Last updated: 2026-03-11_

---

## 1. Current State

`ovos-media` is at `0.0.1a22` (pre-release). Critical bugs have been fixed (see `MAINTENANCE_REPORT.md`).
The codebase is functional but three major architectural concerns must be resolved before `0.0.1`:

1. **GUI coupling** — `ovos_media/gui.py` contains a custom `OCPGUIInterface` that directly manages display state, JavaScript policies, and QML pages. The new GUI system (`GUI_DESIGN.md`) renders this approach obsolete and incompatible.
2. **MPRIS monolith** — `MprisPlayerCtl` mixes two concerns: exposing OCP over D-Bus (Role A) and managing external MPRIS players (Role B).
3. **Incomplete test coverage** — key paths still untested.

---

## 2. GUI Decoupling (Highest Priority)

### Problem

`ovos_media/gui.py` today:
- Defines `OCPGUIInterface` with hardcoded display states (`HOME`, `PLAYER`, `SPINNER`, `PLAYBACK_ERROR`)
- Calls `self.gui.show_page("OVOSMediaPlayer.qml")` directly — tight QML coupling
- Hardcodes JavaScript policy flags (`javascript_policy`, `javascript_can_open_windows`, `allow_url_change`)
- Bundles display logic that belongs to GUI adapter plugins, not to the media service

The new GUI system (`ovos-gui-api-client`, `GUI_DESIGN.md`) explicitly states:
> `show_audio_player()` is called by the OCP audio service, not by individual media skills.
> Skills must not call `show_page()` directly.

### Solution

Delete `ovos_media/gui.py` and replace with a single call to the new `GUIInterface.show_media_player()` template (defined in `GUI_DESIGN.md §4.3a`).

`ovos-media` makes **one kind of GUI call** only:

```python
self.gui.show_media_player(
    now_playing=np.as_dict(),   # current track metadata
    playlist=self.playlist.as_list(),
    search_results=self._last_search_results,
    state="playing" | "paused" | "stopped" | "loading" | "error",
)
```

This template gives adapters everything they need to render the full OCP player chrome: artwork, scrubbar, playback controls, queue, search results — exactly what the current QML `OVOSMediaPlayer` shows.

**Individual backend plugins handle their own rendering:**
- `VideoService` (or a video backend plugin) may call `gui.show_video_player()` on its own `GUIInterface` namespace for full-screen video overlay
- `WebService` (or a web backend plugin) may call `gui.show_url()` on its own namespace
- `ovos-media` is not involved in those calls

### Dependency change

```toml
# pyproject.toml — add:
"ovos-gui-api-client>=0.1.0,<1.0.0"

# remove or demote if only used for GUI:
# ovos-workshop
```

`ovos-gui-api-client` is the standalone package that provides `GUIInterface` (see `GUI_DESIGN.md §4`).

### What disappears

- `ovos_media/gui.py` — entire file deleted
- `OCPGUIInterface` class — replaced by `GUIInterface` from `ovos-gui-api-client`
- All `show_page()` / `remove_page()` / QML references
- JavaScript policy flags — adapters decide policy; `ovos-media` only pushes URIs as data
- `PlaybackState.HOME/SPINNER/PLAYBACK_ERROR` internal display states — expressed via `state=` parameter

### What stays in `player.py`

`OCPMediaPlayer` calls `_update_gui()` after every state or track change:

```python
# player.py (after refactor)
from ovos_gui_api_client import GUIInterface

class OCPMediaPlayer:
    def __init__(self, ...):
        self.gui = GUIInterface("ovos.common_play", bus=self.bus)

    def _update_gui(self) -> None:
        """Push current OCP state to GUI adapters via show_media_player."""
        np = self.now_playing
        state_map = {
            PlayerState.PLAYING: "playing",
            PlayerState.PAUSED:  "paused",
            PlayerState.STOPPED: "stopped",
        }
        self.gui.show_media_player(
            now_playing=np.as_dict() if np else None,
            playlist=self.playlist.as_list(),
            search_results=self._last_search_results or [],
            state=state_map.get(self.state, "stopped"),
        )
```

---

## 3. MPRIS Architecture

### Problem

`MprisPlayerCtl` (`ovos_media/mpris.py`) combines two independent concerns in one class:

**Role A — OCP as MPRIS player (D-Bus server)**
- Registers `org.mpris.MediaPlayer2.OCP` on the session bus
- Exposes OCP state: Position, PlaybackStatus, Metadata, LoopStatus, Shuffle, Volume
- Receives control signals from external apps (KDE Connect, playerctl, GNOME Shell widget)
- Tightly coupled to `OCPMediaPlayer` — must stay in `ovos-media`

**Role B — External MPRIS player manager (D-Bus client)**
- Polls D-Bus every N seconds for new players (Spotify, VLC, Firefox…)
- Auto-pauses OCP when an external player becomes active
- Proxies OCP's skip/pause/shuffle/repeat to external players
- Routes `PlaybackType.MPRIS` tracks to the correct external player
- **No reason to live in core** — this is a pluggable backend

### Proposed split

#### Keep in `ovos-media`: `OcpMprisExporter`
- Thin D-Bus server class, Role A only
- No polling thread, no external player detection
- Always active when `enable_mpris: true`

#### Extract to `ovos-media-plugin-mpris` (new repo): `MprisMediaPlugin`
- Implements `BaseMediaService` (entry point: `opm.media.audio`)
- Polls D-Bus for external players, registers them as selectable backends
- Implements `manage_external_players` auto-pause logic
- Handles `PlaybackType.MPRIS` track routing
- Optional install — `pip install ovos-media-plugin-mpris`

#### `player.py` change
Route `PlaybackType.MPRIS` through the normal `AudioService` dispatch instead of the special-cased `mpris.*` path.

### Why this is the right separation

| Concern | `OcpMprisExporter` | `MprisMediaPlugin` |
|---|---|---|
| D-Bus server | Yes | No |
| D-Bus polling | No | Yes |
| OCP state sync | Yes | No |
| External player control | No | Yes |
| Config: `enable_mpris` | Yes | N/A |
| Config: `manage_external_players` | No | Yes |
| Required for MPRIS-as-sink | Yes | No |
| Required for playing MPRIS streams | No | Yes |

---

## 4. Roadmap to 0.0.1 Stable

### Phase 1 — Critical bug fixes (done ✅)
- [x] Missing pyproject.toml dependencies
- [x] MPRIS Position returns microseconds
- [x] MPRIS LoopStatus maps to enum
- [x] MPRIS Stop() calls stop()
- [x] _set_main_player logic
- [x] manage_players reads config
- [x] Preferred service selection in player.py
- [x] Relative imports removed
- [x] CI workflows added (all using @dev refs)

### Phase 2 — GUI decoupling (next, blocking 0.0.1)
- [ ] Add `ovos-gui-api-client` dependency to `pyproject.toml`
- [ ] Delete `ovos_media/gui.py`
- [ ] Replace `OCPGUIInterface` usage in `player.py` with `GUIInterface` + typed template calls
- [ ] Remove any `show_page()` / QML references from the codebase
- [ ] Update tests (mock `GUIInterface` instead of `OCPGUIInterface`)
- [ ] Update docs

### Phase 3 — MPRIS refactor
- [ ] Extract `OcpMprisExporter` from `MprisPlayerCtl` (Role A only)
- [ ] Create `ovos-media-plugin-mpris` repo (Role B as a `BaseMediaService` plugin)
- [ ] Update `player.py` to route `PlaybackType.MPRIS` through `AudioService`
- [ ] Remove `MprisPlayerCtl` monolith
- [ ] Update config schema and docs

### Phase 4 — Test coverage
- [ ] Unit tests for `OcpMprisExporter` (mock D-Bus)
- [ ] Unit tests for GUI calls (mock `GUIInterface`)
- [ ] Coverage ≥ 80% on all modules

### Phase 5 — Stable release
- [ ] Bump version to `0.0.1` in `version.py`
- [ ] Tag `v0.0.1` on `master`
- [ ] PyPI stable publish via `publish_stable.yml`
- [ ] Update workspace `DOCUMENTATION_INDEX.md`

---

## 5. Open Questions

1. **GUIInterface skill_id**: OCP should register as `"ovos.common_play"` — confirm this is the correct namespace vs `"ovos-media"`.

2. **D-Bus on headless servers**: `OcpMprisExporter` should degrade gracefully (LOG.warning, not crash) when D-Bus session bus is unavailable.

3. **Position live updates**: `show_audio_player()` sets position once. For scrubbar live update, `player.py` needs to call `_update_gui()` periodically (or on `NowPlaying.position` change). Evaluate whether the GUI adapter pulls position from D-Bus (MPRIS) or OCP pushes it via template updates.

4. **MprisMediaPlugin competing with AudioService**: If both a local VLC plugin and the MPRIS plugin are available for the same URI, `preferred_audio_services` config governs priority.

5. **HiveMind remote GUI**: Satellites that connect to a hub could share the hub's `GUIInterface`. No action for 0.0.1, but the decoupled architecture enables this.

---

## 6. Key Files

| File | Status | Action |
|---|---|---|
| `ovos_media/gui.py` | Architectural mismatch | Delete entirely |
| `ovos_media/player.py` | Needs GUI call updates | Replace OCPGUIInterface with GUIInterface |
| `ovos_media/mpris.py` | Monolith | Split → OcpMprisExporter + new plugin repo |
| `ovos_media/media_backends/base.py` | Stable | No changes needed |
| `pyproject.toml` | Needs dep update | Add ovos-gui-api-client, remove ovos-workshop if unused |
| `.github/workflows/` | Complete | All present with @dev refs |

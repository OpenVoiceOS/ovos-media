# Suggestions — `ovos-media`

Actionable proposals for human developers. Each entry includes the problem,
proposed solution, impact rating, and source citation.

---

## 1. Extract external player management into `ovos-media-plugin-mpris`

**Problem**: `OcpMprisExporter` (`ovos_media/mpris.py`) combines two distinct roles:
- Role A: expose OCP itself as a D-Bus MPRIS player (always needed)
- Role B: detect and respond to external MPRIS players (optional, user-controlled)

Role B adds ~200 lines of D-Bus scanning code, asyncio loop, and player lifecycle
management to the core service. This increases startup cost even when the feature
is disabled.

**Proposed solution**: Create a new plugin repo `ovos-media-plugin-mpris` that
implements `BaseMediaService` and handles only Role B. Install optionally.
Role A (`export_ocp()`, property implementations) stays in `ovos_media/mpris.py`.

**Impact**: Medium effort, high architectural clarity. Reduces core footprint.
Makes external player control opt-in at the package level, not just config level.

---

## 2. Implement `handle_search_end` spinner dismissal

**Problem**: `MediaService.handle_search_end` — `ovos_media/service.py:80` — only
logs a warning. The GUI loading spinner shown by `handle_search_start` is never
dismissed after search completes.

**Proposed solution**: Call `self.ocp._update_gui()` at the end of `handle_search_end`.
If there are results in `ocp._last_search_results`, pass them through. If the
player is already in `PLAYING` state (search ended because playback started),
the `_update_gui()` will show the now-playing view naturally.

**Impact**: Low effort, high UX impact. Current behaviour leaves the loading
spinner on screen until the user triggers another state change.

---

## 3. Add periodic `_update_gui()` for live scrubbar updates

**Problem**: `OCPMediaPlayer._update_gui()` — `ovos_media/player.py` — is only
called on discrete state changes (play, pause, stop, track change). The `position`
field in `now_playing` is therefore stale between events. MPRIS clients and GUI
renderers that display a scrubbar will show incorrect position.

**Proposed solution**: Start a background thread or use a periodic bus event
(e.g. every 1 second while `PlayerState.PLAYING`) to call `_update_gui()`.
Rate-limit to avoid flooding the GUI bus namespace.

**Impact**: Medium effort. Required for an accurate scrubbar experience.

---

## 4. Implement `CanSeek` and `Rate` in the MPRIS interface

**Problem**: `CanSeek` returns `False` and `Rate` returns `1.0` unconditionally
in `OcpMprisExporter` — `ovos_media/mpris.py`. This means MPRIS controllers
cannot seek or change playback speed even if the backend supports it.

**Proposed solution**:
- `CanSeek`: query `audio_service.get_track_length()` > 0; return `True` if seekable.
- Seeking: implement `SetPosition(track_id, position)` MPRIS method calling `audio_service.set_track_position(position / 1e6)`.
- `Rate`: expose if backend reports speed control capability.

**Impact**: Medium effort. `CanSeek` is important for KDE Connect / Plasma integration.

---

## 5. Increase test coverage for `player.py` and `mpris.py`

**Problem**: `player.py` is at 46% coverage, `mpris.py` at 37%. The uncovered
regions include `__init__`, `search()`, `play_media()`, `play_next/prev`, and the
full D-Bus async loop in `mpris.py`.

**Proposed solution**:
- For `player.py`: mock `ovos_workshop.OVOSSkill.__init__` to bypass the full
  skill framework init; then test `play_media`, `search`, `play_next/prev` directly.
- For `mpris.py`: use `unittest.mock.AsyncMock` to mock `dbus_next` D-Bus objects;
  test `event_loop`, `scan_players`, `query_player`, all property getters/setters.

**Target**: ≥70% overall coverage before 0.1.0.

**Impact**: Medium effort. Required for confident refactoring.

---

## 6. Migrate deprecated `ovos_utils.messagebus.Message` import

**Problem**: `ovos_media/player.py:18` imports `Message` from
`ovos_utils.messagebus`, which emits a `DeprecationWarning` at test time.

**Proposed solution**: Change to `from ovos_bus_client.message import Message`.

**Impact**: Trivial effort, eliminates noisy test warnings.

---

## 7. Add `publish-alpha.yml` workflow

**Problem**: There is no automatic alpha release workflow on pushes to `dev`.
New commits on `dev` require manual version bumps and PyPI uploads.

**Proposed solution**: Add `.github/workflows/publish-alpha.yml` using
`gh-automations/publish-alpha.yml@dev`. This publishes `0.0.1aN` releases on
every `dev` push automatically.

**Impact**: Low effort, improves release cadence.

# ovos-media — Strategic Plan

_Last updated: 2026-03-12_

---

## 1. Current State

`ovos-media` is at `0.0.1a22` (pre-release).

**Completed work:**
- Phase 1 critical bugs (pyproject.toml, MPRIS Property fixes, relative imports) ✅
- GUI decoupling (`OCPGUIInterface` deleted, `GUIInterface.show_media_player()` in place) ✅
- MPRIS refactor (`OcpMprisExporter` Role A, `ovos-media-plugin-mpris` scaffolded for Role B) ✅
- CI workflows complete (all using `@dev` refs) ✅
- Test coverage 72% ✅

**Blocking the 0.0.1 release:**
A detailed architectural review (2026-03-12) identified 8 correctness/design problems that must be fixed before the stable release. See section 2 below.

**What is correct and should stay:**
- Bus-first design — all state changes flow through MessageBus events
- `NowPlaying` as a bus subscriber (reactive, not pushed)
- Three-backend split: `AudioService`, `VideoService`, `WebService`
- `OcpMprisExporter` Role A / `ovos-media-plugin-mpris` Role B separation
- `OCPMediaCatalog(OVOSCommonPlaybackSkill)` — embedded OCP skill (stays as-is)

---

## 2. Architectural Redesign — Problems and Solutions

### Problem 1: OCPMediaPlayer inherits OVOSAbstractApplication (CRITICAL)

**Root cause:** `OCPMediaPlayer(OVOSAbstractApplication)` in `player.py:~120`.

`OVOSAbstractApplication` is an `OVOSSkill` subclass. It drags in:
- Intent/skill lifecycle machinery (`skill_id`, `intent_service`, `skill_manager_connected`)
- `__del__` that assumes `self.skill_id` exists → `AttributeError` in every test teardown
- `add_event`/`remove_event` abstractions designed for skill message handlers (not a service)
- Config loading meant for skills, not a system daemon

`OCPMediaPlayer` is a **media service daemon**, not a skill. The correct base is no base — or a minimal bus-connected class.

**Fix:**
```python
class OCPMediaPlayer:
    def __init__(self, bus: MessageBusClient, config: dict | None = None) -> None:
        self.bus = bus
        self.config = config or {}
        # ... register handlers manually
        bus.on("ovos.common_play.play", self.handle_play_request)
        # etc.
```

`OCPMediaCatalog(OVOSCommonPlaybackSkill)` stays — it IS a skill and should inherit from it.

**Impact:** Resolves all `AttributeError: 'OCPMediaPlayer' object has no attribute 'skill_id'` test failures.

---

### Problem 2: handle_pause_toggle_request logic inverted (CRITICAL)

**Location:** `player.py` — `handle_pause_toggle_request`.

**Current bug:**
```python
if self.state == PlayerState.PAUSED:
    self.handle_pause_request(msg)   # BUG: pauses again when already paused
else:
    self.handle_resume_request(msg)
```

**Fix:**
```python
if self.state == PlayerState.PAUSED:
    self.handle_resume_request(msg)
else:
    self.handle_pause_request(msg)
```

---

### Problem 3: set_player_state dual-write pattern (HIGH)

**Location:** `player.py` — `set_player_state` + `handle_player_state_update`.

**Current pattern:**
```python
def set_player_state(self, state):
    self.state = state
    self.bus.emit(Message("ovos.common_play.player.state", {"state": state}))  # emits

def handle_player_state_update(self, msg):   # also subscribed to same event
    self.state = msg.data["state"]           # writes self.state twice
```

`set_player_state` writes `self.state` → emits event → `handle_player_state_update` picks up own event → writes `self.state` again. Fragile self-subscribe anti-pattern; if subscription lags, state desync occurs.

**Fix:** Make `OCPMediaPlayer` the single writer of `self.state`. Emit the event but do not subscribe to it internally. External consumers (MPRIS, GUI clients) subscribe; `OCPMediaPlayer` does not.

```python
def set_player_state(self, state: PlayerState) -> None:
    self.state = state          # single write
    self._update_gui()
    self.bus.emit(Message("ovos.common_play.player.state", {"state": state.value}))
    # remove: handle_player_state_update subscription
```

---

### Problem 4: Dual playlist with O(n²) dedup (HIGH)

**Location:** `player.py` — `play_next()` crosses between `self.playlist` and `self.media.search_playlist`.

**Current problem:**
- `self.playlist` — user's explicit queue
- `self.media.search_playlist` — auto-populated from search results
- `play_next()` has to check both, deduplicate, and maintain position — O(n²) scan
- No clear priority rules when both are non-empty

**Fix:** Introduce a single `Queue` abstraction that merges on enqueue, not on consume:
```python
class Queue:
    """Ordered, deduplicated media queue. User entries take priority over search results."""
    def next(self) -> MediaEntry | None: ...
    def enqueue(self, entries: list[MediaEntry], source: str = "search") -> None: ...
```
`self.playlist` and `self.media.search_playlist` feeds into `Queue` at search/play time. `play_next()` simply calls `queue.next()`.

---

### Problem 5: sleep() in bus handlers (HIGH)

**Locations:**
- `media_backends/base.py` `handle_play`: `time.sleep(0.5)` — blocks the MessageBus event loop for 500ms
- `player.py` `on_invalid_stream`: `time.sleep(3)` — blocks for 3 seconds on every failed stream

**Fix:** Replace with `threading.Timer` or `self.bus.emit_later()`:
```python
# instead of time.sleep(0.5); self.play(...)
threading.Timer(0.5, self.play, args=[uri, service]).start()

# instead of time.sleep(3); self.play_next()
threading.Timer(3.0, self.play_next).start()
```

---

### Problem 6: Dead handle_track_state_change (MEDIUM)

**Location:** `player.py` — `NowPlaying.handle_track_state_change`, all branches are `pass`.

This handler subscribes to `ovos.common_play.track.state` but does nothing with it. State changes from backends are silently dropped.

**Fix:** Implement the handler:
```python
def handle_track_state_change(self, msg: Message) -> None:
    state = TrackState(msg.data["state"])
    self.state = state
    if state in (TrackState.PLAYING_AUDIO, TrackState.PLAYING_VIDEO, TrackState.PLAYING_WEBVIEW):
        self._player.set_player_state(PlayerState.PLAYING)
    elif state == TrackState.PAUSED_AUDIO:
        self._player.set_player_state(PlayerState.PAUSED)
    elif state in (TrackState.END_OF_MEDIA, TrackState.ERROR):
        self._player.play_next()
```

---

### Problem 7: No position→GUI forwarding (MEDIUM)

**Location:** `player.py` — `handle_sync_seekbar` updates `self.now_playing.position` but never calls `_update_gui()`.

Result: scrubbar in GUI adapter never moves during playback.

**Fix:**
```python
def handle_sync_seekbar(self, msg: Message) -> None:
    pos = msg.data.get("position", 0)
    self.now_playing.position = pos
    self._update_gui()   # add this line
```

---

### Problem 8: Silent backend load failure (MEDIUM)

**Location:** `media_backends/base.py` — when zero backends load, service silently continues.

**Fix:**
```python
if not self._loaded_backends:
    LOG.error("No media backends loaded — all playback will fail. "
              "Install at least one: ovos-vlc-plugin, ovos-mplayer-plugin, etc.")
    self.bus.emit(Message("ovos.common_play.media.state",
                          {"state": MediaState.NO_MEDIA}))
```

---

### Problem 9: MPRIS super() wrong class name (LOW)

**Location:** `mpris.py:74`

```python
super(MprisPlayerCtl, self).__init__()   # MprisPlayerCtl was renamed to OcpMprisExporter
```

**Fix:** `super().__init__()` (no explicit class name needed in Python 3)

---

### Problem 10: MPRIS asyncio.get_event_loop() deprecated (LOW)

**Location:** `mpris.py:77`

```python
self.loop = asyncio.get_event_loop()   # deprecated, returns running loop or creates new
```

**Fix:** `self.loop = asyncio.new_event_loop()`

---

### Problem 11: MPRIS LoopStatus getter returns wrong string (LOW)

**Location:** `mpris.py` — `LoopStatus` getter returns `"RepeatTrack"` but MPRIS spec requires `"Track"`.

**Fix:** Return `"Track"` for `LoopState.REPEAT_TRACK`.

---

## 3. Roadmap to 0.0.1 Stable

### Phase A — OCPMediaPlayer base class redesign (CRITICAL, 1 file)
Remove `OVOSAbstractApplication` inheritance from `OCPMediaPlayer`. Plain class with `bus` parameter.
- [ ] Rewrite `OCPMediaPlayer.__init__` to take `bus: MessageBusClient`
- [ ] Register all event handlers via `bus.on(...)` directly
- [ ] Update `MediaService` to instantiate `OCPMediaPlayer(bus=self.bus)`
- [ ] Update all tests — remove patches on `OVOSAbstractApplication.__init__`
- [ ] Verify: `uv run pytest test/ -v` — no `AttributeError: skill_id` in teardown

### Phase B — Logic bug fixes (HIGH, 2 fixes)
- [ ] Fix `handle_pause_toggle_request` (inverted condition)
- [ ] Fix `set_player_state` dual-write (remove self-subscription)

### Phase C — Correctness fixes (MEDIUM, 4 fixes)
- [ ] Fix `handle_track_state_change` dead code
- [ ] Fix `handle_sync_seekbar` missing `_update_gui()` call
- [ ] Fix `sleep()` in `handle_play` and `on_invalid_stream` → `threading.Timer`
- [ ] Fix backend load failure → emit error state

### Phase D — MPRIS cleanup (LOW, 3 one-liners)
- [ ] Fix `super()` call in `mpris.py:74`
- [ ] Fix `asyncio.get_event_loop()` in `mpris.py:77`
- [ ] Fix `LoopStatus` getter → `"Track"` not `"RepeatTrack"`

### Phase E — Queue abstraction (MEDIUM, design work)
- [ ] Design `Queue` class with `next()`, `enqueue()`, `clear()`
- [ ] Replace dual-playlist O(n²) dedup in `play_next()`
- [ ] Update tests

### Phase F — Version bump and release
- [ ] Change `version.py` from `0.0.1a22` to `0.0.1`
- [ ] Update `QUICK_FACTS.md`
- [ ] Tag `v0.0.1` on `master`

---

## 4. Open Questions

1. **GUIInterface skill_id**: OCP registers as `"ovos.common_play"` — confirmed.

2. **D-Bus on headless servers**: `OcpMprisExporter` should degrade gracefully when D-Bus is unavailable.

3. **Position live updates**: `show_media_player()` sets position once per call. Live scrubbar needs periodic `_update_gui()` calls during playback (every 1s). Config key: `media.gui_update_interval`.

4. **Queue priority**: When both user-queued items and auto-search results exist, user queue takes strict priority. Search results fill in after user queue is exhausted.

5. **HiveMind remote GUI**: Satellites could share hub's `GUIInterface`. Not in scope for 0.0.1.

---

## 5. Key Files

| File | Status | Action |
|---|---|---|
| `ovos_media/player.py` | Wrong base class + 6 bugs | Phase A–E |
| `ovos_media/mpris.py` | 3 minor fixes | Phase D |
| `ovos_media/media_backends/base.py` | Silent failure | Phase C |
| `ovos_media/service.py` | Needs `OCPMediaPlayer` API update | Phase A |
| `test/unittests/` | Tests assume skill base class | Phase A |

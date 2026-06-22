# GUI Decoupling Plan for ovos-media

## Current State

`ovos-media/ovos_media/gui.py` (`OCPGUIInterface`) drives the GUI by:
- Calling raw QML page names: `show_page("OVOSSyncPlayer")`, `show_page("StreamError")`, `show_pages(["Home", "OVOSSyncPlayer", "PlaylistView"])`
- Registering its own `qt5/` directory so `mycroft-gui` can find those pages: `ui_directories={"qt5": f"{dirname(__file__)}/qt5"}`
- Setting GUI namespace vars (`self["title"]`, `self["image"]`, `self["searchModel"]`, etc.) directly

QML assets live in two places:
- `ovos-media/ovos_media/qt5/` — 12 QML files + assets (this repo)
- `ovos-ocp-audio-plugin/.../res/gui/qt5/` — same set + mediacenter variants (legacy, going away)

Both sets are duplicated and diverged.

## What Already Exists in the Template System

The GUI refactor already defined the OCP templates in both layers:

**`ovos-gui-api-client` — `GUIInterface` methods:**
| Method | Template constant | Data |
|---|---|---|
| `show_ocp_now_playing(...)` | `SYSTEM_ocp_now_playing` | title, artist, image, bg_image, uri, media_type, position, duration, playing, can_prev, can_next, loop_status, shuffle, javascript |
| `show_ocp_search(...)` | `SYSTEM_ocp_search` | results, search_term, skill_cards |
| `show_ocp_playlist(...)` | `SYSTEM_ocp_playlist` | tracks, current_index |

**`ovos-plugin-manager/templates/gui.py` — `AbstractGUIPlugin` hooks:**
| Hook | Fired by |
|---|---|
| `handle_show_ocp_now_playing(skill_id, data, site_id)` | `SYSTEM_ocp_now_playing` |
| `handle_show_ocp_search(skill_id, data, site_id)` | `SYSTEM_ocp_search` |
| `handle_show_ocp_playlist(skill_id, data, site_id)` | `SYSTEM_ocp_playlist` |

These exist. They just have no callers (in `ovos-media`) and no implementations (in any GUI plugin).

## The Plan

### Step 1: Extend the data contracts (small additions needed)

The current `show_ocp_search()` is missing fields needed for the OCP home screen:
- `liked_cards` — list of liked-song card dicts (currently in `OCPGUIInterface.update_ocp_cards()`)
- `is_home` — bool flag so the GUI knows whether to show the browse/home layout vs. search results

The `show_ocp_now_playing()` is missing:
- `is_liked` — whether the current track is in liked songs (drives the ♥ button state)
- `is_music` — whether to show music-specific controls (like/shuffle) vs. generic controls

Add these optional fields to:
1. `ovos-gui-api-client`: `show_ocp_search()` signature + data assignment
2. `ovos-gui-api-client`: `show_ocp_now_playing()` signature + data assignment
3. `ovos-plugin-manager`: `handle_show_ocp_search()` and `handle_show_ocp_now_playing()` docstrings

### Step 2: Replace OCPGUIInterface in ovos-media

Replace `ovos_media/gui.py` (`OCPGUIInterface`) to:
1. Inherit from `ovos_gui_api_client.GUIInterface` instead of `ovos_bus_client.apis.gui.GUIInterface`
2. Replace every `show_page(...)` / `show_pages(...)` call with the appropriate template method

**`OCPGUIState` → template method mapping:**

| `OCPGUIState` | Old call | New call |
|---|---|---|
| `HOME` | `show_pages(["Home", ...])` | `gui.show_ocp_search(results=[], skill_cards=..., liked_cards=..., is_home=True)` |
| `PLAYER` | `show_pages(["Home", "OVOSSyncPlayer", ...])` | `gui.show_ocp_now_playing(title=..., artist=..., ...)` |
| `PLAYLIST` | `show_pages([..., "PlaylistView"])` | `gui.show_ocp_playlist(tracks=..., current_index=...)` |
| `DISAMBIGUATION` | `show_pages([..., "Disambiguation"])` | `gui.show_ocp_search(results=..., search_term=...)` |
| `SPINNER` | `show_page("SearchingMedia")` | `gui.show_loading(message="Searching...")` |
| `PLAYBACK_ERROR` | `show_page("StreamError")` | `gui.show_error(message=...)` |

**Data population — current → new mapping:**

For `show_ocp_now_playing()`:

| Current `self[key]` | New parameter |
|---|---|
| `self["title"]` | `title` |
| `self["artist"]` | `artist` |
| `self["image"]` | `image` |
| `self["bg_image"]` | `bg_image` |
| `self["uri"]` | `uri` |
| `self["duration"]` | `duration` |
| `self["position"]` | `position` |
| `self["canPrev"]` | `can_prev` |
| `self["canNext"]` | `can_next` |
| `self["loopStatus"]` | `loop_status` |
| `self["shuffleStatus"]` | `shuffle` |
| `self["javascript"]` | `javascript` |
| `self["isLike"]` | `is_liked` (new field, see Step 1) |
| `self["isMusic"]` | `is_music` (new field, see Step 1) |
| derived from `now_playing.playback` | `media_type` ("audio"/"video"/"web") |

For `show_ocp_search()`:

| Current `self[key]` | New parameter |
|---|---|
| `self["searchModel"]["data"]` | `results` |
| `self["skillCards"]` | `skill_cards` |
| `self["likedCards"]` | `liked_cards` (new field) |
| *(implied by state)* | `is_home` (new field) |

For `show_ocp_playlist()`:

| Current `self[key]` | New parameter |
|---|---|
| `self["playlistModel"]["data"]` | `tracks` |
| *(current track index)* | `current_index` |

### Step 3: Move bus event handlers out of OCPGUIInterface

`OCPGUIInterface.bind()` currently registers these bus event handlers:
- `ovos.common_play.playlist.play` → `handle_play_from_playlist`
- `ovos.common_play.liked_tracks.play` → `handle_play_from_liked_tracks`
- `ovos.common_play.search.play` → `handle_play_from_search`
- `ovos.common_play.skill.play` → `handle_play_skill_featured_media`
- `ovos.common_play.home` → `handle_home`

These are **player logic**, not GUI logic. They call `self.player.play_media()`. Move them to `OCPMediaCatalog` in `player.py` where the other bus handlers live. The GUI interface should only push display state; it should not route playback.

After the move, `OCPGUIInterface` becomes a thin wrapper that translates player state into template calls. No bus listeners.

### Step 4: Delete ovos_media/qt5/

Once `OCPGUIInterface` no longer references page names or ui_directories, delete:
```
ovos_media/qt5/
├── AudioPlayerControl.qml
├── Disambiguation.qml
├── GenericCloseControl.qml
├── Home.qml
├── NowPlayingHomeBar.qml
├── OCPLikesView.qml
├── OCPSkillsView.qml
├── OVOSSyncPlayer.qml
├── Playlist.qml
├── PlaylistView.qml
├── Search.qml
├── SearchingMedia.qml
├── StreamError.qml
├── animations/
├── code/
├── delegates/
└── images/
```

Also remove:
- `ui_directories={"qt5": ...}` from `OCPGUIInterface.__init__`
- The `self["audio_player_page"]`, `self["video_player_page"]` etc. page-name keys (no longer needed)

### Step 5: Implement OCP templates in ovos-legacy-mycroft-gui-plugin

The legacy Qt5 plugin (`ovos-legacy-mycroft-gui-plugin`) must implement the three OCP handlers. This is where the QML assets live going forward.

**QML consolidation:** The QML from both `ovos-media/ovos_media/qt5/` and `ovos-ocp-audio-plugin/.../res/gui/qt5/` gets merged here. The ocp-audio-plugin set has more complete variants (mediacenter, video, web player), so use that as the base.

**Handler → QML page mapping:**

| Handler | QML page(s) |
|---|---|
| `handle_show_ocp_now_playing(data)` | `OVOSSyncPlayer.qml` (audio) / `OVOSVideoPlayer.qml` (video) / `OVOSWebPlayer.qml` (web) — chosen based on `data["media_type"]` |
| `handle_show_ocp_search(data)` | `Home.qml` (if `is_home`) / `Disambiguation.qml` (search results) — chosen based on `data["is_home"]` |
| `handle_show_ocp_playlist(data)` | `PlaylistView.qml` + `Playlist.qml` |

**Loading and error states** use existing handlers:
- `handle_show_loading()` → reuse existing loading QML (or `SearchingMedia.qml` moved here)
- `handle_show_error()` → `StreamError.qml` moved here

**Mediacenter variants** (`+mediacenter/`) stay as QML's own file selector mechanism and remain inside the legacy plugin.

**Bus events from QML:**
The QML pages currently emit:
- `ovos.common_play.playlist.play` — user taps a track in playlist
- `ovos.common_play.liked_tracks.play` — user taps a liked song
- `ovos.common_play.search.play` — user taps a search result
- `ovos.common_play.skill.play` — user taps a skill card for featured media
- `ovos.common_play.home` — user navigates home

These are bus messages from the GUI layer to the player. They travel via the bus regardless of which GUI plugin is running — so any GUI adapter (Qt, web, pyhtmx) that has user interaction can emit the same bus events. No changes needed to their names; `OCPMediaCatalog` in `player.py` listens for them (after Step 3).

## What Changes Per Repo

### ovos-media

- `ovos_media/gui.py` — `OCPGUIInterface` uses `ovos_gui_api_client.GUIInterface`; all `show_page`/`show_pages` replaced with `show_ocp_now_playing` / `show_ocp_search` / `show_ocp_playlist` / `show_loading` / `show_error`
- `ovos_media/gui.py` — `bind()` no longer registers bus handlers (moved to `player.py`)
- `ovos_media/player.py` — 5 bus handlers moved in from `OCPGUIInterface.bind()`
- `ovos_media/qt5/` — **deleted entirely**
- `pyproject.toml` — remove `ovos-bus-client` GUI import if it becomes unused; add `ovos-gui-api-client` dependency

### ovos-gui-api-client

- `GUIInterface.show_ocp_now_playing()` — add `is_liked: bool = False`, `is_music: bool = False` params
- `GUIInterface.show_ocp_search()` — add `liked_cards: Optional[List[Dict]] = None`, `is_home: bool = False` params

### ovos-plugin-manager

- `AbstractGUIPlugin.handle_show_ocp_now_playing()` — update docstring with new fields
- `AbstractGUIPlugin.handle_show_ocp_search()` — update docstring with new fields

### ovos-legacy-mycroft-gui-plugin

- Implement `handle_show_ocp_now_playing()` — push QML page name + data to mycroft-gui based on `data["media_type"]`
- Implement `handle_show_ocp_search()` — push `Home.qml` or `Disambiguation.qml` based on `data["is_home"]`
- Implement `handle_show_ocp_playlist()` — push `PlaylistView.qml`
- Move all QML from `ovos-media/ovos_media/qt5/` into the plugin's QML assets
- Consolidate with `ovos-ocp-audio-plugin`'s QML (taking the richer mediacenter variants)
- Move `SearchingMedia.qml` here; wire to `handle_show_loading()` if no generic loading page exists
- Move `StreamError.qml` here; wire to `handle_show_error()`

## What Does NOT Change

- The bus event names for GUI-originated playback (`ovos.common_play.playlist.play`, etc.) — they still travel over the bus; any GUI adapter can emit them
- `OCPMediaCatalog` player state machine — unchanged; `manage_display(state)` still called at the same points
- `OCPGUIState` enum — can stay as internal state tracking, but now triggers template calls instead of QML page names
- MPRIS integration — unchanged
- `update_buttons()`, `update_current_track()`, `update_playlist()`, `update_search_results()` data-gathering methods — kept but refactored to return dicts passed to template methods rather than setting `self[key]` directly

## Open Questions

1. **Home state: single page or multi-page?** The current `render_home()` calls `render_pages(index=0)` which shows `["Home", ...]` — the home screen is an index within a multi-page list that also has the player and playlist. With templates, each template call shows one "view". Should `show_ocp_search(is_home=True)` fully replace the multi-page layout, or does the Qt5 plugin need to maintain its own page stack internally?

   Recommendation: let the Qt5 plugin handle the page stack internally. The template call is a logical state request ("show me the home view"), not a low-level page list. The plugin decides how many QML pages to show.

2. **Position updates.** The now-playing view needs frequent position updates (seek bar). Currently `self["position"]` is set periodically. With `show_ocp_now_playing()`, calling it every second would be excessive (it fires a full template dispatch). Options:
   - Add a dedicated `update_ocp_position(position)` method that only updates the `position` namespace var without re-dispatching a template event
   - Or let the QML poll position via a separate bus message (existing pattern: `ovos.common_play.get_track_position`)

3. **The "show suggestion view" side-events.** `render_player()` calls `self.send_event("ocp.gui.show.suggestion.view.playlist")` to tell the QML to show a suggestion panel without switching pages. This is a QML-internal navigation event. In the adapter world, this becomes an implementation detail of the Qt5 plugin's `handle_show_ocp_now_playing()` — if `data["tracks"]` is non-empty, show suggestions.

4. **`OVOSSyncPlayer` vs `OVOSVideoPlayer` vs `OVOSWebPlayer`** — the current code selects among them via `self["audio_player_page"]`, `self["video_player_page"]`, `self["web_player_page"]` keys, which backend plugins can override. With the template system, `media_type` in `show_ocp_now_playing()` carries this. The Qt5 plugin does the QML selection. The override-by-backend capability is lost. If needed, add an optional `player_page` hint field to the data contract.

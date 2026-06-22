# Architecture

This document describes the runtime architecture of `ovos-media` — the playback
daemon — and how it sits inside the wider OCP media flow. Behavioural claims cite
a source location as `ClassName.method — path/to/file.py`.

---

## The media flow end to end

`ovos-media` is one stage in a plugin pipeline. Each boundary is swappable:

```
 utterance
   │
   ▼
 ovos-core ─ OCP pipeline (ovos-ocp-pipeline-plugin)
   │   classifies the media type, parses title/artist/genre into mediavocab.Signals,
   │   gates + dispatches MediaProvider plugins in-process, ranks the Release results
   ▼
 ovos-media (this daemon)
   │   receives the winning result, picks a playback backend, manages the
   │   queue / now-playing, exposes state over the bus / MPRIS / GUI
   ▼
 playback backend (opm.media.audio | .video | .web)
   │   plays the URI via vlc / mplayer / spotify / chromecast / browser …
   ▼
 stream extractor (opm.ocp.extractor) resolves youtube//… , rss//… , file://…
```

- The **OCP pipeline plugin** (`ovos-ocp-pipeline-plugin`) is the NLP brain: it
  classifies the utterance, queries [media providers](media-providers.md), ranks
  the results, and emits the winner to this daemon. It never plays anything.
- **`ovos-media`** is the player: queue, now-playing, state, and backend
  dispatch. It is a bus-connected service, **not** a skill.
- **Backends** ([backends.md](backends.md)) are single-track players; **stream
  extractors** resolve deferred `{sei}//{uri}` stream identifiers at playback.

The rest of this document focuses on the daemon itself.

---

## Overview

`ovos-media` is structured as three cooperating layers:

1. **`MediaService`** (`ovos_media/service.py`) — the process entry point. It runs as a `Thread`, owns the `MessageBusClient` connection, instantiates `OCPMediaPlayer`, and registers a small set of top-level bus handlers. `MediaService.__init__ — ovos_media/service.py:33`

2. **`OCPMediaPlayer`** (`ovos_media/player.py`) — the virtual media player. It subclasses `OVOSAbstractApplication`, manages the playback state machine (`PlayerState`, `MediaState`), owns three backend service objects (`AudioService`, `VideoService`, `WebService`), drives the GUI via `GUIInterface`, and optionally drives MPRIS. `OCPMediaPlayer.__init__ — ovos_media/player.py:343`

3. **Backend plugins** (`ovos_media/media_backends/`) — three concrete `BaseMediaService` subclasses (`AudioService`, `VideoService`, `WebService`). Each loads OPM plugins at startup and delegates actual playback to the selected plugin instance.

These layers communicate via the OVOS MessageBus (WebSocket pub/sub). The `MediaService` layer handles service-lifecycle and discovery events; `OCPMediaPlayer` handles all media-control events from external callers; backend services handle low-level playback over namespaced bus events.

---

## Message Bus Events

### MediaService handlers

Registered in `MediaService.__init__` — `ovos_media/service.py:60` and `MediaService.init_messagebus` — `ovos_media/service.py:117`.

| Message type | Handler | Notes |
| :--- | :--- | :--- |
| `ovos.common_play.home` | `handle_home` | Calls `ocp._update_gui()` to refresh the GUI home screen. `MediaService.handle_home — ovos_media/service.py:66` |
| `ovos.common_play.ping` | `handle_ping` | Replies immediately with `ovos.common_play.pong`. `MediaService.handle_ping — ovos_media/service.py:69` |
| `ovos.common_play.search.start` | `handle_search_start` | Calls `ocp.gui.show_media_player(state="loading")` to display a loading animation. `MediaService.handle_search_start — ovos_media/service.py:76` |
| `ovos.common_play.search.end` | `handle_search_end` | Marks the end of a search dispatch. `MediaService.handle_search_end — ovos_media/service.py:85` |
| `opm.audio.query` | `handle_opm_audio_query` | Replies with the dict returned by `audio_service.available_backends()`, preserving the legacy OPM discovery contract. `MediaService.handle_opm_audio_query — ovos_media/service.py:92` |

### OCPMediaPlayer handlers

Registered in `OCPMediaPlayer.register_bus_handlers` — `ovos_media/player.py:393`.

| Message type | Handler | Notes |
| :--- | :--- | :--- |
| `ovos.common_play.play` | `handle_play_request` | Starts playback of a new track or playlist. |
| `ovos.common_play.pause` | `handle_pause_request` | Pauses current playback and sets `PlayerState.PAUSED`. |
| `ovos.common_play.play_pause` | `handle_pause_toggle_request` | Toggles between play and pause. |
| `ovos.common_play.resume` | `handle_resume_request` | Resumes paused playback and sets `PlayerState.PLAYING`. |
| `ovos.common_play.stop` | `handle_stop_request` | Stops all active backends and sets `PlayerState.STOPPED`. |
| `ovos.common_play.next` | `handle_next_request` | Advances to the next track, respecting shuffle and loop state. |
| `ovos.common_play.previous` | `handle_prev_request` | Returns to the previous track. |
| `ovos.common_play.seek` | `handle_seek_request` | Seeks audio to a position in milliseconds. `OCPMediaPlayer.seek — ovos_media/player.py:949` |
| `ovos.common_play.get_track_length` | `handle_track_length_request` | Replies with the current track length. |
| `ovos.common_play.set_track_position` | `handle_set_track_position_request` | Seeks to an absolute track position. |
| `ovos.common_play.get_track_position` | `handle_track_position_request` | Replies with current playback position. |
| `ovos.common_play.track_info` | `handle_track_info_request` | Replies with track metadata as `ovos.common_play.track.info.reply`. |
| `ovos.common_play.list_backends` | `handle_list_backends_request` | Replies with the list of loaded backend plugins. |
| `ovos.common_play.playlist.set` | `handle_playlist_set_request` | Replaces the current playlist. |
| `ovos.common_play.playlist.queue` | `handle_playlist_queue_request` | Appends tracks to the current playlist. |
| `ovos.common_play.playlist.clear` | `handle_playlist_clear_request` | Clears the current playlist. |
| `ovos.common_play.shuffle.toggle` | `handle_shuffle_toggle_request` | Toggles shuffle mode. |
| `ovos.common_play.shuffle.set` | `handle_set_shuffle` | Enables shuffle. |
| `ovos.common_play.shuffle.unset` | `handle_unset_shuffle` | Disables shuffle. |
| `ovos.common_play.repeat.toggle` | `handle_repeat_toggle_request` | Toggles repeat mode. |
| `ovos.common_play.repeat.set` | `handle_set_repeat` | Enables repeat. |
| `ovos.common_play.repeat.unset` | `handle_unset_repeat` | Disables repeat. |
| `ovos.common_play.duck` | `handle_duck_request` | Lowers backend volume. |
| `ovos.common_play.unduck` | `handle_unduck_request` | Restores backend volume. |
| `ovos.common_play.cork` | `handle_cork_request` | Pauses playback while TTS/recording is active. |
| `ovos.common_play.uncork` | `handle_uncork_request` | Resumes playback after TTS/recording finishes. |
| `recognizer_loop:audio_output_start` | `handle_duck_request` | Legacy ducking alias — same semantics as `ovos.common_play.duck`. `OCPMediaPlayer.register_bus_handlers — ovos_media/player.py:418` |
| `recognizer_loop:audio_output_end` | `handle_unduck_request` | Legacy ducking alias. |
| `recognizer_loop:record_begin` | `handle_cork_request` | Legacy cork alias. |
| `mycroft.stop` | `handle_mycroft_stop` | Global stop — stops all backends and resets state. `OCPMediaPlayer.register_bus_handlers — ovos_media/player.py:424` |
| `ovos.common_play.like` | `handle_like` | Adds the current track to the liked-songs store. |
| `ovos.common_play.unlike` | `handle_unlike` | Removes the current track from the liked-songs store. |
| `ovos.common_play.status` | `handle_status` | Replies with full player status (state, media type, position, shuffle, loop). `OCPMediaPlayer.handle_status — ovos_media/player.py:454` |

### Emitted events

| Message type | Emitter | Payload |
| :--- | :--- | :--- |
| `ovos.common_play.player.state` | `OCPMediaPlayer.set_player_state` — `ovos_media/player.py:586` | `{"state": PlayerState}` |
| `ovos.common_play.media.state` | `OCPMediaPlayer.set_media_state` — `ovos_media/player.py:573` | `{"state": MediaState}` |
| `ovos.common_play.pong` | `MediaService.handle_ping` — `ovos_media/service.py:74` | empty data |
| `ovos.common_play.track.info.reply` | `handle_track_info_request` | dict of track metadata |
| `ovos.common_play.track.state` | `BaseMediaService.handle_media_state_change` — `ovos_media/media_backends/base.py:133` | `{"state": TrackState}` |

---

## State Machine

Two orthogonal state enums are tracked by `OCPMediaPlayer`:

### PlayerState

Represents the action the player is currently performing. Defined in `ovos_utils.ocp`.

| Value | Meaning |
| :--- | :--- |
| `PLAYING` | Media is actively playing. |
| `PAUSED` | Playback is paused; resumable. |
| `STOPPED` | No active playback. |

State changes are made exclusively through `OCPMediaPlayer.set_player_state` — `ovos_media/player.py:586`, which updates `self.state`, emits `ovos.common_play.player.state`, notifies MPRIS, and calls `handle_status` to report full status to ovos-core.

### MediaState

Represents the lifecycle stage of the loaded media item. Defined in `ovos_utils.ocp`.

| Value | Meaning |
| :--- | :--- |
| `NO_MEDIA` | No media is loaded. |
| `LOADING` | Media URI is being loaded by a backend plugin. |
| `LOADED_MEDIA` | Media is loaded and ready; backend begins playback automatically. `BaseMediaService.handle_media_state_change — ovos_media/media_backends/base.py:133` |
| `END_OF_MEDIA` | Playback has finished; `NowPlaying.reset()` is called. `NowPlaying.handle_media_state_change — ovos_media/player.py:300` |

State changes are made through `OCPMediaPlayer.set_media_state` — `ovos_media/player.py:573`, which emits `ovos.common_play.media.state`.

---

## GUI Integration

`OCPMediaPlayer` holds a `GUIInterface("ovos.common_play")` instance, created in `OCPMediaPlayer.bind` — `ovos_media/player.py:391`.

After every state change, `OCPMediaPlayer._update_gui` — `ovos_media/player.py:439` is called. It maps the current `PlayerState` to one of the strings `"playing"`, `"paused"`, or `"stopped"` and invokes:

```python
self.gui.show_media_player(
    now_playing=np.as_dict if np and np.uri else None,
    playlist=self.playlist.as_list(),
    search_results=self._last_search_results,
    state=state_map[self.state],
)
```

The GUI client (`ovos-gui` or another `GUIInterface` consumer) renders the media player screen. Individual backend plugins that handle video or web content render in their own GUI namespaces, independent of the `"ovos.common_play"` namespace.

`MediaService.handle_search_start` — `ovos_media/service.py:76` calls `show_media_player(state="loading")` directly when the OCP pipeline begins searching, before any results are available.

---

## MPRIS Integration

`OcpMprisExporter` (`ovos_media/mpris.py`) runs in a background thread. It serves two roles:

- **Role A (always active when enabled)**: Exposes OCP as a D-Bus MPRIS 2.0 player so that external desktop widgets and media keys interact with it.
- **Role B (optional)**: When `manage_external_players = true` in config, it also monitors external MPRIS players and hands control to `OCPMediaPlayer` when they become active.

MPRIS is enabled via the `enable_mpris` config key. It is disabled by default. When enabled, `OCPMediaPlayer.bind` — `ovos_media/player.py:383` creates the `OcpMprisExporter` and passes `manage_players` from `ocp_config.get("manage_external_players", False)`.

When player state changes, `set_player_state` — `ovos_media/player.py:586` calls `self.mpris.update_props({"PlaybackStatus": ..., "CanPause": ..., "CanPlay": ...})`.

---

## Plugin System

Backend services are loaded by `BaseMediaService.load_services` — `ovos_media/media_backends/base.py:75`. At startup it calls the injected `plugin_loader` callable (one of `find_ocp_audio_plugins`, `find_ocp_video_plugins`, or `find_ocp_web_plugins` from `ovos-plugin-manager`) to get a dict of available plugin classes, then instantiates each plugin that is present in the `media.<namespace>_players` configuration block and not marked `active: false`.

Local (non-remote) backend instances are placed before remote instances in `self.services` — `ovos_media/media_backends/base.py:106`, so local backends are tried first when selecting a playback backend by URI type.

The three concrete subclasses and their OPM discovery functions are:

| Class | Module | OPM function |
| :--- | :--- | :--- |
| `AudioService` | `ovos_media/media_backends/audio.py:8` | `find_ocp_audio_plugins` |
| `VideoService` | `ovos_media/media_backends/video.py:8` | `find_ocp_video_plugins` |
| `WebService` | `ovos_media/media_backends/web.py:8` | `find_ocp_web_plugins` |

---

## Per-session player state

The OCP pipeline plugin does not assume a single global player. It tracks one
player proxy per MessageBus session, recording that session's player and media
state, the classified media type, and which extractors that player can resolve.

This matters for distributed setups (for example HiveMind satellites): each
device has its own session, so a user on a satellite plays media on **that**
device's player rather than the hub's. `ovos-media` participates by announcing
itself on the bus and reporting its state through `handle_status`, which the
pipeline associates with the originating session.

---

## See also

- [Media providers](media-providers.md) — the catalog/search layer feeding this daemon
- [Backends](backends.md) — the audio/video/web playback plugins
- [Configuration](configuration.md) — the `media` config block
- [MPRIS integration](mpris.md) — the D-Bus exporter in depth
- [Migration guide](migration-guide.md) — moving from the legacy audio service

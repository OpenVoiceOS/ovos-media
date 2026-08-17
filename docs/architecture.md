# Architecture

This document describes the runtime architecture of `ovos-media`, the playback
daemon, and how it sits inside the wider OCP media flow.

---

## The media flow end to end

`ovos-media` is one stage in a plugin pipeline. Each boundary is swappable:

```
 utterance
   │
   ▼
 ovos-core ─ OCP pipeline (ovos-ocp-pipeline-plugin)
   │   classifies the media type, parses title/artist/genre into mediavocab.Signals,
   │   dispatches MediaProvider plugins in-process, ranks the Release results
   ▼
 ovos-media (this daemon)
   │   receives the winning result, picks a playback backend, manages the
   │   queue / now-playing, broadcasts state over the bus and MPRIS
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

1. **`MediaService`** (`ovos_media/service.py`), the process entry point. It runs
   as a `Thread`, owns the `MessageBusClient` connection, instantiates
   `OCPMediaPlayer`, and answers a small set of top-level bus topics.

2. **`OCPMediaPlayer`** (`ovos_media/player.py`), the virtual media player. It is
   a plain bus-connected class (it is *not* a skill or an `OVOSAbstractApplication`)
   that manages the playback state machine (`PlayerState`, `MediaState`), owns the
   `NowPlaying` tracker and three backend service objects (`AudioService`,
   `VideoService`, `WebService`), broadcasts every state change on the bus, and
   optionally drives MPRIS. All of this is wired up in `OCPMediaPlayer.__init__`.
   There is no GUI client in-process: a UI (`ovos-webui` or otherwise) consumes
   the same bus broadcasts any other client does, live — see
   [Bus as the UI contract](#bus-as-the-ui-contract).

3. **Backend services** (`ovos_media/media_backends/`), three concrete
   `BaseMediaService` subclasses (`AudioService`, `VideoService`, `WebService`).
   Each loads OPM plugins at startup and delegates actual playback to the selected
   plugin instance.

Every subscription `MediaService`, `OCPMediaPlayer`, `NowPlaying` and
`OCPMediaCatalog` make lives in the bus edge, `ovos_media/bus/api.py`.
`OCPBusApi` holds one registration table naming, per topic, the payload decoder
to run, whether the topic is gated to the local session, and the method to
call. The same table drives teardown, so a shut-down daemon cannot keep
answering a topic it registered.

The backend services are the exception: each of `AudioService`,
`VideoService` and `WebService` binds its own
`ovos.common_play.media.state` listener in `BaseMediaService.__init__`, and the
skill base class the catalog inherits from registers its intents and keyword
plumbing itself. Reading the table therefore tells you what the player layer
answers, not everything this process has bound.

Payload validation for all layers lives in `ovos_media/bus/schemas.py`.
Every rule an incoming bus payload must satisfy — numeric fields that must be
finite, uri characters that would inject newlines into a log viewer or an HTTP
stack downstream, track lists that must be coerced entry by entry, enum states
that arrive as bare ints — is a plain function over a raw value there, so no
handler restates the numeric, uri, state, or track-list rules inline.

These layers communicate via the OVOS MessageBus (WebSocket pub/sub). The
`MediaService` layer handles service-lifecycle and discovery events;
`OCPMediaPlayer` handles all media-control events from external callers; backend
services handle low-level playback over namespaced bus events.

### NowPlaying

`OCPMediaPlayer.now_playing` is a `NowPlaying` instance, a `MediaEntry` subclass
holding the currently-playing track's metadata, status (`TrackState`), and seek
position. It subscribes to nothing itself: the bus edge routes
`ovos.common_play.track.state`, `ovos.common_play.play` and
`ovos.common_play.playback_time` to it, and the player forwards end-of-media
from `ovos.common_play.media.state` as a plain method call so the reset cannot
race the autoplay decision that reads it. When a backend
confirms playback (`TrackState.PLAYING_*`), `NowPlaying` calls back into the
player to set `PlayerState.PLAYING`; on `MediaState.END_OF_MEDIA` it resets.

> There is no paused `TrackState`. Pause/resume are `PlayerState`/`MediaState`
> concerns; `TrackState` only describes queued/playing/disambiguation status.

---

## Message Bus Events

### MediaService handlers

Registered in `MediaService.__init__` and `MediaService.init_messagebus`
(`ovos_media/service.py`).

| Message type | Handler | Notes |
| :--- | :--- | :--- |
| `ovos.common_play.ping` | `handle_ping` | Replies immediately with `ovos.common_play.pong`. |
| `opm.audio.query` | `handle_opm_audio_query` | Replies with the dict returned by `audio_service.available_backends()`, preserving the legacy OPM discovery contract. |

`ovos-media` does not implement the classic `mycroft.audio.service.*` API.
Skills that still call it are served by the old ovos-audio/OCP stack, which
stays installed alongside `ovos-media` and answers that surface directly.

`ovos.common_play.home`, `.search.start`, and `.search.end` are pipeline-side
signals the OCP pipeline plugin uses to drive a GUI's own navigation/loading
state. Neither `MediaService` nor `OCPMediaPlayer` subscribes to any of the
three — this daemon has no other state to change in response to them, and a
prior version's `home` binding to a player reset was a bug: the pipeline
emits `home` on routine "open media player" intents, and resetting killed
in-progress playback the reset never actually stopped at the backend. A bus
message with no subscriber here is legal.

### OCPMediaPlayer handlers

Registered by the bus edge (`ovos_media/bus/api.py`), which subscribes every
topic in its table, decodes the payload, applies the session gate, and then
calls the handler below.

| Message type | Handler | Notes |
| :--- | :--- | :--- |
| `ovos.common_play.play` | `handle_play_request` | Starts playback of a new track or playlist. |
| `ovos.common_play.pause` | `handle_pause_request` | Pauses current playback and sets `PlayerState.PAUSED`. |
| `ovos.common_play.play_pause` | `handle_pause_toggle_request` | Toggles between play and pause. |
| `ovos.common_play.resume` | `handle_resume_request` | Resumes paused playback and sets `PlayerState.PLAYING`. |
| `ovos.common_play.stop` | `handle_stop_request` | Stops all active backends and sets `PlayerState.STOPPED`. |
| `ovos.common_play.next` | `handle_next_request` | Advances to the next track, respecting shuffle and loop state. |
| `ovos.common_play.previous` | `handle_prev_request` | Returns to the previous track. |
| `ovos.common_play.seek` | `handle_seek_request` | Seeks to a position (data in seconds or an absolute `seekValue`). |
| `ovos.common_play.get_track_length` | `handle_track_length_request` | Replies with the current track length. |
| `ovos.common_play.set_track_position` | `handle_set_track_position_request` | Seeks to an absolute track position. |
| `ovos.common_play.get_track_position` | `handle_track_position_request` | Replies with current playback position. |
| `ovos.common_play.track_info` | `handle_track_info_request` | Replies with track metadata (via `message.response`). |
| `ovos.common_play.list_backends` | `handle_list_backends_request` | Replies with the dict of loaded audio backends. |
| `ovos.common_play.playlist.set` | `handle_playlist_set_request` | Replaces the current playlist. |
| `ovos.common_play.playlist.queue` | `handle_playlist_queue_request` | Appends tracks to the current playlist. |
| `ovos.common_play.playlist.clear` | `handle_playlist_clear_request` | Clears the current playlist. |
| `ovos.common_play.shuffle.toggle` / `.set` / `.unset` | `handle_shuffle_toggle_request` / `handle_set_shuffle` / `handle_unset_shuffle` | Shuffle controls. |
| `ovos.common_play.repeat.toggle` / `.set` / `.unset` | `handle_repeat_toggle_request` / `handle_set_repeat` / `handle_unset_repeat` | Repeat / loop controls. |
| `ovos.common_play.duck` | `handle_duck_request` | Lowers backend volume. |
| `ovos.common_play.unduck` | `handle_unduck_request` | Restores backend volume. |
| `ovos.common_play.cork` | `handle_cork_request` | Pauses playback while the mic is open. |
| `ovos.common_play.uncork` | `handle_uncork_request` | Resumes playback after recording finishes. |
| `ovos.common_play.SEI.get` | `handle_get_SEIs` | Replies with the supported stream-extractor identifiers. |
| `ovos.common_play.like` / `.unlike` | `handle_like` / `handle_unlike` | Add/remove the current track in the liked-songs store. |
| `ovos.common_play.status` | `handle_status` | Replies with full player status (state, media type, position, shuffle, loop). |
| `ovos.common_play.mpris.now_playing` | `handle_mpris_now_playing` | Reflects an **external** MPRIS player as OCP now-playing, see [MPRIS](#mpris-integration). |
| `ovos.audio.output.started` / `ovos.audio.output.ended` | `handle_duck_request` / `handle_unduck_request` | ovos-audio emits these unconditionally on every TTS output; bound to the same handlers as `ovos.common_play.duck` / `.unduck`. |
| `recognizer_loop:record_begin` / `:record_end` | `handle_cork_request` / `handle_record_end` | Bound to the microphone recording window; `record_end` auto-uncorks if no `speak` arrives within 8 s. |
| `ovos.utterance.handled` | `handle_utterance_handled` | Restores volume once speech finishes. |
| `mycroft.stop` | `handle_mycroft_stop` | Global stop, stops all backends, resets state, replies `mycroft.stop.handled`. |

### Emitted events

| Message type | Emitter | Payload |
| :--- | :--- | :--- |
| `ovos.common_play.player.state` | `OCPMediaPlayer.set_player_state` | `{"state": PlayerState}` |
| `ovos.common_play.media.state` | `OCPMediaPlayer.set_media_state` | `{"state": MediaState}` |
| `ovos.common_play.track.state` | `BaseMediaService.handle_media_state_change` (and the backend templates) | `{"state": TrackState}` |
| `ovos.common_play.status.response` | `OCPMediaPlayer.handle_status` (reply to `ovos.common_play.status`) | full status snapshot |
| `ovos.common_play.pong` | `MediaService.handle_ping` | empty data |
| `mycroft.audio.play_sound` | `OCPMediaPlayer.on_invalid_stream` / `handle_like` | `{"uri": "snd/…"}` |
| `mycroft.stop.handled` | `OCPMediaPlayer.handle_mycroft_stop` | `{"by": "ovos-media"}` |

`handle_status` is also emitted (as a self-addressed `ovos.common_play.status`
response) on startup and after every `set_player_state` / `set_now_playing`, so
ovos-core's OCP pipeline always has a current snapshot for the session.

---

## State Machine

Two orthogonal state enums are tracked by `OCPMediaPlayer`, both defined in
`ovos_utils.ocp`.

### PlayerState

Represents the action the player is currently performing.

| Value | Meaning |
| :--- | :--- |
| `PLAYING` | Media is actively playing. |
| `PAUSED` | Playback is paused; resumable. |
| `STOPPED` | No active playback. |

State changes are made exclusively through `OCPMediaPlayer.set_player_state`,
which updates `self.state`, emits `ovos.common_play.player.state`, updates MPRIS
props, and calls `handle_status` to report full status to ovos-core.

### MediaState

Represents the lifecycle stage of the loaded media item.

| Value | Meaning |
| :--- | :--- |
| `NO_MEDIA` | No media is loaded. |
| `LOADED_MEDIA` | Media is loaded and ready; the backend begins playback automatically (`BaseMediaService.handle_media_state_change`). |
| `BUFFERED_MEDIA` | Media is buffered/playing (used by the external-player reflection path). |
| `END_OF_MEDIA` | Playback has finished; `NowPlaying.reset()` is called. |
| `INVALID_MEDIA` | The loaded URI could not be played; the player advances or shows an error. |

State changes are made through `OCPMediaPlayer.set_media_state`, which emits
`ovos.common_play.media.state`.

---

## Bus as the UI contract

`ovos-media` has no GUI client in-process. Every state transition — track
changes, play/pause/stop, queue and shuffle/repeat updates — is broadcast on
the bus as it happens: `ovos.common_play.player.state`,
`ovos.common_play.media.state`, `ovos.common_play.track.state`, and the full
snapshot on `ovos.common_play.status.response` (see
[Emitted events](#emitted-events)). A UI is just another bus client: it
subscribes to those broadcasts and to `ovos.common_play.status` for an
on-demand snapshot, the same way `ovos-webui` does. This keeps `ovos-media`
itself free of any rendering, theming, or display-technology dependency —
any future GUI is an outboard client, not a component this daemon loads or
manages.

Video and web backend plugins that render their own content (a webview, a
Chromecast target, …) do so independently; `ovos-media` only reports their
state over the bus.

---

## MPRIS Integration

`OcpMprisExporter` (`ovos_media/mpris.py`) runs in a background thread. MPRIS
participation is enabled with the `enable_mpris` config key (off by default); when
enabled, `OCPMediaPlayer.__init__` creates the exporter, passing `manage_players`
from `ocp_config.get("manage_external_players", False)`.

There are two distinct directions:

- **OCP *as* an MPRIS server (Role A, outbound).** `OcpMprisExporter` registers
  `org.mpris.MediaPlayer2.OCP` on the D-Bus session bus so desktop widgets, media
  keys, and `playerctl` can control OCP. When player state changes,
  `set_player_state` calls `self.mpris.update_props({...})` to keep PlaybackStatus,
  CanPause/CanPlay, Metadata, and the rest in sync.

- **External player → OCP now-playing (inbound reflection).** An external player
  (Spotify, a browser, VLC…) can be reflected *as* OCP's now-playing without OCP
  driving any backend, via
  `OCPMediaPlayer.set_external_now_playing` / the `ovos.common_play.mpris.now_playing`
  bus message handled by `handle_mpris_now_playing`. The reflection sets
  `playback_type = PlaybackType.MPRIS`, updates now-playing metadata and
  player/media state, and on a *new* external player taking over it first calls
  `handle_MPRIS_takeover()` to stop OCP's own audio/video/web backends so the two
  do not overlap. This message is emitted by the standalone
  `ovos-media-plugin-mpris` watcher; the same reflection also runs in-process when
  `manage_external_players` is enabled. See [mpris.md](mpris.md) for the full
  picture.

---

## Plugin System

Backend services are loaded by `BaseMediaService.load_services`
(`ovos_media/media_backends/base.py`). At startup it calls the injected
`plugin_loader` callable (one of `find_ocp_audio_plugins`, `find_ocp_video_plugins`,
or `find_ocp_web_plugins` from `ovos-plugin-manager`) to get a dict of available
plugin classes, then instantiates each plugin that is present in the
`media.<namespace>_players` configuration block and not marked `active: false`.

Local (non-remote) backend instances are placed before remote instances in
`self.services`, so local backends are tried first when selecting a playback
backend by URI type.

The three concrete subclasses and their OPM discovery functions and entry-point
groups are:

| Class | Module | OPM function | Entry-point group |
| :--- | :--- | :--- | :--- |
| `AudioService` | `ovos_media/media_backends/audio.py` | `find_ocp_audio_plugins` | `opm.media.audio` |
| `VideoService` | `ovos_media/media_backends/video.py` | `find_ocp_video_plugins` | `opm.media.video` |
| `WebService` | `ovos_media/media_backends/web.py` | `find_ocp_web_plugins` | `opm.media.web` |

Catalog/search providers (`opm.media.provider`) are *not* loaded by this daemon —
they are loaded in-process by the OCP pipeline plugin. See
[media-providers.md](media-providers.md).

---

## Per-session player state

The OCP pipeline plugin does not assume a single global player. It tracks one
player proxy per MessageBus session, recording that session's player and media
state, the classified media type, and which extractors that player can resolve.

This matters for distributed setups (for example HiveMind satellites): each
device has its own session, so a user on a satellite plays media on **that**
device's player rather than the hub's. `ovos-media` participates by reporting its
state through `handle_status`, which the pipeline associates with the originating
session.

`ovos-media` itself stays a **single** player bound to its own device, the
`"default"` session. Its playback-executing handlers are gated by
`is_default_session()` (`ovos_media/utils.py`), so a server-side daemon
**ignores** a satellite's forwarded command (`session_id != "default"`) while
the satellite's own embedded daemon executes it. Read-only query handlers stay
ungated so a remote pipeline can still read state. See
[Sessions](sessions.md) for the full topology, the gated-handler list, and the
`validate_source` config knob.

---

## See also

- [Sessions](sessions.md), the default/local session filter (HiveMind topology)
- [Media providers](media-providers.md), the catalog/search layer feeding this daemon
- [Backends](backends.md), the audio/video/web playback plugins
- [Configuration](configuration.md), the `media` config block
- [MPRIS integration](mpris.md), the D-Bus exporter and external-player reflection
- [Migration guide](migration-guide.md), moving from the legacy audio service

---
[← Getting started](getting-started.md) · [Home](../README.md) · [Sessions →](sessions.md)

# MPRIS Integration

MPRIS (Media Player Remote Interfacing Specification) is a D-Bus standard. It lets
external media controllers, such as KDE Connect, the Plasma media widget,
`playerctl`, and GNOME Shell, discover and control any compliant media player on the
same desktop session. `ovos-media` participates in MPRIS in three ways:

1. **OCP as an MPRIS server (Role A, outbound).** OCP exposes itself as a controllable `org.mpris.MediaPlayer2.OCP` player so desktop tools and media keys can drive it.
2. **External player to OCP now-playing (inbound reflection).** What another MPRIS player is playing is mirrored *into* OCP as its now-playing, without OCP driving any backend, so voice queries and any bus-subscribed UI reflect reality.
3. **OCP managing external players (Role B, opt-in).** OCP polls the session bus and proxies its transport controls onto a chosen external player.

All three are gated behind `enable_mpris: true`. Roles B and the in-process reflection
also require `manage_external_players: true`. The inbound reflection also arrives over
the bus from the standalone [`ovos-media-plugin-mpris`](https://github.com/OpenVoiceOS/ovos-media-plugin-mpris) watcher.

## Architecture

The entry point is `OcpMprisExporter` (`ovos_media/mpris/`), which the player holds and which wires three units together:

- `DbusLoop` (`loop.py`) owns the thread, the `asyncio` loop and the D-Bus connection. Everything MPRIS does runs there. Work crosses onto that thread through `DbusLoop.call_async`, which posts a coroutine; player state crosses back out through the dispatcher, so the player keeps a single writer.
- `MprisExporter` (`exporter.py`) is Role A: the two D-Bus service interfaces and their properties.
- `ExternalPlayerManager` (`manager.py`) is Role B. It is inert unless `manage_external_players` is set, and the package boundary around it is where the cut to `ovos-media-plugin-mpris` goes.

The exporter owns two service interfaces:

- `_MediaPlayer2Interface` (`org.mpris.MediaPlayer2`), identity, MIME type list, URI scheme list
- `_MediaPlayer2PlayerInterface` (`org.mpris.MediaPlayer2.Player`), playback state, metadata, transport controls

Both interfaces are exported at the object path `/org/mpris/MediaPlayer2` and the well-known name `org.mpris.MediaPlayer2.OCP` is requested from the session bus (`MprisExporter.export`).

### Reading player state from the D-Bus thread

The property getters run on the D-Bus thread, which is not the thread that mutates the player. They therefore answer from the `PlayerSnapshot` the dispatcher publishes after every command, not from live player attributes. `Position` is the one exception, because it advances between commands and a snapshot value would leave every controller showing a frozen seekbar; the dispatcher sanctions that single live read. `Metadata` is the other, because it is built from the live `now_playing` entry.

`set_player_state` republishes the snapshot before emitting `PropertiesChanged`, so a controller that reads a property in response to the signal is not answered with the state the signal says has changed.

## Role A: OCP as an MPRIS player

This role is always active when `enable_mpris: true`. Any MPRIS controller on the same desktop session can see OCP and issue commands.

### Exposed properties

| MPRIS property | Notes |
|---|---|
| `PlaybackStatus` | Maps `PlayerState.PLAYING` → `"Playing"`, `PlayerState.PAUSED` → `"Paused"`, anything else → `"Stopped"` |
| `Metadata` | Returns `now_playing.mpris_metadata` when a track is loaded, otherwise an empty dict |
| `Position` | Returns `now_playing.position * 1000` when a track is loaded, otherwise `0`. Position is milliseconds internally and microseconds on the wire. Clamped into `int64` and coerced to `0` for a missing or malformed value |
| `Volume` | Read via `mycroft.volume.get` bus message with a 0.5-second timeout, falls back to `1.0` if no reply. Writable (`mycroft.volume.set`) |
| `Shuffle` | Mirrors `OCPMediaPlayer.shuffle`. Writable |
| `LoopStatus` | See mapping table below. Writable |
| `Rate` | Always returns `1.0`. Not writable |
| `CanSeek` | `True` when a player in the roster takes the seek verb for the current playback type. Audio and video seek; skill playback, external-MPRIS playback and an idle player do not |
| `CanPlay` | `True` when `PlayerState.PAUSED` |
| `CanPause` | `True` when playing *and* a player takes the pause verb for the current playback type |
| `CanGoNext` | Mirrors `OCPMediaPlayer.can_next` |
| `CanGoPrevious` | Mirrors `OCPMediaPlayer.can_prev` |
| `CanControl` | Always `True` |

### Seeking

The Player interface exports `Seek(x offset_us)` and `SetPosition(o track_id, x position_us)`, and emits `Seeked(x position_us)` once a seek has been applied. Offsets and positions are microseconds on the wire and milliseconds inside `ovos-media`, so both convert on the way in; a relative seek that would land before the start of the track is clamped to zero.

`SetPosition` compares `track_id` against the current track and ignores a mismatch, as the spec requires, so a seekbar drag cannot jump whichever track replaced the one the client was looking at. The track identity is published as `mpris:trackid` in `Metadata`, derived from the current uri: stable while that track plays, different for the next one. With nothing loaded it is `/org/mpris/MediaPlayer2/TrackList/NoTrack`.

`CanSeek` reports `False` whenever a seek would not reach a player that can honour it, which includes an idle player: `PlaybackType.UNDEFINED` has a row in the routing table only because stop and pause fan out to every backend when nothing is loaded, and that is the wrong answer to "can this be seeked". A `Seek` or `SetPosition` arriving while idle is ignored rather than routed.

### LoopStatus mapping

`LoopStatus` translates between the MPRIS 2.2 string values (`"None"`, `"Track"`,
`"Playlist"`) and the `LoopState` enum from `ovos_utils.ocp`. Both the getter and
the setter use the same canonical MPRIS strings.

| MPRIS string | `LoopState` (internal) |
|---|---|
| `"Track"` | `LoopState.REPEAT_TRACK` |
| `"Playlist"` | `LoopState.REPEAT` |
| `"None"` | `LoopState.NONE` |

### Volume control

Volume reads and writes are delegated to the OCP bus rather than a direct audio API. Reading sends `mycroft.volume.get` and writing sends `mycroft.volume.set` with `{"percent": value}`.

## Inbound reflection: an external player as OCP now-playing

Independent of who *controls* whom, OCP can mirror what an external MPRIS player
is playing into its own now-playing state, title, artist, art, and player/media
state, **without driving any OCP backend**. This is the playback-less path: it
exists so voice queries ("what song is this?") and any bus-subscribed UI
reflect a player that OCP did not itself start, such as Spotify, a browser, or
VLC.

The entry point is `OCPMediaPlayer.set_external_now_playing(data)`, reachable two
ways:

- **Over the bus**, the `ovos.common_play.mpris.now_playing` message (handled by
  `OCPMediaPlayer.handle_mpris_now_playing`). The standalone
  [`ovos-media-plugin-mpris`](https://github.com/OpenVoiceOS/ovos-media-plugin-mpris)
  watcher runs out-of-process, observes external MPRIS players, and emits this
  message. This is the recommended way to watch external players.
- **In-process**, the inline Role B below calls `set_external_now_playing`
  directly via `_update_ocp` when `manage_external_players` is enabled.

`set_external_now_playing` recognises these keys in `data`:

| Key | Meaning |
|---|---|
| `external_player` (or `skill_id`) | the external player's MPRIS bus name, required |
| `title` / `artist` / `image` / `length` | track metadata (`length` in ms) |
| `state` | `"Playing"` (default), `"Paused"`, or `"Stopped"` |
| `skill_icon` | optional player icon |

The reflection sets `playback_type = PlaybackType.MPRIS`, status
`TrackState.PLAYING_MPRIS`, and updates now-playing + player/media state to match
`state`. When a *new* external player starts playing (a different player than the
one currently reflected), it first calls `OCPMediaPlayer.handle_MPRIS_takeover()`.
This stops OCP's own audio/video/web backends and any active skill, so OCP and
the external player do not overlap.

## Role B: External player management

When `manage_external_players: true`, `ExternalPlayerManager` also scans the session bus for other MPRIS players and coordinates playback between them and OCP. The dedicated, recommended home for this is the standalone [`ovos-media-plugin-mpris`](https://github.com/OpenVoiceOS/ovos-media-plugin-mpris) plugin, which also provides a player backend that drives an external MPRIS player. The inline implementation below mirrors that behaviour.

### Discovery

`ExternalPlayerManager.scan_players` calls `org.freedesktop.DBus.ListNames` and filters results to names that contain `org.mpris.MediaPlayer2`. Players already tracked, KDE Connect proxy players (`org.mpris.MediaPlayer2.kdeconnect.*`), and players listed in `ignored_players` are skipped. Each new player is introspected and a D-Bus property-change signal handler is attached via `_create_player_handler`.

### Active player selection

`ExternalPlayerManager._set_main_player` designates one external player as the "main player". When an external player reports `PlaybackStatus = "Playing"`, it becomes the main player. If a second player also starts playing, the previous one is stopped.

### OCP takeover

When an external player becomes active, `handle_player_state` calls `OCPMediaPlayer.handle_MPRIS_takeover()` and sets `playback_type = PlaybackType.MPRIS`. OCP then reflects the external player's metadata (title, artist, album, art) via `_update_ocp`, which calls `set_now_playing` so the reflected track is broadcast on the bus (see [Inbound reflection](#inbound-reflection-an-external-player-as-ocp-now-playing)).

Dedicated icons are substituted for known players: Spotify, Firefox, Chromium, VLC, MPV, and Audacious. All others receive the generic MPRIS icon.

Each external player also joins the player roster as an `MprisPlayerAdapter`, so the virtual player's picture of what can play media on this machine includes the players it does not own. Those adapters are marked external, and `handle_MPRIS_takeover` skips them: the takeover exists to yield to one of them, so sweeping it would stop the playback that triggered the takeover.

### Poll interval

The event loop polls at `mpris_poll_interval` seconds (default `1`). Two polls occur per cycle: one for discovering new players and one for re-querying existing players (to catch browsers that do not emit events on autoplay).

### Failure handling

`query_player` increments a failure counter per player. After three consecutive failures the player is treated as gone and removed.

## Configuration

All options live under the `"media"` section of the OVOS configuration.

```json
{
  "media": {
    "enable_mpris": true,
    "manage_external_players": false,
    "mpris_poll_interval": 1,
    "dbus_type": "session",
    "ignored_players": [
      "org.mpris.MediaPlayer2.OCP",
      "org.mpris.MediaPlayer2.plasma-browser-integration"
    ]
  }
}
```

| Key | Type | Default | Description |
|---|---|---|---|
| `enable_mpris` | `bool` | `false` | Enable Role A: register OCP on D-Bus |
| `manage_external_players` | `bool` | `false` | Enable Role B: observe and manage external MPRIS players |
| `mpris_poll_interval` | `int` | `1` | Seconds between each D-Bus scan cycle |
| `dbus_type` | `str` | `"session"` | `"session"` or `"system"` |
| `ignored_players` | `list[str]` | see default | D-Bus names to skip during external player scanning |

The default `ignored_players` list is set in `ExternalPlayerManager.__init__`.

## Using playerctl

`playerctl` can control OCP from the command line once `enable_mpris: true` is set.

```bash
# Show current playback status
playerctl --player=OCP status

# Get the title of the current track
playerctl --player=OCP metadata title

# Pause
playerctl --player=OCP pause

# Skip to next track
playerctl --player=OCP next

# Skip to previous track
playerctl --player=OCP previous

# Seek forward five seconds
playerctl --player=OCP position 5+

# Jump to 30 seconds
playerctl --player=OCP position 30
```

## Behaviour notes

- `CanSeek` and `CanPause` report what the routing table in `ovos_media/player/roster.py` says can actually be reached, rather than a fixed answer. `CanSeek` additionally reports `False` while idle.
- `Rate` always reports `1.0`. Variable playback speed is not exposed over MPRIS.
- Volume read uses a 0.5-second bus timeout. If the volume service is unavailable, the getter returns `1.0`.
- The `dbus_next` library is patched at import time (`patch_dbus_next`) to ignore malformed introspection XML. This accommodates players that expose invalid D-Bus introspection data.

---

## See also

- [Architecture](architecture.md), the MPRIS exporter inside the daemon
- [Configuration](configuration.md), the `media` config block
- [Backends](backends.md), the playback plugins MPRIS controls
- [`ovos-media-plugin-mpris`](https://github.com/OpenVoiceOS/ovos-media-plugin-mpris), the standalone external-player watcher and MPRIS player backend

---
[← Configuration](configuration.md) · [Home](../README.md) · [Migration guide →](migration-guide.md)

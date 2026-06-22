# MPRIS Integration

MPRIS (Media Player Remote Interfacing Specification) is a D-Bus standard that allows external media controllers — KDE Connect, Plasma media widget, `playerctl`, GNOME Shell, and others — to discover and control any compliant media player on the same desktop session. `ovos-media` implements both sides of this interface: it exposes OCP as a controllable player, and it can optionally observe and manage other MPRIS players running on the same session bus.

## Architecture

The entry point is `OcpMprisExporter` — `ovos_media/mpris.py:57`. It is a `Thread` subclass that runs an `asyncio` event loop and owns two D-Bus service interfaces:

- `_MediaPlayer2Interface` (`org.mpris.MediaPlayer2`) — identity, MIME type list, URI scheme list — `ovos_media/mpris.py:695`
- `_MediaPlayer2PlayerInterface` (`org.mpris.MediaPlayer2.Player`) — playback state, metadata, transport controls — `ovos_media/mpris.py:760`

Both interfaces are exported at the object path `/org/mpris/MediaPlayer2` and the well-known name `org.mpris.MediaPlayer2.OCP` is requested from the session bus — `OcpMprisExporter.export_ocp` — `ovos_media/mpris.py:113`.

The backward-compatibility alias `MprisPlayerCtl = OcpMprisExporter` is defined at `ovos_media/mpris.py:692`.

## Role A: OCP as an MPRIS player

This role is always active when `enable_mpris: true`. Any MPRIS controller on the same desktop session can see OCP and issue commands.

### Exposed properties

| MPRIS property | Source | Notes |
|---|---|---|
| `PlaybackStatus` | `_MediaPlayer2PlayerInterface.PlaybackStatus` — `ovos_media/mpris.py:772` | Maps `PlayerState.PLAYING` → `"Playing"`, `PlayerState.PAUSED` → `"Paused"`, anything else → `"Stopped"` |
| `Metadata` | `_MediaPlayer2PlayerInterface.Metadata` — `ovos_media/mpris.py:766` | Returns `now_playing.mpris_metadata` when a track is loaded, otherwise an empty dict |
| `Position` | `_MediaPlayer2PlayerInterface.Position` — `ovos_media/mpris.py:822` | Returns `now_playing.position * 1e6` (microseconds, as required by the MPRIS spec) when a track is loaded, otherwise `0` |
| `Volume` | `_MediaPlayer2PlayerInterface.Volume` — `ovos_media/mpris.py:807` | Read via `mycroft.volume.get` bus message with a 0.5-second timeout; falls back to `1.0` if no reply |
| `Shuffle` | `_MediaPlayer2PlayerInterface.Shuffle` — `ovos_media/mpris.py:799` | Mirrors `OCPMediaPlayer.shuffle`; writable |
| `LoopStatus` | `_MediaPlayer2PlayerInterface.LoopStatus` — `ovos_media/mpris.py:781` | See mapping table below; writable |
| `Rate` | `_MediaPlayer2PlayerInterface.Rate` — `ovos_media/mpris.py:818` | Always returns `1.0`; not writable |
| `CanSeek` | `_MediaPlayer2PlayerInterface.CanSeek` — `ovos_media/mpris.py:836` | Always returns `False` |
| `CanPlay` | `_MediaPlayer2PlayerInterface.CanPlay` — `ovos_media/mpris.py:828` | `True` when `PlayerState.PAUSED` |
| `CanPause` | `_MediaPlayer2PlayerInterface.CanPause` — `ovos_media/mpris.py:832` | `True` when `PlayerState.PLAYING` |
| `CanGoNext` | `_MediaPlayer2PlayerInterface.CanGoNext` — `ovos_media/mpris.py:840` | Mirrors `OCPMediaPlayer.can_next` |
| `CanGoPrevious` | `_MediaPlayer2PlayerInterface.CanGoPrevious` — `ovos_media/mpris.py:844` | Mirrors `OCPMediaPlayer.can_prev` |
| `CanControl` | `_MediaPlayer2PlayerInterface.CanControl` — `ovos_media/mpris.py:848` | Always `True` |

### LoopStatus mapping

`LoopStatus` translates between the MPRIS string values and the `LoopState` enum from `ovos_utils.ocp`. The getter and setter are defined at `_MediaPlayer2PlayerInterface.LoopStatus` — `ovos_media/mpris.py:781` and `LoopStatus_setter` — `ovos_media/mpris.py:789`.

| MPRIS string (getter output) | `LoopState` (internal) | MPRIS string (setter input) |
|---|---|---|
| `"RepeatTrack"` | `LoopState.REPEAT_TRACK` | `"Track"` |
| `"Repeat"` | `LoopState.REPEAT` | `"Playlist"` |
| `"None"` | `LoopState.NONE` | anything else |

Note that the getter returns `"RepeatTrack"` but the setter recognises `"Track"` — this asymmetry matches the MPRIS specification, where the getter uses freeform strings and the setter uses the canonical set `{"None", "Track", "Playlist"}`.

### Volume control

Volume reads and writes are delegated to the OCP bus rather than a direct audio API. Reading sends `mycroft.volume.get` and writing sends `mycroft.volume.set` with `{"percent": value}` — `_MediaPlayer2PlayerInterface.Volume_setter` — `ovos_media/mpris.py:814`.

## Role B: External player management

When `manage_external_players: true`, `OcpMprisExporter` additionally scans the session bus for other MPRIS players and coordinates playback between them and OCP.

### Discovery

`OcpMprisExporter.scan_players` — `ovos_media/mpris.py:422` — calls `org.freedesktop.DBus.ListNames` and filters results to names that contain `org.mpris.MediaPlayer2`. Players already tracked, KDE Connect proxy players (`org.mpris.MediaPlayer2.kdeconnect.*`), and players listed in `ignored_players` are skipped. Each new player is introspected and a D-Bus property-change signal handler is attached via `_create_player_handler` — `ovos_media/mpris.py:453`.

### Active player selection

`OcpMprisExporter._set_main_player` — `ovos_media/mpris.py:230` — designates one external player as the "main player". When an external player reports `PlaybackStatus = "Playing"`, it becomes the main player. If a second player also starts playing, the previous one is stopped — `ovos_media/mpris.py:239-246`.

### OCP takeover

When an external player becomes active, `handle_player_state` — `ovos_media/mpris.py:204` — calls `OCPMediaPlayer.handle_MPRIS_takeover()` and sets `playback_type = PlaybackType.MPRIS`. OCP then reflects the external player's metadata (title, artist, album, art) in the GUI via `_update_ocp` — `ovos_media/mpris.py:121`.

Dedicated icons are substituted for known players: Spotify, Firefox, Chromium, VLC, MPV, and Audacious — `OcpMprisExporter._update_ocp` — `ovos_media/mpris.py:165-178`. All others receive the generic MPRIS icon.

### Poll interval

The event loop polls at `mpris_poll_interval` seconds (default `1`) — `OcpMprisExporter.event_loop` — `ovos_media/mpris.py:633`. Two polls occur per cycle: one for discovering new players and one for re-querying existing players (to catch browsers that do not emit events on autoplay).

### Failure handling

`query_player` — `ovos_media/mpris.py:536` — increments a failure counter per player. After three consecutive failures the player is treated as gone and removed — `ovos_media/mpris.py:571-573`.

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

The default `ignored_players` list is set in `OcpMprisExporter.__init__` — `ovos_media/mpris.py:100-103`.

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
```

## Behaviour notes

- `CanSeek` always reports `False`; seeking is not exposed over MPRIS — `_MediaPlayer2PlayerInterface.CanSeek` — `ovos_media/mpris.py:836`.
- `Rate` always reports `1.0`; variable playback speed is not exposed over MPRIS — `_MediaPlayer2PlayerInterface.Rate` — `ovos_media/mpris.py:818`.
- Volume read uses a 0.5-second bus timeout; if the volume service is unavailable the getter returns `1.0` — `_MediaPlayer2PlayerInterface.Volume` — `ovos_media/mpris.py:808-811`.
- The `dbus_next` library is patched at import time to ignore malformed introspection XML — `patch_dbus_next` — `ovos_media/mpris.py:7`. This accommodates players that expose invalid D-Bus introspection data.

---

## See also

- [Architecture](architecture.md) — the MPRIS exporter inside the daemon
- [Configuration](configuration.md) — the `media` config block
- [Backends](backends.md) — the playback plugins MPRIS controls

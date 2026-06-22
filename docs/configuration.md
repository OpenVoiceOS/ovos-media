# Configuration Reference

All runtime settings for `ovos-media` live inside the `media` top-level key of
the OVOS/Mycroft configuration file.

---

## Where to Configure

OVOS reads configuration from (in ascending priority order):

1. `/etc/mycroft/mycroft.conf` — system-wide defaults
2. `~/.config/mycroft/mycroft.conf` — user overrides (Mycroft-compat path)
3. `~/.config/ovos/ovos.conf` — user overrides (OVOS-native path)

Place your `media` block in the user config file for the path appropriate to
your installation. Both paths are equivalent at runtime.

Minimal example:

```json
{
  "media": {
    "preferred_audio_services": ["vlc", "mpv"],
    "enable_mpris": true
  }
}
```

`MediaService.__init__` reads this section via `Configuration().get("media", {})` — `ovos_media/service.py:47`.

`OCPMediaPlayer.__init__` stores it as `self.ocp_config` — `ovos_media/player.py:345`.

---

## Backend Selection

`OCPMediaPlayer._resolve_preferred_service` — `ovos_media/player.py:650`

When `ovos-media` is about to play a track it resolves a preferred backend by
walking the ordered list of names in the relevant config key and returning the
first loaded backend whose name or aliases match. If the list is empty or no
match is found, any available backend is used.

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `preferred_audio_services` | list of strings | `[]` | Ordered preference list for audio backends (e.g. `["vlc", "mpv", "simple"]`). Also used as a generic fallback when no type-specific list is set. |
| `preferred_video_services` | list of strings | `[]` | Ordered preference list for video backends. Read by `VideoService.get_preferred_players` — `ovos_media/media_backends/video.py:21`. |
| `preferred_web_services` | list of strings | `[]` | Ordered preference list for web/webview backends. Read by `WebService.get_preferred_players` — `ovos_media/media_backends/web.py:21`. |

Backend plugin names are the entry-point keys registered under `opm.plugin.audio`,
`opm.plugin.video`, or `opm.plugin.web` in each plugin's `pyproject.toml`.

---

## Playback Behaviour

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `autoplay` | bool | `true` | Automatically start playback when search results arrive. Checked at `ovos_media/player.py:1074`. |
| `merge_search` | bool | `true` | Merge incoming search results into the existing playlist rather than replacing it. Checked at `ovos_media/player.py:565`. |
| `force_audioservice` | bool | `false` | Force audio-only playback even when a GUI is connected; bypasses video backend selection. Checked at `ovos_media/player.py:690`. |
| `playback_mode` | string | `""` | Set to `"force_audio"` (`PlaybackMode.FORCE_AUDIO`) to always use audio backends regardless of GUI availability. Checked at `ovos_media/player.py:691`. |

---

## MPRIS Integration

`OcpMprisExporter` — `ovos_media/mpris.py:57`

MPRIS (Media Player Remote Interfacing Specification) is the standard D-Bus
protocol used by desktop environments to control media players. When enabled,
`ovos-media` registers itself as `org.mpris.MediaPlayer2.OCP` on the session
bus, making it controllable from KDE Connect, `playerctl`, the GNOME Shell
media widget, and similar tools.

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `enable_mpris` | bool | `false` | Start the D-Bus MPRIS exporter. When `false`, `self.mpris` is set to `None` and no D-Bus registration occurs. Checked at `ovos_media/player.py:384`. |
| `manage_external_players` | bool | `false` | Role B behaviour: poll for external MPRIS players and pause OCP when another player becomes active; also proxies skip/pause/shuffle/repeat to the external player. Read by `OcpMprisExporter.__init__` — `ovos_media/mpris.py:99`. |
| `ignored_players` | list of strings | `["org.mpris.MediaPlayer2.OCP", "org.mpris.MediaPlayer2.plasma-browser-integration"]` | D-Bus player names excluded from external player scanning. Read by `OcpMprisExporter.__init__` — `ovos_media/mpris.py:100`. |
| `mpris_poll_interval` | int (seconds) | `1` | Interval between external player scans. Only relevant when `manage_external_players` is `true`. Read inside `OcpMprisExporter.event_loop` — `ovos_media/mpris.py:633`. |

`OcpMprisExporter.event_loop` — `ovos_media/mpris.py:575`

The event loop runs two passes per poll interval when `manage_external_players`
is `true`: one `scan_players` call followed by a `query_player` pass over all
known players. When `manage_external_players` is `false`, only the D-Bus export
(Role A) is active and the loop sleeps for `mpris_poll_interval` between
control-signal checks.

---

## Native Sources

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `native_sources` | list of strings | `["debug_cli", "audio"]` | Message sources treated as trusted native callers. Requests from these sources bypass skill-search validation. |

`MediaService.__init__` — `ovos_media/service.py:48`

---

## Full Example Configuration

```json
{
  "media": {
    "preferred_audio_services": ["vlc", "mpv"],
    "preferred_video_services": ["vlc"],
    "preferred_web_services": [],
    "autoplay": true,
    "merge_search": true,
    "force_audioservice": false,
    "enable_mpris": true,
    "manage_external_players": true,
    "mpris_poll_interval": 2,
    "ignored_players": [
      "org.mpris.MediaPlayer2.OCP",
      "org.mpris.MediaPlayer2.plasma-browser-integration",
      "org.mpris.MediaPlayer2.kdeconnect"
    ],
    "native_sources": ["debug_cli", "audio"]
  }
}
```

This configuration enables MPRIS with external player management, polls every
two seconds, prefers VLC for both audio and video, and leaves the web service
with no preference (first available backend wins).

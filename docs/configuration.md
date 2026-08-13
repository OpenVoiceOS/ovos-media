# Configuration reference

`ovos-media` reads two top-level keys from the OVOS/Mycroft configuration file:

- **`media`**, the daemon: backends, playback behaviour, MPRIS. Documented here.
- **`media_providers`**, per-provider catalog/search settings. Documented in
  [media-providers.md](media-providers.md#configuration) and summarised
  [below](#media-providers).

The legacy audio service is turned off with the top-level `enable_old_audioservice`
flag (see [getting started](getting-started.md) and the
[migration guide](migration-guide.md)).

---

## Where to Configure

OVOS reads configuration from (in ascending priority order):

1. `/etc/mycroft/mycroft.conf`, system-wide defaults
2. `~/.config/mycroft/mycroft.conf`, user overrides (Mycroft-compat path)
3. `~/.config/ovos/ovos.conf`, user overrides (OVOS-native path)

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

`MediaService.__init__` reads this section via `Configuration().get("media", {})`, and `OCPMediaPlayer.__init__` stores it as `self.ocp_config`.

---

## Backend Selection

`OCPMediaPlayer._resolve_preferred_service`

When `ovos-media` is about to play a track it resolves a preferred backend by
walking the ordered list of names in the relevant config key and returning the
first loaded backend whose name or aliases match. If the list is empty or no
match is found, any available backend is used.

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `preferred_audio_services` | list of strings | `[]` | Ordered preference list for audio backends (e.g. `["vlc", "mpv", "simple"]`). Also used as a generic fallback when no type-specific list is set. |
| `preferred_video_services` | list of strings | `[]` | Ordered preference list for video backends. |
| `preferred_web_services` | list of strings | `[]` | Ordered preference list for web/webview backends. |

Backend plugin names are the entry-point keys registered under `opm.media.audio`,
`opm.media.video`, or `opm.media.web` in each plugin's `pyproject.toml`.

---

## Declaring Backends

The `audio_players`, `video_players`, and `web_players` blocks declare which
installed backends `ovos-media` should load and how they are addressed. Each
entry's key is a local name; `module` is the plugin's entry-point name,
`aliases` are spoken names a user can say ("play on VLC"), and `active: false`
loads-time-disables a backend without removing it.

```json
{
  "media": {
    "audio_players": {
      "vlc": { "module": "ovos-media-audio-plugin-vlc", "aliases": ["VLC"], "active": true },
      "cli": { "module": "ovos-media-audio-plugin-cli", "aliases": ["Command Line"], "active": true }
    },
    "video_players": {
      "vlc": { "module": "ovos-media-video-plugin-vlc", "aliases": ["VLC"], "active": true }
    },
    "web_players": {}
  }
}
```

Web backends register under `opm.media.web`; add their declarations to
`web_players` once one is installed.

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `audio_players` | dict | `{}` | Audio backends to load (`opm.media.audio`), keyed by local name. |
| `video_players` | dict | `{}` | Video backends to load (`opm.media.video`). |
| `web_players` | dict | `{}` | Web/webview backends to load (`opm.media.web`). |

The `module` value is the plugin's **entry-point name** (e.g.
`ovos-media-audio-plugin-vlc`), which often differs from its pip package name
(`ovos-media-plugin-vlc`). See [backends.md](backends.md) for each plugin's
entry-point name.

Any additional keys inside a player entry are passed through to that backend
plugin's own `config` dict. See [backends.md](backends.md) for the backend API.

---

## Playback Behaviour

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `autoplay` | bool | `true` | Automatically advance to the next track when the current one ends (and on invalid media). |
| `merge_search` | bool | `true` | Merge incoming search results into the playback queue alongside the user playlist rather than ignoring them. |
| `force_audioservice` | bool | `false` | Force audio-only playback even when a GUI is connected; bypasses video/web backend selection. |
| `playback_mode` | enum | unset | Set to `PlaybackMode.FORCE_AUDIO` to always use audio backends regardless of GUI availability. |
| `validate_source` | bool | `true` | Only act on playback commands from the local/`"default"` session. Leave `true` on a server-side daemon so it ignores HiveMind satellite sessions; set `false` on a satellite not getting default-NAT'd sessions. See [Sessions](sessions.md). |

---

## MPRIS Integration

MPRIS (Media Player Remote Interfacing Specification) is the standard D-Bus
protocol used by desktop environments to control media players. When enabled,
`ovos-media` registers itself as `org.mpris.MediaPlayer2.OCP` on the session
bus, making it controllable from KDE Connect, `playerctl`, the GNOME Shell
media widget, and similar tools.

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `enable_mpris` | bool | `false` | Start the D-Bus MPRIS exporter. When `false`, `self.mpris` is `None` and no D-Bus registration occurs. |
| `manage_external_players` | bool | `false` | Role B behaviour: poll for external MPRIS players and pause OCP when another player becomes active; also proxies skip/pause/shuffle/repeat to the external player. |
| `ignored_players` | list of strings | `["org.mpris.MediaPlayer2.OCP", "org.mpris.MediaPlayer2.plasma-browser-integration"]` | D-Bus player names excluded from external player scanning. |
| `mpris_poll_interval` | int (seconds) | `1` | Interval between external player scans. Only relevant when `manage_external_players` is `true`. |
| `dbus_type` | string | `"session"` | `"session"` or `"system"` D-Bus to connect to. |

The event loop runs two passes per poll interval when `manage_external_players`
is `true`: one `scan_players` call followed by a `query_player` pass over all
known players. When `manage_external_players` is `false`, only the D-Bus export
(Role A) is active and the loop sleeps for `mpris_poll_interval` between
control-signal checks.

---

## Native Sources

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `native_sources` | list of strings | `["debug_cli", "audio"]` | Message sources treated as trusted native callers. Requests from these sources bypass message-context validation in the backend services. May be set at the top level (`native_sources`) or nested under `media` (`media.native_sources`, as in the example below) — both are honoured by `BaseMediaService._is_message_for_service`, with the top-level key taking precedence when both are set. |

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
    "dbus_type": "session",
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

---

## Media Providers

Catalog/search providers are configured under the **separate top-level
`media_providers` key** (not inside `media`), keyed by each provider's
entry-point name. Keys are passed through to the provider's `config`; set
`enabled: false` to disable a provider without uninstalling it.

```json
{
  "media_providers": {
    "bandcamp": { "max_pages": 2 },
    "youtube": { "max_results": 10 },
    "soundcloud": { "enabled": false }
  }
}
```

Provider settings and the full provider list are documented in
[media-providers.md](media-providers.md#configuration).

---

## See also

- [Getting started](getting-started.md), install and enable the daemon
- [Backends](backends.md), the `audio_players` / `video_players` / `web_players` plugins
- [Media providers](media-providers.md), the `media_providers` catalog/search plugins
- [MPRIS integration](mpris.md), all MPRIS-specific options in depth
- [Migration guide](migration-guide.md), mapping legacy audio-service config to `media`

---
[← Backends](backends.md) · [Home](../README.md) · [MPRIS →](mpris.md)

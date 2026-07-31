# Migration guide: legacy audio service → ovos-media

This guide covers moving media playback from the legacy audio service (the
`ovos-ocp-audio-plugin` hosted inside `ovos-audio`) to the `ovos-media` daemon:
what to turn off, what to install, and how the configuration and bus messages map
across.

`ovos-media` handles audio, video, and web playback. `ovos-audio` keeps handling
TTS. The two run side by side.

---

## At a glance

| Concern | Legacy audio service | ovos-media |
|---|---|---|
| Where it runs | Inside `ovos-audio` as a special audio backend | A separate `ovos-media` daemon |
| Config location | `Audio.backends.OCP` | top-level `media` block |
| Search/catalog layer | OCP search skills (`@ocp_search`) | [MediaProvider plugins](media-providers.md) (in-process) |
| Playback backends | OPM audio plugins | `opm.media.audio` / `.video` / `.web` plugins |
| Bus namespace | mixed `ocp.audio.*` / `mycroft.audio.service.*` | unified `ovos.common_play.*` |
| MPRIS | bolted on | built in (Roles A and B) |
| Web/webview playback | not supported | supported |
| Remote backends | not supported | supported |

---

## Step 1, disable the legacy audio service

Turn off the in-process audio service so it does not contend with `ovos-media`:

```json
{
  "enable_old_audioservice": false
}
```

Place this in `~/.config/mycroft/mycroft.conf` (or `~/.config/ovos/ovos.conf`).
`ovos-audio` keeps running for TTS.

## Step 2, install ovos-media and a backend

```bash
pip install ovos-media
pip install ovos-media-plugin-vlc        # at least one audio backend
```

## Step 3, configure ovos-media

```json
{
  "media": {
    "audio_players": {
      "vlc": { "module": "ovos-media-audio-plugin-vlc", "aliases": ["VLC"], "active": true }
    },
    "preferred_audio_services": ["vlc"],
    "enable_mpris": true
  }
}
```

See [configuration.md](configuration.md) for every key.

## Step 4, install media providers (catalogs)

The OCP pipeline finds media through [MediaProvider plugins](media-providers.md).
Install the catalogs you want and, if needed, configure them under
`media_providers`:

```bash
pip install ovos-media-provider-youtube ovos-media-provider-bandcamp
```

## Step 5, run and test

```bash
ovos-media
```

Then exercise it by voice or over the bus:

> "play jazz" · "next track" · "pause" · "resume" · "what song is this?"

If MPRIS is enabled:

```bash
playerctl --player=OCP status
playerctl --player=OCP play-pause
playerctl --player=OCP next
```

---

## Configuration mapping

**Legacy (inside `ovos-audio`):**

```json
{
  "Audio": {
    "backends": { "OCP": { "enable_mpris": true } },
    "default-backend": "vlc"
  }
}
```

**ovos-media:**

```json
{
  "media": {
    "enable_mpris": true,
    "audio_players": {
      "vlc": { "module": "ovos-media-audio-plugin-vlc", "active": true }
    },
    "preferred_audio_services": ["vlc"]
  }
}
```

Key differences:

- Settings move from `Audio.backends.OCP` to the top-level `media` block.
- Backends are declared in `audio_players` / `video_players` / `web_players` and
  preferred order is set with `preferred_audio_services` (and the video/web
  equivalents) instead of a single `default-backend`.

---

## Bus message mapping

All media control uses the `ovos.common_play.*` namespace.

### Playback control

| Action | Legacy | ovos-media |
|---|---|---|
| Start play | (OCP was special) | `ovos.common_play.play` |
| Pause | `ocp.audio.pause` | `ovos.common_play.pause` |
| Resume | `ocp.audio.resume` | `ovos.common_play.resume` |
| Stop | `ocp.audio.stop` | `ovos.common_play.stop` |
| Next | `ocp.audio.next` | `ovos.common_play.next` |
| Previous | `ocp.audio.prev` | `ovos.common_play.previous` |

### Playlist control

| Action | ovos-media |
|---|---|
| Queue track | `ovos.common_play.playlist.queue` |
| Set playlist | `ovos.common_play.playlist.set` |
| Clear playlist | `ovos.common_play.playlist.clear` |
| Shuffle toggle | `ovos.common_play.shuffle.toggle` |
| Repeat toggle | `ovos.common_play.repeat.toggle` |

### Volume and ducking

| Action | Legacy | ovos-media |
|---|---|---|
| Duck (TTS speaking) | `recognizer_loop:audio_output_start` | `ovos.common_play.duck` (legacy alias kept) |
| Unduck | `recognizer_loop:audio_output_end` | `ovos.common_play.unduck` (legacy alias kept) |
| Cork (mic open) | `recognizer_loop:record_begin` | `ovos.common_play.cork` (legacy alias kept) |
| Uncork | (implicit in `record_end`) | `ovos.common_play.uncork` + auto-uncork on `record_end` |

The legacy `recognizer_loop:*` messages remain wired as aliases, so existing
callers keep working.

### Status and info

| Action | Legacy | ovos-media |
|---|---|---|
| Player state | `ocp.player.state` | `ovos.common_play.player.state` |
| Media state | `ocp.media.state` | `ovos.common_play.media.state` |
| Track info | `ocp.audio.track_info` | `ovos.common_play.track_info` |
| Backend discovery | `opm.audio.query` | `opm.audio.query` (unchanged) |
| Full status snapshot |, | `ovos.common_play.status` |
| Like / unlike track |, | `ovos.common_play.like` / `ovos.common_play.unlike` |
| Reflect external MPRIS player |, | `ovos.common_play.mpris.now_playing` (see [mpris.md](mpris.md)) |
| Supported stream extractors |, | `ovos.common_play.SEI.get` |

See [architecture.md](architecture.md) for the complete handler and emitted-event
tables.

---

## Search skills → media providers

The catalog/search layer changes from OCP *skills* to *providers*:

- OCP search skills subclass `OVOSCommonPlaybackSkill` and answer
  `ovos.common_play.query` over the bus with `MediaEntry` results.
- MediaProviders subclass `MediaProvider`, are loaded in-process, and return
  typed `mediavocab.Release` results.

Skills still work during the transition. New catalog integrations should be
written as providers, see [media-providers.md](media-providers.md) for the
contract, a worked example, and the list of existing providers.

---

## Troubleshooting

**No audio backends loaded.** Install one and declare it:

```bash
pip install ovos-media-plugin-vlc
```

```json
{ "media": { "audio_players": {
  "vlc": { "module": "ovos-media-audio-plugin-vlc", "active": true }
} } }
```

**Audio doesn't play.** Watch the daemon log and confirm the backend reacts:

```bash
journalctl -u ovos-media -f
playerctl --player=OCP play-pause
```

**MPRIS not visible.** Enable it and check:

```json
{ "media": { "enable_mpris": true } }
```

```bash
playerctl --player=OCP status
```

---

## See also

- [Getting started](getting-started.md), install and enable
- [Configuration](configuration.md), full config reference
- [Architecture](architecture.md), layers and bus events
- [Backends](backends.md), playback plugins and writing your own
- [Media providers](media-providers.md), the catalog/search layer
- [MPRIS integration](mpris.md), D-Bus and `playerctl`

---
[← MPRIS](mpris.md) · [Home](../README.md) · [OCP skills →](ocp-skills.md)

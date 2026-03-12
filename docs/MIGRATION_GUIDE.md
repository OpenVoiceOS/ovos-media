# Migration Guide: From OCP-in-ovos-audio to ovos-media

This guide explains **what changed**, **why it changed**, and **how to migrate** from the old OCP implementation (embedded in `ovos-audio` via `ovos-ocp-audio-plugin`) to the new **`ovos-media`** service.

---

## What Was the Old OCP?

The original OCP (OpenVoiceOS Common Play) was implemented as a special hardcoded audio backend within `ovos-audio` (`PlaybackService`). It was loaded separately from normal audio backends:

```python
# Old code (ovos-audio)
class PlaybackService:
    def find_ocp(self):
        from ovos_plugin_common_play import OCPAudioBackend  # hardcoded import
        self.ocp = OCPAudioBackend(config, bus=self.bus)  # separate singleton
```

### Problems with the Old Approach

| Issue | Impact |
|-------|--------|
| **Hardcoded in ovos-audio** | OCP couldn't evolve independently; required changes to ovos-audio release cycle |
| **Monolithic** | GUI, MPRIS, playback logic, skill search all tightly coupled in one package |
| **Limited test coverage** | Hard to test in isolation; integration tests were the only option |
| **Configuration scattered** | OCP settings mixed with TTS settings under `"Audio"` key |
| **GUI rendering tightly bound** | QML pages hardcoded in the backend plugin; couldn't swap GUI implementations |
| **MPRIS in a thread** | D-Bus integration was bolted on; not a first-class concern |

---

## What Is ovos-media?

`ovos-media` is a standalone service that **cleanly separates media playback** from TTS:

- **Runs as a separate process** — doesn't depend on `ovos-audio`'s release cycle
- **Modular architecture** — three layers (MediaService, OCPMediaPlayer, backends)
- **Configurable** — media settings go under `"media"` key in `mycroft.conf`
- **GUI-agnostic** — uses `GUIInterface.show_media_player()` instead of hardcoded QML
- **MPRIS-first** — D-Bus export is a first-class feature, not an afterthought
- **Testable** — 528+ unit tests; E2E tests with `ovoscope`; **83% code coverage**

---

## Architecture Comparison

### Old OCP (in ovos-audio)

```
ovos-core
  ↓
  ocp-pipeline-plugin (classifies as media)
  ↓
OCPAudioBackend (in ovos-audio)  ← hardcoded, monolithic
  ├─ Search & skill management
  ├─ Playback state machine
  ├─ GUI rendering (QML pages)
  ├─ MPRIS D-Bus export (thread)
  └─ Audio/video/web backends (OPM plugins)
```

### New ovos-media

```
ovos-core
  ↓
  ocp-pipeline-plugin (classifies as media)
  ↓
MediaService (ovos-media)  ← separate service
  ├─ Bus lifecycle handlers
  └─ OCPMediaPlayer
      ├─ State machine (PlayerState, MediaState)
      ├─ GUI interface (GUIInterface.show_media_player)
      ├─ MPRIS exporter (OcpMprisExporter, in thread)
      └─ Three backend services (Audio, Video, Web)
          └─ OPM plugins (vlc, mpv, mpd, etc.)
```

**Key difference:** OCPMediaPlayer is the **virtual media player** — it doesn't do playback itself. It coordinates backend plugins and emits state to the GUI/MPRIS via bus messages.

---

## Bus Message Changes

All OCP messages now use the `ovos.common_play.*` namespace instead of the old `ocp.audio.*` / `mycroft.audio.service.*` mix.

### Playback Control

| Feature | Old (ovos-audio) | New (ovos-media) |
|---------|------------------|------------------|
| **Start play** | N/A (OCP was special) | `ovos.common_play.play` |
| **Pause** | `ocp.audio.pause` | `ovos.common_play.pause` |
| **Resume** | `ocp.audio.resume` | `ovos.common_play.resume` |
| **Stop** | `ocp.audio.stop` | `ovos.common_play.stop` |
| **Next** | `ocp.audio.next` | `ovos.common_play.next` |
| **Previous** | `ocp.audio.prev` | `ovos.common_play.previous` |

### Playlist Control

| Feature | Old | New |
|---------|-----|-----|
| **Queue track** | N/A | `ovos.common_play.playlist.queue` |
| **Set playlist** | N/A | `ovos.common_play.playlist.set` |
| **Clear playlist** | N/A | `ovos.common_play.playlist.clear` |
| **Shuffle toggle** | `ocp.shuffle` | `ovos.common_play.shuffle.toggle` |
| **Repeat toggle** | `ocp.repeat` | `ovos.common_play.repeat.toggle` |

### Volume & Audio Control

| Feature | Old (ovos-audio) | New (ovos-media) |
|---------|------------------|------------------|
| **Duck (TTS speaking)** | `recognizer_loop:audio_output_start` | `ovos.common_play.duck` (legacy alias supported) |
| **Unduck** | `recognizer_loop:audio_output_end` | `ovos.common_play.unduck` (legacy alias supported) |
| **Cork (mic open)** | `recognizer_loop:record_begin` | `ovos.common_play.cork` (legacy alias supported) |
| **Uncork** | (implicit in `record_end`) | `ovos.common_play.uncork` + auto-uncork on `record_end` |

**Note:** Legacy `recognizer_loop:*` messages are still supported for backward compatibility.

### Status & Info Queries

| Feature | Old | New |
|---------|-----|-----|
| **Get player state** | Listen to `ocp.player.state` | Listen to `ovos.common_play.player.state` |
| **Get media state** | Listen to `ocp.media.state` | Listen to `ovos.common_play.media.state` |
| **Get track info** | Emit `ocp.audio.track_info` | Emit `ovos.common_play.track_info` |
| **Query backends** | `opm.audio.query` (same) | `opm.audio.query` (same) |

### New Features

| Feature | Old | New |
|---------|-----|-----|
| **Like track** | N/A | `ovos.common_play.like` |
| **Unlike track** | N/A | `ovos.common_play.unlike` |
| **Status request** | N/A | `ovos.common_play.status` (full state snapshot) |

---

## Configuration Changes

### Location Change

**Old (ovos-audio):**
```json
{
  "Audio": {
    "backends": {
      "OCP": { ... }
    }
  }
}
```

**New (ovos-media):**
```json
{
  "media": {
    "audio_players": { ... },
    "video_players": { ... }
  }
}
```

### New Configuration Keys

| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `media.audio_players` | dict | `{}` | Audio backend configuration |
| `media.video_players` | dict | `{}` | Video backend configuration |
| `media.web_players` | dict | `{}` | Web (webview) backend configuration |
| `media.preferred_audio_services` | list | `[]` | Preferred audio backends (priority order) |
| `media.preferred_video_services` | list | `[]` | Preferred video backends |
| `media.preferred_web_services` | list | `[]` | Preferred web backends |
| `media.enable_mpris` | bool | `false` | Enable D-Bus MPRIS export |
| `media.manage_external_players` | bool | `false` | Allow MPRIS player control (`playerctl` integration) |
| `media.autoplay` | bool | `true` | Auto-advance to next track on end-of-media |
| `media.merge_search` | bool | `true` | Merge user playlist with search results |

### Example Configuration Migration

**Old (ovos-audio):**
```json
{
  "Audio": {
    "backends": {
      "OCP": {
        "enable_mpris": true
      }
    },
    "default-backend": "vlc"
  }
}
```

**New (ovos-media):**
```json
{
  "media": {
    "enable_mpris": true,
    "audio_players": {
      "vlc": {
        "module": "ovos-audio-plugin-vlc",
        "active": true
      }
    },
    "preferred_audio_services": ["vlc"]
  }
}
```

---

## Feature Comparison

| Feature | Old OCP | ovos-media | Notes |
|---------|---------|-----------|-------|
| Play | ✅ | ✅ | |
| Pause | ✅ | ✅ | |
| Resume | ✅ | ✅ | |
| Stop | ✅ | ✅ | |
| Next | ✅ | ✅ | |
| Previous | ✅ | ✅ | |
| Seek | ✅ | ✅ | |
| Shuffle | ✅ | ✅ | Plus toggle in ovos-media |
| Repeat | ✅ (on/off) | ✅ (none/all/track, plus toggle) | More granular in ovos-media |
| Liked songs | ✅ | ✅ | Explicit `like`/`unlike` messages in ovos-media |
| Volume ducking | ✅ | ✅ | TTS-aware in both |
| Microphone cork | ✅ | ✅ | Explicit `cork`/`uncork` + auto-uncork in ovos-media |
| MPRIS export | ✅ | ✅ | First-class in ovos-media; supports `playerctl` |
| Backend selection | ✅ | ✅ | Runtime preferred service in ovos-media |
| Remote backends | ❌ | ✅ | New in ovos-media |
| Playlist management | ✅ | ✅ | Explicit queue/set/clear in ovos-media |
| VIDEO playback | ✅ | ✅ | Video backend plugins in ovos-media |
| WebView playback | ❌ | ✅ | New in ovos-media |

---

## Step-by-Step Migration

### Phase 1: Prepare ovos-audio (now)

Set configuration flags in `mycroft.conf`:

```json
{
  "enable_old_audioservice": false,
  "disable_ocp": true
}
```

This tells `ovos-audio` to **not** load the legacy audio service or OCP. Only TTS will run through `ovos-audio`.

### Phase 2: Install ovos-media

```bash
pip install ovos-media
# or in the workspace:
uv pip install ovos-media
```

### Phase 3: Configure ovos-media

Add to `mycroft.conf`:

```json
{
  "media": {
    "audio_players": {
      "vlc": {
        "module": "ovos-audio-plugin-vlc",
        "active": true
      }
    },
    "preferred_audio_services": ["vlc"],
    "enable_mpris": true
  }
}
```

Install at least one backend plugin:

```bash
pip install ovos-audio-plugin-vlc
```

### Phase 4: Enable OCP pipeline

Ensure the OCP pipeline plugin is enabled in `mycroft.conf`:

```json
{
  "intents": {
    "pipeline": ["...", "ocp_pipeline_plugin", "..."]
  }
}
```

### Phase 5: Test

Test media commands:
```
"Play jazz"
"Next track"
"Pause"
"Resume"
"What song is this?"
```

### Phase 6: Verify MPRIS (if enabled)

```bash
playerctl status
playerctl play-pause
playerctl next
```

### Phase 7: Monitor

```bash
journalctl -u ovos-media -f
```

Watch for backend loading and state transitions.

---

## Troubleshooting

### No audio backends loaded

**Solution:**
```bash
pip install ovos-audio-plugin-vlc
```

Update config:
```json
{
  "media": {
    "audio_players": {
      "vlc": {
        "module": "ovos-audio-plugin-vlc",
        "active": true
      }
    }
  }
}
```

### Audio doesn't play

**Check logs:**
```bash
journalctl -u ovos-media -f
```

**Test backend:**
```bash
playerctl play-pause
```

### MPRIS not working

**Enable it:**
```json
{
  "media": {
    "enable_mpris": true
  }
}
```

**Test:**
```bash
playerctl status
```

---

## Cleanup: What Happens to ovos-audio

Once fully migrated, these can be removed from `ovos-audio` in future releases:

1. `AudioService.find_ocp()` — hardcoded OCP loader
2. `self.ocp` field — the `OCPAudioBackend` instance
3. OCP exclusion in `load_services()` — no longer needed
4. Deprecated methods: `handle_opm_audio_query()`, `get_audio_options()`

**Timeline:**
- **Now (0.1.x):** Warn if OCP is still loaded; recommend `disable_ocp: true`
- **0.2.0:** Default `disable_ocp: true` (breaking)
- **1.0.0:** Remove OCP code (breaking)

---

## See Also

- [Architecture](architecture.md) — Deep dive into layers and bus events
- [Configuration Reference](configuration.md) — All config keys explained
- [Backend Plugins](backends.md) — Writing custom backends
- [MPRIS Integration](mpris.md) — D-Bus and `playerctl` usage
- [OCP Skills](ocp-skills.md) — Implementing OCP-compatible skills
- [06-ovos-audio-migration.md](06-ovos-audio-migration.md) — Legacy ovos-audio details

---

## FAQ

**Q: Can I run ovos-audio and ovos-media simultaneously?**

A: Yes. Keep `ovos-audio` for TTS with `disable_ocp: true`. `ovos-media` handles all media playback.

**Q: Can I contribute?**

A: Yes! Current coverage is **83%**. PRs that maintain coverage are welcome.

**Q: What's the long-term vision?**

A: `ovos-audio` → pure TTS service. `ovos-media` → all media playback. This is already the design intent.


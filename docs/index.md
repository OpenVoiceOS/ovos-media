# `ovos-media` — Documentation

`ovos-media` is the OCP-native audio, video, and web media service for OpenVoiceOS.
It handles all media playback and replaces the legacy media handling inside `ovos-audio`,
while `ovos-audio` continues to run for TTS output.

**Version**: 0.0.1 | **License**: Apache-2.0 | **Python**: >=3.10

---

## User Guides

| Document | Description |
|---|---|
| [Migration Guide](MIGRATION_GUIDE.md) | **What changed from old OCP** — architecture, bus messages, config, features, step-by-step migration |
| [Getting Started](getting-started.md) | Install, run, configure ovos-media |
| [Configuration](configuration.md) | Full `mycroft.conf` reference for the `media` section |
| [Architecture](architecture.md) | Service layers, bus events, state machine, GUI integration |
| [Backend Plugins](backends.md) | Audio/video/web backends, plugin discovery, writing a custom backend |
| [MPRIS Integration](mpris.md) | D-Bus MPRIS support, external player control, `playerctl` usage |
| [OCP Skills](ocp-skills.md) | OCP query flow, MediaEntry structure, testing with ovoscope |

---

## Reference

| Document | Description |
|---|---|
| [`../QUICK_FACTS.md`](../QUICK_FACTS.md) | Key classes, entry points, config keys, coverage table |
| [`../FAQ.md`](../FAQ.md) | Common questions and answers |
| [`../AUDIT.md`](../AUDIT.md) | Known issues, open bugs, security notes |
| [`../MAINTENANCE_REPORT.md`](../MAINTENANCE_REPORT.md) | Date-stamped change log |
| [`../SUGGESTIONS.md`](../SUGGESTIONS.md) | Proposed improvements |
| [`../CHANGELOG.md`](../CHANGELOG.md) | Release changelog |

---

## Background Reading

The following documents cover the history, design decisions, and integration context:

| Document | Description |
|---|---|
| [History and Architecture](01-history-and-architecture.md) | Origins from Mycroft, evolution to OCP |
| [Current State](02-current-state.md) | Component inventory, what exists where |
| [Music Assistant Integration](03-music-assistant-integration.md) | Integration with the Music Assistant server |
| [OCP Pipeline](05-ocp-pipeline.md) | How the OCP intent pipeline works |
| [Migration from ovos-audio](06-ovos-audio-migration.md) | Detailed migration guide and feature comparison |
| [OCP Pipeline ML Classifier](07-ocp-pipeline-ml-classifier.md) | ML-based media type classification |
| [GUI Decoupling Plan](08-gui-decoupling-plan.md) | How GUI rendering was decoupled from the service |

---

## Architecture in Brief

```
User utterance
  ↓
ovos-ocp-pipeline-plugin  (classifies as media query)
  ↓
OCP Skills                (search and return MediaEntry lists)
  ↓
MediaService              (ovos_media/service.py)
  ↓
OCPMediaPlayer            (ovos_media/player.py)  ←→  GUIInterface.show_media_player()
  ↓                ↓                  ↓
AudioService   VideoService      WebService        (ovos_media/media_backends/)
  ↓
OPM backend plugins       (e.g. ovos-audio-plugin-vlc)
```

`MediaService` — `ovos_media/service.py:33` — owns the bus connection and `OCPMediaPlayer`.
`OCPMediaPlayer` — `ovos_media/player.py` — manages the playback state machine and dispatches to backends.
`OcpMprisExporter` — `ovos_media/mpris.py:74` — runs in a background thread to expose OCP over D-Bus.

---

## Quick Config Example

```json
{
  "enable_old_audioservice": false,
  "disable_ocp": true,
  "media": {
    "preferred_audio_services": ["vlc"],
    "enable_mpris": true,
    "manage_external_players": false
  }
}
```

Full reference: [configuration.md](configuration.md).

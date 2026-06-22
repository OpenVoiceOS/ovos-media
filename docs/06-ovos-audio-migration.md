# ovos-audio and the Migration Path

## What ovos-audio Actually Is

`ovos-audio` is the TTS + audio playback service for OVOS. Despite its name, its primary job today is **TTS** (text-to-speech synthesis and playback). Media playback (OCP) has been growing out of it for a long time and is now being split off into `ovos-media`.

`ovos-audio` runs as `PlaybackService` (a thread), which owns:
- The active TTS engine
- A fallback TTS engine
- A `PlaybackThread` (for queuing TTS utterances)
- A `DialogTransformersService`
- An optional `AudioService` (the legacy media backend loader — only active when `enable_old_audioservice: true`)

## How OCP Gets Special Treatment in ovos-audio

The critical thing to understand: **`ovos-ocp-audio-plugin` is NOT loaded through the normal audio plugin discovery path.** It is hardcoded.

In `ovos_audio/audio.py`, `AudioService.load_services()` does this:

```python
found_plugins = find_audio_service_plugins()
if 'ovos_common_play' in found_plugins:
    found_plugins.pop('ovos_common_play')   # <-- explicitly excluded
```

OCP is then loaded separately via `find_ocp()`:

```python
def find_ocp(self):
    if self.disable_ocp:
        return
    from ovos_plugin_common_play import OCPAudioBackend   # hardcoded import
    ocp_config = Configuration().get("Audio", {}).get("backends", {}).get("OCP", {})
    self.ocp = OCPAudioBackend(ocp_config, bus=self.bus)
```

The result: `self.ocp` is separate from `self.service` (the list of normal backends). OCP is a special singleton object that owns its own player state machine, GUI, MPRIS, and search logic. No other `AudioBackend` does this — they are thin wrappers around playback tools.

This means:
- Normal audio backends (`vlc`, `mpd`, `simple`, `mass`, etc.) are in `self.service` — they handle `mycroft.audio.service.play` for simple URI playback
- OCP (`self.ocp`) handles media queries routed through the OCP bus protocol — it is the voice-controlled media player
- The two are parallel, not stacked. OCP internally uses `ClassicAudioServiceInterface` to delegate raw URI playback to the normal backends when needed

## The Two Config Flags

### `enable_old_audioservice` (default: `True`)

Controls whether `PlaybackService` creates an `AudioService` at all. When `False`, no audio backends are loaded and OCP is not initialized through this path. This is the flag to set when migrating to `ovos-media`.

```json
{ "enable_old_audioservice": false }
```

**TODO in code:** The comment says `# TODO default to False soon` — this needs to actually happen.

### `disable_ocp` (default: `False`)

Controls whether `AudioService.find_ocp()` loads `ovos-ocp-audio-plugin` even when the old audio service is enabled. When `True`, OCP is skipped and the system falls back to using raw `mycroft.audio.service.*` for media playback (no voice-controlled media player at all).

```json
{ "disable_ocp": true }
```

**TODO in code:** The comment says `# TODO default to True soon` — this should happen when `ovos-media` is stable enough to be the default.

There is a warning in `ovos_audio/service.py` that is already emitted:
```
"OCP has moved to ovos-media, if you already migrated to ovos-media set 'disable_ocp': true in mycroft.conf"
```

## Deprecation Status in ovos-audio

Several methods in `PlaybackService` are already decorated `@deprecated("audio service moved to ovos-media", "0.1.0")`:
- `get_audio_options()`
- `handle_opm_audio_query()`

The `opm.audio.query` handler returns empty data, meaning the audio plugin query API is intentionally broken in preparation for removal.

## What Needs to Happen in ovos-audio for Full Migration

### 1. Flip the defaults

```python
# Current
self.audio_enabled = self.config.get("enable_old_audioservice", True)  # TODO default to False soon
disable_ocp = self.config.get("disable_ocp", False)                     # TODO default to True soon
```

These TODO comments have been there a while. The defaults need to flip once `ovos-media` is the recommended path:
```python
self.audio_enabled = self.config.get("enable_old_audioservice", False)
disable_ocp = self.config.get("disable_ocp", True)
```

This is a breaking change — needs a deprecation cycle (warn loudly before flipping).

### 2. Remove find_ocp() and the hardcoded OCP path

Once `enable_old_audioservice` defaults to `False`:
- `AudioService.find_ocp()` becomes dead code
- The `self.ocp` field and all references to it can be removed
- The `ovos_common_play` exclusion from `load_services()` becomes irrelevant
- `AudioService` itself becomes a thin helper or can be removed entirely

### 3. Remove the deprecated handlers

`handle_opm_audio_query()` and `get_audio_options()` are already broken (return empty). They should be removed once there's no expectation of backward compat.

### 4. Consider removing AudioService entirely

Once OCP is gone from `ovos-audio`, the remaining `AudioService` only handles raw `mycroft.audio.service.play` — which is the TTS system speaking sound files (earcons, etc.), NOT media playback. That functionality may be better folded into `PlaybackService` directly or into `PlaybackThread`.

The `mycroft.audio.service.play` messages for sound effects (not media) are a separate concern from the OCP media pipeline. Clarifying this boundary is part of the cleanup.

## Migration Config Summary

### Full migration to ovos-media (recommended)

```json
{
  "enable_old_audioservice": false,
  "intents": {
    "pipeline": ["...", "ocp_pipeline_plugin", "..."]
  },
  "media": {
    "preferred_audio_services": ["vlc", "mplayer", "cli"],
    "audio_players": {
      "vlc": { "module": "ovos-media-audio-plugin-vlc", "active": true }
    }
  }
}
```

### Transitional: keep old OCP but suppress warning

```json
{
  "enable_old_audioservice": true,
  "disable_ocp": false
}
```

### Transitional: old audio service without OCP (raw URI playback only)

```json
{
  "enable_old_audioservice": true,
  "disable_ocp": true
}
```

## Bus Interface Difference

When the system runs `ovos-media` instead of `ovos-ocp-audio-plugin`, the bus message surface changes:

| Old (ocp-audio-plugin) | New (ovos-media) |
|------------------------|-----------------|
| `mycroft.audio.service.play` | `ovos.common_play.play` |
| `mycroft.audio.service.pause` | `ovos.common_play.pause` |
| `mycroft.audio.service.stop` | `ovos.common_play.stop` |
| `mycroft.audio.service.next` | `ovos.common_play.next` |
| `mycroft.audio.service.prev` | `ovos.common_play.prev` |

The OCP pipeline plugin already emits `ovos.common_play.*`. The `ClassicAudioServiceInterface` bridges from `ovos.common_play.*` → `mycroft.audio.service.*` for backward compat with the old plugin. When the old plugin is gone, this bridge can be removed from the pipeline plugin too.

## Relationship Between ovos-audio and ovos-media Long-Term

`ovos-audio` should eventually become a pure TTS service:
- Handles `speak` messages → TTS synthesis → audio playback via `PlaybackThread`
- Handles queued sound effects (`mycroft.audio.queue`, `mycroft.audio.play_sound`)
- Does NOT handle media playback — that is `ovos-media`'s job

`ovos-media` handles everything under the `ovos.common_play.*` namespace and `ocp_pipeline` routing.

This split is already the design intent. The current confusion is that `ovos-audio` still carries the legacy code to host the old monolith.

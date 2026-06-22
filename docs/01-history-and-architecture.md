# History and Architecture

## Origins: The Mycroft Audio Plugin Hack

Mycroft AI had a plugin system for audio backends (`mycroft-audio`). It was designed for simple backends like MPD or VLC — not for a full-featured voice-driven media player.

OCP (OVOS Common Play) was originally developed as a contribution to Mycroft, but the PRs were rejected. Rather than fork the entire Mycroft audio stack, OCP was shoehorned into the existing `AudioBackend` plugin interface — a severe abuse of a system that expected a thin wrapper around a playback binary.

The result was `ovos-ocp-audio-plugin` (`ovos_plugin_common_play`), which crammed all of the following into a single mycroft audio backend:

- Voice intent handling (NLP, utterance classification for media queries)
- Search orchestration (fanning out to OCP skills, scoring, deduplication)
- A full player state machine (play/pause/stop/next/prev/seek, playlist management, shuffle/repeat)
- MPRIS integration (controlling and being controlled by external players via D-Bus)
- GUI rendering (pushing state and pages to mycroft-gui / Qt)
- "Liked songs" / favorites persistence
- Stream extraction plugin loading

This worked, but created a tightly coupled monolith. The GUI in particular became a serious problem: because OCP ran as an `OVOSAbstractApplication` (a skill-like entity that owns a GUI namespace), the Qt5 media player UI was tightly bound to the OCP player state machine. Every GUI page was a QML file shipped inside the plugin.

## The GUI Problem

Mycroft/OVOS used `mycroft-gui` as the display layer. Skills push page names and data to the GUI service, which renders QML files found on the device. OCP took this to an extreme: the entire media player UI — now playing, search results, playlists, disambiguation — was implemented as QML pages pushed from within the audio plugin.

This meant:
- GUI state was driven by player state changes emitted inside the monolith
- The GUI and the player were logically inseparable
- Remote or headless deployments had to carry all the QML assets anyway
- Adding alternative renderers (web, kiosk display, etc.) was impossible without forking

Multiple attempts were made to decouple the GUI. Each time, hacks were layered on rather than replaced, because the untangling was never taken far enough.

## Extraction Phase: ocp-pipeline-plugin

Eventually, the NLP portion of OCP was extracted into a standalone pipeline plugin: `ovos-ocp-pipeline-plugin` (`ocp_pipeline`).

This plugin integrates with `ovos-core`'s intent pipeline and handles:
- Media query classification (is this utterance asking to play music? what media type?)
- Skill search dispatch (collecting results from registered OCP skills via bus)
- Result scoring and selection
- Routing the selected result to the player for playback

OCP skills themselves were already clean — they only implement `@ocp_search()` and `@ocp_featured_media()` handlers and yield `MediaEntry` / `Playlist` objects. No intents, no playback logic. The pipeline plugin was the missing separation layer.

## The Special-Case Loading in ovos-audio

Because OCP was never a normal audio backend, `ovos-audio` could not just load it through standard plugin discovery. The code in `ovos_audio/audio.py` explicitly blacklists `ovos_common_play` from the plugin scan:

```python
found_plugins = find_audio_service_plugins()
if 'ovos_common_play' in found_plugins:
    found_plugins.pop('ovos_common_play')  # handled separately
```

Then `find_ocp()` does a hardcoded import of `OCPAudioBackend` and instantiates it as `self.ocp` — a singleton separate from `self.service` (the list of regular backends). This is why removing OCP requires changes inside `ovos-audio`, not just uninstalling a package.

Two config flags control this:
- `enable_old_audioservice` (default: `True`) — whether `AudioService` is created at all
- `disable_ocp` (default: `False`) — whether `find_ocp()` runs even when the old service is enabled

Both have `# TODO default to ... soon` comments in the code. Flipping them is the migration trigger.

## ovos-media: The New Audio Daemon

`ovos-media` (`ovos-media` pip package) is the intended replacement for the audio-plugin approach. It is a standalone service (not a mycroft-audio plugin) that:

- Runs as its own process (`ovos-media` entry point)
- Loads `MediaBackend` plugins for audio, video, and web playback
- Manages the player state machine
- Handles MPRIS
- Handles GUI state (still coupled, see below)

Plugin discovery uses `ovos-plugin-manager`. Backends are configured in `mycroft.conf` under the `"media"` key, with separate lists for `audio_players`, `video_players`, and `web_players`. Each backend specifies its plugin module, user-facing aliases, and active/inactive status.

## What Was Not Cleaned Up

Despite the extraction of NLP into `ocp-pipeline-plugin` and the player into `ovos-media`, several problems remain:

### 1. ovos-ocp-audio-plugin Still Exists and Is Deployed

Many users still run the old plugin. It is not yet deprecated/archived. It still carries all the old GUI coupling, the old NLP integration (now duplicated by the pipeline plugin), and the old player state machine (now duplicated by `ovos-media`).

### 2. GUI Is Still Coupled in ovos-media

`ovos-media` still ships Qt5 QML files (`ovos_media/qt5/`) and drives the GUI from within the player. The GUI refactor (adapter plugin system — `opm.gui_adapter` entry point) is the intended fix, but `ovos-media` has not yet been updated to use it. The `ovos_media/gui.py` module still directly manipulates GUI state in the mycroft-gui style.

### 3. OCPMediaCatalog is a skill inside ovos-media

`ovos_media/player.py` contains `OCPMediaCatalog`, which inherits from `OVOSCommonPlaybackSkill`. This means `ovos-media` still registers as a skill (for liked songs and featured media), coupling the daemon to the skill framework.

### 4. The URI scheme for Music Assistant

MA uses a `library://` URI scheme for internal library items (tracks, albums, artists). These URIs are not playable by generic media backends — they must be resolved by Music Assistant itself. This creates an implicit dependency: if OCP pipeline selects a MA skill result, the MA media plugin MUST be present to handle it. Generic backends will fail silently or error.

## Architecture Timeline

```
[Mycroft era]
  mycroft-audio
    └── ovos_common_play (AudioBackend hack)
          ├── NLP / intent handling
          ├── Search dispatch
          ├── Player state machine
          ├── MPRIS
          └── GUI (Qt5 QML, tightly coupled)

[Extraction era]
  ovos-core intent pipeline
    └── ocp-pipeline-plugin
          ├── NLP / media query classification
          └── Search dispatch -> OCP skills -> results -> ovos-ocp-audio-plugin player

  ovos-audio (mycroft-audio successor)
    └── ovos-ocp-audio-plugin (still alive, increasingly legacy)
          ├── Player state machine
          ├── MPRIS
          └── GUI (still coupled)

[Current / target]
  ovos-core intent pipeline
    └── ocp-pipeline-plugin
          └── Search dispatch -> OCP skills -> results -> ovos-media

  ovos-media (standalone daemon)
    ├── Player state machine
    ├── MPRIS
    ├── Media backend plugins (ovos-media-audio-plugin-*, etc.)
    └── GUI (still coupled — target: GUI adapter plugins)
```

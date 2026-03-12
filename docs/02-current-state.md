# Current State

## Component Inventory

### ovos-ocp-audio-plugin (LEGACY)

**Repo:** `ovos-ocp-audio-plugin`
**Package:** `ovos-plugin-common-play`
**Status:** Functional but legacy; targeted for deprecation

The original monolith. Contains:
- `OCPMediaPlayer` — full player state machine as an `OVOSAbstractApplication`
- `OCPMediaPlayerGUI` — Qt5 GUI integration (pushes QML pages to mycroft-gui)
- `OCPSearch` — search fanout and result scoring
- `NowPlaying` — current track metadata management
- `MprisPlayerCtl` — MPRIS integration
- Loads as a mycroft-audio `AudioBackend` (type `ovos_common_play`)

Still used by anyone running `ovos-audio` with `"enable_old_audioservice": true` (the default for older setups).

Configured under `mycroft.conf` as:
```json
{
  "Audio": {
    "backends": {
      "local": { "type": "ovos_common_play", "active": true }
    }
  }
}
```

### ovos-ocp-pipeline-plugin

**Repo:** `ovos-ocp-pipeline-plugin`
**Package:** `ocp-pipeline`
**Status:** Active, stable

The NLP brain. Integrates with `ovos-core`'s intent pipeline. Does NOT handle playback.

Responsibilities:
- Classifies utterances as media queries (using trained classifiers)
- Determines media type (music, podcast, radio, video, audiobook, etc.)
- Dispatches search to registered OCP skills via bus messages
- Collects, scores, and selects the best result
- Emits the selected result to the active player (`ovos-media` or `ovos-ocp-audio-plugin`)

OCP skills register with this pipeline by inheriting `OVOSCommonPlaybackSkill` and implementing `@ocp_search()` handlers. The skill does not need to handle playback — it only returns `MediaEntry` / `Playlist` objects.

### ovos-media

**Repo:** `ovos-media`
**Package:** `ovos-media`
**Status:** Beta / proof of concept

The new standalone audio daemon. Replaces `ovos-ocp-audio-plugin` as the player.

Entry point: `ovos-media` (runs `ovos_media.__main__:main`)

Key modules:
- `ovos_media/player.py` — `OCPMediaCatalog` (the application, inherits `OVOSCommonPlaybackSkill`), drives the full player state machine; manages playlists, track history, liked songs
- `ovos_media/media_backends/` — `AudioService`, `VideoService`, `WebService` — each manages a set of typed backend plugins
- `ovos_media/gui.py` — `OCPGUIInterface`, `OCPGUIState` — pushes GUI state to mycroft-gui (still Qt5 coupled)
- `ovos_media/mpris.py` — MPRIS integration

Configured under `mycroft.conf`:
```json
{
  "media": {
    "enable_mpris": false,
    "preferred_audio_services": ["gui", "vlc", "mplayer", "cli"],
    "preferred_video_services": ["gui", "vlc"],
    "preferred_web_services": ["gui", "browser"],
    "audio_players": { ... },
    "video_players": { ... },
    "web_players": { ... }
  }
}
```

To use `ovos-media` instead of the old plugin:
```json
{ "enable_old_audioservice": false }
```

### Available Media Backend Plugins

| Package | Type | Description |
|---------|------|-------------|
| `ovos-media-plugin-vlc` | audio + video | Headless VLC instance |
| `ovos-media-plugin-mplayer` | audio | mplayer |
| `ovos-media-plugin-mpv` | audio | mpv |
| `ovos-media-plugin-ffplay` | audio + video | ffplay |
| `ovos-media-plugin-simple` | audio | Simple fallback |
| `ovos-media-plugin-chromecast` | audio + video | Chromecast via pychromecast |
| `ovos-media-plugin-spotify` | audio | Spotify Connect |
| `ovos-media-plugin-mass` | audio | Music Assistant (see below) |

GUI-based backends (audio/video/web) are built into the old plugin but not yet cleanly separated in `ovos-media`.

### OCP Skills

OCP skills are standalone packages that provide media search. They inherit `OVOSCommonPlaybackSkill` and implement:
- `@ocp_search()` — given a phrase and media type, yield `MediaEntry` / `Playlist` objects
- `@ocp_featured_media()` — optional; return a playlist shown in the OCP browse UI

Skills do not handle playback. They do not have intents for play/pause/stop. They are purely search providers.

Examples in this workspace:
- `ovos-ocp-youtube-plugin` — YouTube search
- `ovos-ocp-bandcamp-plugin` — Bandcamp
- `ovos-skill-local-media` — local files
- `ovos-skill-mass` — Music Assistant (see next doc)

### Stream Extractor Plugins

OCP also supports stream extractor plugins (`opm.ocp` entry point group). These transform non-playable URIs (YouTube URLs, playlist files, etc.) into playable streams before handing them to the media backend. Examples:
- `ovos-ocp-youtube-plugin` — extracts audio stream from YouTube URLs
- `ovos-ocp-m3u-plugin` — parses M3U playlists
- `ovos-ocp-rss-plugin` — parses podcast RSS feeds

## Known Coupling Issues

### GUI still Qt5/mycroft-gui bound

`ovos_media/gui.py` pushes page names + data to `mycroft-gui` exactly as the old plugin did. The QML assets live in `ovos_media/qt5/`. This means:
- `ovos-media` still depends on `mycroft-gui` being connected for the player UI
- No alternative renderers (web UI, kiosk, headless) without replacing `gui.py`
- The GUI adapter plugin system (being developed in `ovos-gui`) is the planned fix

### OCPMediaCatalog is a skill

`ovos_media/player.py` inherits from `OVOSCommonPlaybackSkill`. This registers `ovos-media` as a skill on the bus and loads skill infrastructure (settings, locale, etc.). It also registers `@ocp_search()` for liked songs and `@ocp_featured_media()` for the browse view.

This is a pragmatic reuse of the skill framework but creates a conceptual oddity: the player daemon is also a skill. If/when liked songs and browse are extracted, the dependency could be severed.

### No next/prev support in some backends

The MA audio backend (`MAssAudioService`) does not implement `next()` or `previous()` in the `AudioBackend` (legacy) interface. It supports them via the `MAssBaseService` using the MA queue API, but the old `load_service()` pattern wrapping it in `MAssAudioService` loses that capability.

### Hardcoded MA URL in skill

`ovos-skill-mass/__init__.py` has a hardcoded `url = "http://100.88.41.41:8095"` for development — must be removed before any release.

# Music Assistant Integration

[Music Assistant](https://music-assistant.io) (MA) is a self-hosted music library server that aggregates sources (local files, Spotify, Tidal, etc.) and controls network players (DLNA, Snapcast, Chromecast, etc.).

OVOS integrates with MA via two companion packages:

- **`ovos-skill-music-assistant`** (`ovos-skill-mass`) — OCP skill: voice search against the MA library
- **`ovos-media-plugin-mass`** — media backend plugin: delegates playback to a specific MA player

## How It Fits Together

```
User: "play Viagra Boys on the living room speaker"
        |
        v
[ocp-pipeline-plugin]
  - classifies as MediaType.MUSIC
  - dispatches @ocp_search to all registered OCP skills
        |
        v
[ovos-skill-mass]
  - calls MA HTTP API: music/search "Viagra Boys"
  - scores and yields MediaEntry objects
  - URI format: library://track/9903  (MA internal URI)
        |
        v
[ocp-pipeline-plugin]
  - selects best result
  - routes to ovos-media player
        |
        v
[ovos-media / OCPMediaCatalog]
  - loads the selected MediaEntry
  - picks audio backend by URI scheme ("library://")
        |
        v
[ovos-media-plugin-mass / MAssOCPAudioService]
  - calls MA HTTP API: player_queues/play_media
  - MA resolves library:// URI and streams to the configured player
  - handles pause/resume/stop/seek/volume via MA queue/player commands
```

## ovos-skill-music-assistant

**Repo:** `TigreGotico/ovos-skill-music-assistant` (checked out as `ovos-skill-mass`)
**Class:** `MusicAssistantSkill(OVOSCommonPlaybackSkill)`

Key points:
- No intents — purely an OCP search provider
- Talks to MA via `SimpleHTTPMusicAssistantClient` (HTTP, not WebSocket)
- Searches: tracks, albums, artists, radio, podcasts, audiobooks
- Scoring uses `fuzzy_match` on name/artist + bonuses for favorites, explicit media type, and vocabulary matches (`"play ... on music assistant"`)
- `@ocp_featured_media()` returns the MA "recently played" list as a Playlist

Configuration in `settings.json` (skill settings):
```json
{ "url": "http://your-mass-server:8095" }
```

**Known issue:** `url` is currently hardcoded as `"http://100.88.41.41:8095"` in the `api` property. Must be fixed before any release.

### URI Format

MA returns `library://` URIs for all library items:
- `library://track/9903`
- `library://album/1303`
- `library://artist/42`

These URIs are opaque to all other media backends. Only the MA media plugin knows how to play them.

## ovos-media-plugin-mass

**Repo:** `TigreGotico/ovos-media-plugin-mass`
**Classes:**
- `MAssBaseService(MediaBackend)` — core MA playback logic
- `MAssOCPAudioService(RemoteAudioPlayerBackend, MAssBaseService)` — for `ovos-media`
- `MAssAudioService(AudioBackend)` — wrapper for legacy `ovos-audio` / `ovos-ocp-audio-plugin`

Each configured MA player shows up as a separate backend in `mycroft.conf`. The backend is selected by `ovos-media` based on URI support — `MAssBaseService.supported_uris()` returns `["library"]` when the player is available.

### Configuration

Each MA speaker is configured as a named entry:
```json
{
  "media": {
    "audio_players": {
      "mass-HomeLabRenderer:dlna": {
        "module": "ovos-media-audio-plugin-mass",
        "identifier": "uuid:4b778a71-0499-485a-a5a4-88140603fba9",
        "url": "http://your-mass-server:8095",
        "player_type": "dlna",
        "aliases": ["HomeLabRenderer", "Home Lab Renderer"],
        "active": true
      }
    }
  }
}
```

The `ovos-mass-autoconfigure` script automates this by scanning MA for available players and writing both the `ovos-media` and legacy `ovos-audio` config blocks.

### Player Availability

`MAssBaseService` polls `api.get_player_state(player_id)` and stores the result in `self.player_state`. `supported_uris()` returns `[]` if `player_state["available"]` is false, which causes `ovos-media` to skip this backend and try the next preferred one.

**Issue:** The availability poll in `handle_player_ping` calls `threading.Event().wait(20)` which blocks for 20 seconds unconditionally on every ping. This is a bug — it should use a timed wait on a real event or just sleep, not block a threading.Event indefinitely.

### Playback Commands

All playback is delegated to MA via HTTP:

| Action | MA API call |
|--------|------------|
| play | `player_queues/play_media` (with `library://` URI) |
| pause | `player_queues/play_pause` |
| resume | `player_queues/play` |
| stop | `players/cmd/stop` |
| seek | `players/cmd/seek` |
| volume up/down | `players/cmd/volume_up` / `volume_down` |
| next | `player_queues/next` |
| prev | `player_queues/previous` |

### Timestamp Tracking

MA does not push real-time position callbacks. `PlaybackTimestampTracker` approximates the current position by accumulating wall-clock time since play started, pausing the accumulation on pause/stop. Used for `get_track_position()` and end-of-track detection.

### Legacy Wrapper

`MAssAudioService(AudioBackend)` wraps `MAssOCPAudioService` for use with the old `ovos-audio` / `ovos-ocp-audio-plugin` stack. It uses the `load_service()` pattern (type: `"ovos_mass"` or `"mass"` in `Audio.backends`).

**Known limitation:** The legacy `AudioBackend` interface does not have `next()`/`previous()` and `MAssAudioService` just logs an error for them. The newer `MAssOCPAudioService` (for `ovos-media`) does support these via the MA queue API.

## Related: hivemind-homeassistant

A companion project (`JarbasHiveMind/hivemind-homeassistant`) makes OVOS appear as a player in Home Assistant's media player entity list. This is the inverse direction — rather than OVOS controlling MA, HA controls OVOS.

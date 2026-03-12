# OCP Pipeline Plugin

**Repo:** `ovos-ocp-pipeline-plugin`
**Package:** `ocp-pipeline`
**Entry point group:** `opm.pipeline`
**Class:** `OCPPipelinePlugin(ConfidenceMatcherPipeline, PipelinePlugin)`

The OCP pipeline plugin is the NLP brain of the media stack. It integrates with `ovos-core`'s intent pipeline, handles all media query classification, and dispatches search to OCP skills. It does NOT handle playback — it only selects what to play, then tells the active player.

## What It Does

When `ovos-core` receives an utterance, it runs it through the intent pipeline. OCP is one stage in that pipeline. If OCP scores the utterance as a media query above the pipeline threshold, OCP takes over:

1. **Classification** — determine the media type (music, podcast, radio, video, audiobook, news, etc.) using a trained `AhocorasickNER` classifier and vocabulary files. The classifiers live in `ocp_pipeline/res/`.

2. **Search dispatch** — emit `ovos.common_play.query` on the bus. All registered OCP skills respond with `ovos.common_play.query.response`, providing `MediaEntry`/`Playlist` objects scored 0–100.

3. **Result selection** — collect responses up to a timeout, sort by score, pick the best result.

4. **Playback routing** — emit the selected result to the active player:
   - For `ovos-media`: emits `ovos.common_play.play` with the `MediaEntry`/`Playlist`
   - For legacy `ovos-ocp-audio-plugin`: uses `ClassicAudioServiceInterface`

## Per-Session Player State Tracking

The pipeline plugin does NOT assume a single global player. It tracks one `OCPPlayerProxy` per session:

```python
@dataclass
class OCPPlayerProxy:
    session_id: str
    available_extractors: List[str]
    ocp_available: bool
    player_state: PlayerState = PlayerState.STOPPED
    media_state: MediaState = MediaState.UNKNOWN
    media_type: MediaType = MediaType.GENERIC
    skill_id: Optional[str] = None
```

This matters for HiveMind: each satellite has its own session. A user on a satellite device plays media on THAT device's player, not the main node's player. The pipeline plugin keeps track of which player belongs to which session.

## OCP Skills: How They Register

OCP skills inherit `OVOSCommonPlaybackSkill` (from `ovos-workshop`). When a skill loads, it registers itself on the bus by emitting `ovos.common_play.announce`. The pipeline plugin tracks which skills are available.

Skills implement:
- `@ocp_search()` — called during search dispatch; receives `(phrase, media_type)`, yields `MediaEntry` or `Playlist` objects
- `@ocp_featured_media()` — optional; returns a playlist for the OCP home/browse screen

Skills must NOT handle playback. They must NOT have intents for play/pause/stop/next. They are purely search providers / media catalogs.

## Bus Messages

### Inbound (from core / skills / player)

| Message | Description |
|---------|-------------|
| `ovos.common_play.query.response` | OCP skill returning search results |
| `ovos.common_play.skills.detach` | OCP skill unloaded |
| `ovos.common_play.announce` | OCP skill announcing presence |
| `ovos.common_play.player.state` | Player state update (from `ovos-media`) |
| `ovos.common_play.media.state` | Media state update |
| `ovos.common_play.track.state` | Track state update |

### Outbound (to skills / player)

| Message | Description |
|---------|-------------|
| `ovos.common_play.query` | Dispatch search to all OCP skills |
| `ovos.common_play.play` | Tell player to play selected result |
| `ovos.common_play.pause` | Pause (forwarded from voice command) |
| `ovos.common_play.resume` | Resume |
| `ovos.common_play.stop` | Stop |
| `ovos.common_play.next` | Next track |
| `ovos.common_play.prev` | Previous track |

## Legacy Bridges

The pipeline plugin contains two backward-compatibility layers:

### LegacyCommonPlay

Bridges to the ancient Mycroft `CommonPlaySkill` interface (pre-OCP):
- Emits `play:query` instead of `ovos.common_play.query`
- Collects `play:query.response` from old-style skills
- Emits `play:start` to tell the winning skill to handle playback itself

This is for skills that still use the old Mycroft `CommonPlaySkill` base class (not `OVOSCommonPlaybackSkill`). It is marked for removal in `ovos-core 0.1.0`.

### ClassicAudioServiceInterface

When `ovos-media` is not running (i.e., the system still uses `ovos-ocp-audio-plugin` inside `ovos-audio`), the pipeline plugin can fall back to emitting `mycroft.audio.service.*` bus messages to control the classic audio service. Managed via `ovos_bus_client.apis.ocp.ClassicAudioServiceInterface`.

## Classifiers and Language Support

Classification relies on:
- `AhocorasickNER` from `ahocorasick_ner` — fast string matching for media keywords
- Vocabulary files per language in `ocp_pipeline/locale/`
- Translations in `ocp_pipeline/translations/`

The classifier identifies phrases like "play some jazz", "put on a podcast", "show me a video of" and extracts the media type and search phrase. It also handles player aliases (e.g. "play on vlc", "play on the living room speaker").

## Configuration

The pipeline plugin is enabled by including it in the `ovos-core` pipeline config:

```json
{
  "intents": {
    "pipeline": [
      "...",
      "ocp_pipeline_plugin",
      "..."
    ]
  }
}
```

OCP-specific settings (search timeout, confidence thresholds) are configurable under `mycroft.conf`. Refer to the plugin's README for current config keys.

## Relationship to ovos-media

The pipeline plugin is player-agnostic in principle — it emits bus messages and any conforming player can handle them. In practice:

- `ovos-media` is the new target player — it listens on `ovos.common_play.*`
- `ovos-ocp-audio-plugin` also listens on `ovos.common_play.*` (legacy)
- Only one should be active at a time

The pipeline plugin detects whether `ovos-media` or the legacy player is active by checking `ocp_available` in the session's `OCPPlayerProxy`. This flag is set when the player announces itself on the bus.

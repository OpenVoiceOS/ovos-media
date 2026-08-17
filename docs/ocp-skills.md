# OCP Skills and the Media Pipeline

There are **two kinds** of OCP skill, and only one of them is deprecated:

| Kind | Base class | Status |
|------|-----------|--------|
| **Search skill**, returns catalog results for *"play X"* | `OVOSCommonPlaybackSkill` + `@ocp_search` | ⛔ **deprecated** → write a [MediaProvider](media-providers.md) instead |
| **Game / interactive skill**, *is* the experience (a game, quiz, interactive story) | `OVOSGameSkill` / `ConversationalGameSkill` | ✅ **fully supported**, stays a skill; see [Game & interactive skills](#game--interactive-skills) |

> **Why the split?** A *search* skill is a catalog: it just answers "where can I
> find this?", which is exactly what an in-process [MediaProvider](media-providers.md)
> does better (no bus round-trip, typed `Release` results). A *game* skill, by
> contrast, owns an interactive session, there is no catalog to extract, so it
> remains a skill. MediaProviders do **not** replace games.

The rest of this page documents the legacy **search-skill** flow (still functional
during the transition), then the **game-skill** path which is current.

## What is OCP?

OCP (OpenVoiceOS Common Play) is the media pipeline that routes voice utterances such as "play lofi hip hop" to actual media content. It is composed of three cooperating layers:

1. **OCP pipeline plugin** (`ovos-ocp-pipeline-plugin`), an intent pipeline stage that classifies utterances as media queries, determines the `MediaType`, and broadcasts search requests to registered OCP skills.
2. **OCP skills**, domain-specific skills (YouTube, Spotify, local music, radio, etc.) that respond to search requests by returning lists of `MediaEntry` objects ranked by confidence.
3. **`ovos-media`**, receives the winning `MediaEntry`, resolves the appropriate audio/video/web backend, and drives playback. The central class is `OCPMediaPlayer` (`ovos_media/player.py`).

## The OCP query flow

```
User says "play jazz"
  -> recognizer_loop:utterance
  -> ovos-ocp-pipeline-plugin classifies as MediaType.MUSIC
  -> ovos.common_play.search.start emitted
  -> ovos.common_play.query broadcast to all OCP skills
  -> Skills reply with ovos.common_play.query.response
     (list of MediaEntry, each with match_confidence 0-100)
  -> Pipeline selects best result
  -> ovos.common_play.play emitted with winning MediaEntry
  -> OCPMediaPlayer receives the track
  -> OCPMediaPlayer.set_now_playing stores it in now_playing
  -> OCPMediaPlayer routes to AudioService / VideoService / WebService
  -> Playback begins; MPRIS metadata updated if enabled
```

`OCPMediaPlayer.set_now_playing` accepts a `MediaEntry`, a `dict` representation of one, or a `Playlist`. It updates `self.now_playing`, adds the entry to the internal playlist, and notifies any connected MPRIS client by emitting a property-changed signal with the new `Metadata`.

## MediaEntry structure

`MediaEntry` is defined in `ovos_utils.ocp`. Key fields:

| Field | Type | Description |
|---|---|---|
| `uri` | `str` | Stream URL or local file path |
| `title` | `str` | Track title |
| `artist` | `str` | Artist name |
| `album` | `str` | Album name |
| `image` | `str` | Album art URL |
| `media_type` | `MediaType` | `MUSIC`, `VIDEO`, `AUDIOBOOK`, `PODCAST`, `RADIO`, etc. |
| `playback` | `PlaybackType` | `AUDIO`, `VIDEO`, `WEBVIEW`, `SKILL`, `MPRIS` |
| `skill_id` | `str` | ID of the skill that provided this entry |
| `match_confidence` | `float` | 0 to 100; how well the entry matched the query |
| `length` | `int` | Duration in milliseconds |

## NowPlaying

`NowPlaying` is a `MediaEntry` subclass that additionally subscribes to bus events to track live playback state. It is instantiated in `OCPMediaPlayer.__init__` and stored as `OCPMediaPlayer.now_playing`.

The `as_dict` property returns a plain `dict` with the current track's metadata fields (uri, title, artist, image, playback type, etc.). It is a property, not a method; access it as `player.now_playing.as_dict` without calling it.

`NowPlaying` tracks the seek position via the `ovos.common_play.playback_time` bus event. This position is exposed through MPRIS as `Position` in microseconds, see [mpris.md](mpris.md).

## Writing an OCP skill

> For new integrations, write a [MediaProvider](media-providers.md) instead, it
> is loaded in-process and returns typed `mediavocab.Release` objects. The skill
> approach below is retained for compatibility.

OCP skills subclass `OVOSCommonPlaybackSkill` from `ovos-workshop` and decorate a search method with `@ocp_search`. The method (any name) returns or yields `MediaEntry` objects / result dicts. Full documentation and the base class API are in the `ovos-workshop` package.

A minimal skeleton:

```python
from ovos_workshop.skills.common_play import OVOSCommonPlaybackSkill, MediaType, PlaybackType
from ovos_workshop.decorators.ocp import ocp_search
from ovos_utils.ocp import MediaEntry


class MyMusicSkill(OVOSCommonPlaybackSkill):
    @ocp_search()
    def search(self, phrase: str, media_type: MediaType):
        if media_type not in (MediaType.MUSIC, MediaType.GENERIC):
            return

        # Query your source here
        for item in self._my_search(phrase):
            yield MediaEntry(
                uri=item["stream_url"],
                title=item["title"],
                artist=item["artist"],
                image=item["thumbnail"],
                media_type=MediaType.MUSIC,
                playback=PlaybackType.AUDIO,
                skill_id=self.skill_id,
                match_confidence=item["score"],
            )
```

OCP skills are regular OVOS skills: they announce themselves on the bus with `ovos.common_play.announce` when they load, and the OCP pipeline tracks the available skills from those announcements.

## Game & interactive skills

**These are not deprecated.** A game skill *is* the media experience rather than a
catalog of it, so there is nothing for a MediaProvider to replace. Game skills
subclass `OVOSGameSkill` (or `ConversationalGameSkill` for turn-by-turn
voice games) from `ovos_workshop.skills.game_skill`, and OCP routes the session
to them via `PlaybackType.SKILL`, the daemon hands control to the skill instead
of streaming a URI through an audio/video backend.

`ConversationalGameSkill` gives a game a managed lifecycle (start/stop/pause/
resume, idle timeout) plus **intent layers**, sets of intents you enable/disable
as the game advances, so the same utterance means different things in different
game states.

```python
from ovos_workshop.skills.game_skill import ConversationalGameSkill
from ovos_workshop.decorators import layer_intent, enables_layer

class MyGameSkill(ConversationalGameSkill):
    def __init__(self, *args, **kwargs):
        super().__init__(skill_voc_filename="MyGameKeyword", *args, **kwargs)

    def initialize(self):
        self.intent_layers.disable()      # start with game intents off

    def on_play_game(self):
        """Called when OCP launches the game."""
        self.speak_dialog("intro")
        self.enable_intent_layer("main")

    def on_stop_game(self):
        """Clean up when the session ends."""

    @layer_intent(intent_name="guess.intent", layer_name="main")
    def handle_guess(self, message):
        ...
```

The reference implementation is
[`ovos-skill-moon-game`](https://github.com/OpenVoiceOS/ovos-skill-moon-game), an
Apollo-11 escape-room game built on `ConversationalGameSkill` with intent layers,
and the canonical end-to-end test that the game-skill integration still works on
the modern stack. Game skills register under the normal `ovos.plugin.skill`
entry-point group, not any `opm.media.*` group.

## Backend selection

When it plays, `OCPMediaPlayer` calls `_resolve_preferred_service`, which reads `preferred_audio_services`, `preferred_video_services`, or `preferred_web_services` from the `"media"` configuration section and returns the first matching loaded backend. If no preference is configured, the first available backend is used. The three service wrappers are `AudioService`, `VideoService`, and `WebService`, created in `OCPMediaPlayer.__init__`.

## Testing OCP skills with ovoscope

OCP skills can be exercised with the `ovoscope` end-to-end test framework. Consult `ovoscope/docs/ocp.md` for the full `OCPTest` API. A representative test looks like this:

```python
from ovoscope.ocp import OCPTest

result = OCPTest(
    skill_ids=["my-music-skill.openvoiceos"],
    utterance="play jazz",
    expected_media=[{"title": "Jazz Radio"}],
).execute()
```

The test fires a `recognizer_loop:utterance` message on a `FakeBus`, waits for the pipeline to select a result, and asserts the returned `MediaEntry` fields match `expected_media`. Mock HTTP responses can be injected to avoid real network calls.

For skills that are not exercised through the standard pipeline (for example, those using `PlaybackType.SKILL`), use `FakeBus` unit tests directly instead of `ovoscope`.

To test a modern [MediaProvider](media-providers.md) instead of a skill, use
ovoscope's `MediaProviderHarness` (see `ovoscope/docs/media-provider-testing.md`),
which drives a provider's `search()` directly without the skill machinery.

## Configuration reference

Backend preferences and feature flags live under the `"media"` key in the OVOS configuration:

```json
{
  "media": {
    "preferred_audio_services": ["vlc", "mpv"],
    "preferred_video_services": ["vlc"],
    "preferred_web_services": [],
    "enable_mpris": true,
    "manage_external_players": false
  }
}
```

See [mpris.md](mpris.md) for all MPRIS-specific options.

---

## See also

- [Media providers](media-providers.md), the current catalog/search approach that supersedes OCP skills
- [Architecture](architecture.md), where the pipeline and player sit in the flow
- [Backends](backends.md), the playback plugins that consume search results
- [Configuration](configuration.md), the `media` config block

---
[← Migration guide](migration-guide.md) · [Home](../README.md)

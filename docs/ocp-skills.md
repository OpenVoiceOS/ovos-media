# OCP Skills and the Media Pipeline (legacy)

> **Superseded.** OCP *search skills* (`OVOSCommonPlaybackSkill` + `@ocp_search`)
> are the legacy catalog/search approach. New catalog integrations should be
> written as [MediaProvider plugins](media-providers.md), which the OCP pipeline
> loads in-process instead of broadcasting bus queries to skills. This page
> documents the legacy flow, which still works during the transition.

## What is OCP?

OCP (OpenVoiceOS Common Play) is the media pipeline that routes voice utterances such as "play lofi hip hop" to actual media content. It is composed of three cooperating layers:

1. **OCP pipeline plugin** (`ovos-ocp-pipeline-plugin`) — an intent pipeline stage that classifies utterances as media queries, determines the `MediaType`, and broadcasts search requests to registered OCP skills.
2. **OCP skills** — domain-specific skills (YouTube, Spotify, local music, radio, etc.) that respond to search requests by returning lists of `MediaEntry` objects ranked by confidence.
3. **`ovos-media`** — receives the winning `MediaEntry`, resolves the appropriate audio/video/web backend, and drives playback. The central class is `OCPMediaPlayer` — `ovos_media/player.py:338`.

## The OCP query flow

```
User says "play jazz"
  -> recognizer_loop:utterance
  -> ovos-ocp-pipeline-plugin classifies as MediaType.MUSIC
  -> ovos.common_play.search.start emitted (GUI loading indicator)
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

`OCPMediaPlayer.set_now_playing` — `ovos_media/player.py:608` — accepts a `MediaEntry`, a `dict` representation of one, or a `Playlist`. It updates `self.now_playing`, adds the entry to the internal playlist, and notifies any connected MPRIS client by emitting a property-changed signal with the new `Metadata`.

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

`NowPlaying` — `ovos_media/player.py:146` — is a `MediaEntry` subclass that additionally subscribes to bus events to track live playback state. It is instantiated in `OCPMediaPlayer.bind` — `ovos_media/player.py:376` — and stored as `OCPMediaPlayer.now_playing` — `ovos_media/player.py:355`.

The `as_dict` property — `NowPlaying.as_dict` — `ovos_media/player.py:167` — returns a plain `dict` with the current track's metadata fields (uri, title, artist, image, playback type, etc.). It is a property, not a method; access it as `player.now_playing.as_dict` without calling it.

`NowPlaying` tracks the seek position via the `ovos.common_play.playback_time` bus event — `NowPlaying.__init__` — `ovos_media/player.py:158`. This position is exposed through MPRIS as `Position` in microseconds — see `docs/mpris.md`.

## Writing an OCP skill

> For new integrations, write a [MediaProvider](media-providers.md) instead — it
> is loaded in-process and returns typed `mediavocab.Release` objects. The skill
> approach below is retained for compatibility.

OCP skills subclass `OVOSCommonPlaybackSkill` from `ovos-workshop` and implement a `search_ocp` method that returns a list of `MediaEntry` objects. Full documentation and the base class API are in the `ovos-workshop` package.

A minimal skeleton:

```python
from ovos_workshop.skills.common_play import OVOSCommonPlaybackSkill, MediaType, PlaybackType
from ovos_utils.ocp import MediaEntry


class MyMusicSkill(OVOSCommonPlaybackSkill):
    def search_ocp(self, phrase: str, media_type: MediaType):
        if media_type not in (MediaType.MUSIC, MediaType.GENERIC):
            return []

        # Query your source here
        results = []
        for item in self._my_search(phrase):
            results.append(MediaEntry(
                uri=item["stream_url"],
                title=item["title"],
                artist=item["artist"],
                image=item["thumbnail"],
                media_type=MediaType.MUSIC,
                playback=PlaybackType.AUDIO,
                skill_id=self.skill_id,
                match_confidence=item["score"],
            ))
        return results
```

OCP skills are regular OVOS skills: they announce themselves on the bus with `ovos.common_play.announce` when they load, and the OCP pipeline tracks the available skills from those announcements.

## Backend selection

After `set_now_playing`, `OCPMediaPlayer` calls `_resolve_preferred_service` — `ovos_media/player.py:650` — which reads `preferred_audio_services`, `preferred_video_services`, or `preferred_web_services` from the `"media"` configuration section and returns the first matching loaded backend. If no preference is configured, the first available backend is used. The three service wrappers are `AudioService`, `VideoService`, and `WebService`, assigned in `OCPMediaPlayer.bind` — `ovos_media/player.py:378-380`.

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

- [Media providers](media-providers.md) — the current catalog/search approach that supersedes OCP skills
- [Architecture](architecture.md) — where the pipeline and player sit in the flow
- [Backends](backends.md) — the playback plugins that consume search results
- [Configuration](configuration.md) — the `media` config block

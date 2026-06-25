# Media Providers

A **MediaProvider** is a catalog/search plugin: given a parsed media request it
returns candidate playables. Providers are the layer that turns *"play Viagra
Boys"* into a ranked list of streamable tracks, and they are the catalog layer
of the `ovos-media` stack.

Providers are the in-process replacement for the older OCP *search skills*
(`OVOSCommonPlaybackSkill` + `@ocp_search`). Instead of broadcasting
`ovos.common_play.query` over the bus and waiting for skills to answer, the OCP
pipeline loads providers **in-process** and calls each provider's `search()`
method directly. See [Migration: from OCP search skills](#migration-from-ocp-search-skills)
below.

---

## Plugin group

Providers register under the `opm.media.provider` entry-point group and subclass
`ovos_plugin_manager.templates.media_provider.MediaProvider`:

```toml
[project.entry-points."opm.media.provider"]
bandcamp = "ovos_media_provider_bandcamp:BandcampMediaProvider"
```

The entry-point key (`bandcamp`) is the provider's registry name, its `skill_id`
downstream, and its per-instance config key under `media_providers`.

---

## The contract: one method

`MediaProvider` has a **single abstract method**:

```python
def search(self, signals: Signals, lang: str = "en-us",
           **context) -> list[Release]:
    ...
```

There is nothing else to implement. There is no `is_available`, no `matches`, no
`serves`, no routing class attributes, and no `QueryContext` object — a provider
decides for itself whether it can serve a query and returns an empty list when it
cannot. Availability and routing are the provider's own concern.

```python
class MediaProvider(metaclass=ABCMeta):
    name: ClassVar[str] = ""        # stable registry key / downstream skill_id

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    @abstractmethod
    def search(self, signals, lang="en-us", **context) -> list[Release]: ...

    def shutdown(self) -> None:     # optional — release resources
        ...
```

### Arguments

- **`signals`** is a `mediavocab.Signals` — the parsed request. The fields a
  provider usually reads are `signals.title` (the search phrase) and
  `signals.artist` (artist, or director for video). `signals.medium` carries the
  classified `MediaType` and `signals.content_genres` the requested genres.
- **`lang`** is the BCP-47 language tag for the request (default `"en-us"`).
- **`**context`** carries whatever the pipeline knows about the request
  environment. A provider reads the kwargs it cares about and ignores the rest.
  Recognised keys include:

  | Context kwarg | Meaning |
  |---|---|
  | `supported_playback_types` | which player surfaces the requesting device can render (so a video-only provider can bail out on an audio-only device) |
  | `blocked_genres` | genres the session/profile has blocked |
  | `region` | the requester's region/country, for geo-scoped catalogs |
  | `session_id` | the originating MessageBus session |

### Return value

Return zero or more `mediavocab.Release` objects. Each `Release` is a typed,
playable catalog entry shared across the whole media ecosystem. A provider
returns **playables**, not identities — drop artist/label hits. Return an empty
list `[]` whenever the provider cannot serve the query: wrong media type, the
device can't render the result, a blocked genre, no network or API key, no
match, and so on.

**Ranking** rides on each result via `Release.match_confidence` (`0.0`–`1.0`).
The pipeline filters and ranks *across* all providers, so a provider only needs
to score within its own results.

### `mediavocab.Release` in brief

A `Release` is a manifestation of a `Work`. The fields most relevant to playback:

| Field | Where | Description |
|---|---|---|
| `work.title` | `Release.work` | Track / album / show title |
| `work.media_type` | `Release.work` | `MediaType` of the content |
| `work.content_genres` | `Release.work` | Genre tags |
| `uri` | `Release` | Stream URL or deferred stream identifier (see below) |
| `image` | `Release` | Cover / thumbnail art URL |
| `match_confidence` | `Release` | `0.0`–`1.0` relevance score |

Most provider authors never build a `Release` by hand: the client library a
provider wraps (`py_bandcamp`, `tutubo`, `radiosoma`, …) already emits typed
`Release` objects, so a provider is a thin routing/filtering shim over that
client's search API.

### Deferred stream URIs

A `Release.uri` may be a real URL or a deferred stream identifier of the form
`"{sei}//{uri}"` (for example `youtube//https://youtube.com/watch?v=…`). These
are resolved at playback time by the `opm.ocp.extractor` plugins — provider code
does not resolve streams itself. See [Architecture](architecture.md) and
[Backends](backends.md).

---

## How the pipeline discovers and dispatches providers

The OCP pipeline loads every installed `opm.media.provider` entry point,
instantiating each with its per-provider config (`media_providers.<name>`) and
skipping any whose config sets `"enabled": false`.

At query time the pipeline:

1. Classifies the utterance into a `Signals` (media type, title, artist, genres).
2. Builds the `**context` (supported playback types, blocked genres, region,
   session) from the requesting session's state.
3. Calls `search(signals, lang, **context)` on each loaded provider
   **concurrently** (thread pool). A provider that cannot serve the request
   returns `[]`; a misbehaving provider that raises is caught and treated as `[]`
   so it cannot abort a multi-provider dispatch.
4. Collects, filters, and ranks the returned `Release` objects across providers,
   then hands the winner(s) to the `ovos-media` daemon to play.

Because providers run in-process, there is no bus round-trip and no per-skill
announce/response handshake.

---

## A minimal provider

A complete provider over a client library that already emits `Release` objects:

```python
from typing import ClassVar, List, Optional

from ovos_utils.log import LOG
from mediavocab import Release, Signals
from ovos_plugin_manager.templates.media_provider import MediaProvider

from py_bandcamp import BandCamp


class BandcampMediaProvider(MediaProvider):
    """Search Bandcamp's public catalog for playable music releases."""

    name: ClassVar[str] = "bandcamp"

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.max_pages = self.config.get("max_pages", 1)

    def search(self, signals: Signals, lang: str = "en-us",
               **context) -> List[Release]:
        title = (signals.title or "").strip()
        artist = (signals.artist or "").strip()
        query = " ".join(p for p in (artist, title) if p).strip()
        if not query:
            return []  # nothing to search for

        results: List[Release] = []
        try:
            for item in BandCamp.search(query, albums=True, tracks=True,
                                        artists=False, labels=False,
                                        max_pages=self.max_pages):
                if isinstance(item, Release):
                    results.append(item)
                # Entity (artist/label identity) hits are not playable — skip
        except Exception:
            LOG.exception("Bandcamp search failed")
            return []  # no network / API failure — serve nothing
        return results
```

Register it in `pyproject.toml`:

```toml
[project.entry-points."opm.media.provider"]
bandcamp = "ovos_media_provider_bandcamp:BandcampMediaProvider"
```

Notice the provider never declares what it serves up front: if the query is for a
movie, Bandcamp's client simply returns nothing and the provider yields `[]`.
That is the whole gating contract.

---

## Existing providers

Each wraps a standalone scraper/client library that emits `mediavocab.Release`
objects, and each replaces a legacy OCP search skill.

| Provider | Entry point | Serves | Replaces |
|---|---|---|---|
| [`ovos-media-provider-youtube`](https://github.com/OpenVoiceOS/ovos-media-provider-youtube) | `youtube` | video / music videos / podcasts | `ovos-skill-youtube` |
| [`ovos-media-provider-youtube-music`](https://github.com/OpenVoiceOS/ovos-media-provider-youtube-music) | `youtube_music` | music | `ovos-skill-youtube-music` |
| [`ovos-media-provider-bandcamp`](https://github.com/OpenVoiceOS/ovos-media-provider-bandcamp) | `bandcamp` | music | `ovos-skill-bandcamp` |
| [`ovos-media-provider-soundcloud`](https://github.com/OpenVoiceOS/ovos-media-provider-soundcloud) | `soundcloud` | music / playlists | `ovos-skill-soundcloud` |
| [`ovos-media-provider-tunein`](https://github.com/OpenVoiceOS/ovos-media-provider-tunein) | `tunein` | radio | `ovos-skill-tunein` |
| [`ovos-media-provider-somafm`](https://github.com/OpenVoiceOS/ovos-media-provider-somafm) | `somafm` | radio | `ovos-skill-somafm` |
| [`ovos-media-provider-pyradios`](https://github.com/OpenVoiceOS/ovos-media-provider-pyradios) | `pyradios` | radio | `ovos-skill-pyradios` |

---

## Configuration

Per-provider settings live under the top-level `media_providers` key of
`mycroft.conf`, keyed by the provider's entry-point name. Any keys are passed
through to the provider's `config` dict; `enabled: false` disables a provider
without uninstalling it.

```json
{
  "media_providers": {
    "bandcamp": {
      "max_pages": 2
    },
    "youtube": {
      "max_results": 10
    },
    "soundcloud": {
      "enabled": false
    }
  }
}
```

See [Configuration reference](configuration.md) for the daemon-side `media` block
(backends, MPRIS, GUI).

---

## Migration: from OCP search skills

Providers replace the catalog/search half of the old OCP design. The control and
playback halves are unchanged — those live in the OCP pipeline and the
`ovos-media` daemon.

| Old (OCP search skill) | New (MediaProvider) |
|---|---|
| Subclass `OVOSCommonPlaybackSkill` | Subclass `MediaProvider` |
| `@ocp_search` method returning `MediaEntry`/`Playlist` | `search(signals, lang, **context)` returning `list[Release]` |
| Registered as a skill; replies to `ovos.common_play.query` over the bus | Registered under `opm.media.provider`; called in-process |
| Routing implied by the skill's vocab and per-result `media_type` | The provider decides per call and returns `[]` when it can't serve |
| Match score `0`–`100` on each `MediaEntry` | `match_confidence` `0.0`–`1.0` on each `Release` |
| Provider-specific result dicts | Typed `mediavocab.Release`, shared across the ecosystem |

The legacy approach is documented in [OCP Skills](ocp-skills.md) and still works
during the transition, but new catalog integrations should be written as
MediaProviders.

---

## See also

- [Architecture](architecture.md) — where providers sit in the full flow
- [Backends](backends.md) — the playback plugins that consume provider results
- [OCP Skills](ocp-skills.md) — the legacy search-skill approach this supersedes
- [Configuration](configuration.md) — daemon-side configuration
- [mediavocab](https://github.com/TigreGotico/mediavocab) — the `Release`/`Signals` data model

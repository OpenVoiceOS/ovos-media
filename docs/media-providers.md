# Media Providers

A **MediaProvider** is a catalog/search plugin: given a parsed media request it
returns candidate playables. Providers are the layer that turns *"play Viagra
Boys"* into a ranked list of streamable tracks, and they are the catalog layer
of the `ovos-media` stack.

Providers supersede the older OCP *search skills* (`OVOSCommonPlaybackSkill` +
`@ocp_search`). Instead of broadcasting `ovos.common_play.query` over the bus and
waiting for skills to answer, the OCP pipeline loads providers **in-process**,
gates each one by routing, and calls its `search()` method directly. See
[Migration: from OCP search skills](#migration-from-ocp-search-skills) below.

---

## Plugin group

Providers register under the `opm.media.provider` entry-point group and subclass
`ovos_plugin_manager.templates.media_provider.MediaProvider`:

```toml
[project.entry-points."opm.media.provider"]
bandcamp = "ovos_media_provider_bandcamp:BandcampMediaProvider"
```

The entry-point key (`bandcamp`) is the provider's registry name and its
per-instance config key under `media_providers`.

---

## The three-axis routing contract

Every provider declares three class-level sets that let the pipeline skip it
*before* paying for a search. This mirrors mediavocab's canonical routing gate
(`mediavocab.models.protocols.provider_matches`):

| Class attribute | Type | Meaning | Empty set means |
|---|---|---|---|
| `media` | `set[mediavocab.MediaType]` | Which media types the provider serves (`MUSIC`, `RADIO`, `PODCAST`, `MOVIE`, …) | universal — matches any media type |
| `playback_type` | `set[mediavocab.taxonomy.PlaybackType]` | Player surface: `AUDIO`, `VIDEO`, `PAGED`, `INTERACTIVE` | universal — matches any surface |
| `genre_filter` | `set[str]` | Genre tags (`mediavocab.taxonomy.genre`) the provider is scoped to | no genre gate |

> **Two `PlaybackType` enums.** The routing axis here is
> `mediavocab.taxonomy.PlaybackType` (`AUDIO`/`VIDEO`/`PAGED`/`INTERACTIVE`),
> which is distinct from `ovos_utils.ocp.PlaybackType` (the backend selector used
> by the daemon when it picks an audio/video/web player). The two are bridged in
> the pipeline and player — not inside a provider.

The default `matches(signals)` implementation applies all three axes. Override it
only if your provider needs routing logic the three sets cannot express.

---

## The `search()` contract

```python
def search(self, signals: Signals, lang: str = "en-us") -> list[Release]:
    ...
```

- **`signals`** is a `mediavocab.Signals` — the parsed request. The fields a
  provider usually reads are `signals.title` (the search phrase) and
  `signals.artist` (artist, or director for video). `signals.medium` carries the
  classified `MediaType` and `signals.content_genres` the requested genres.
- **Return** zero or more `mediavocab.Release` objects. Each `Release` is a
  typed, playable catalog entry shared across the whole media ecosystem. A
  provider returns **playables**, not identities — drop artist/label hits.
- **Ranking** rides on each result via `Release.match_confidence` (`0.0`–`1.0`).
  The pipeline filters and ranks *across* all providers, so you only need to
  score within your own results.

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

## Lifecycle methods

| Method | Required | Purpose |
|---|---|---|
| `is_available() -> bool` | yes | Return `True` only when the provider can run now — API keys present, optional deps importable, network reachable. Unavailable providers are skipped at load. |
| `search(signals, lang) -> list[Release]` | yes | The search itself (above). |
| `featured_media(lang) -> list[Release]` | no | Curated/home content for the browse screen. Defaults to `[]`. |
| `matches(signals) -> bool` | no | Routing gate; defaults to the three-axis test. |
| `shutdown()` | no | Release any held resources. |

The pipeline calls providers through `search_safe()`, which wraps `search()` so a
single misbehaving provider cannot abort a multi-provider dispatch — it logs and
returns `[]` on error.

---

## How the pipeline discovers and dispatches providers

Discovery and instantiation live in `ovos_plugin_manager.media_provider`:

1. `find_media_provider_plugins()` enumerates every installed `opm.media.provider`
   entry point.
2. `load_media_providers(config)` instantiates each one with its per-provider
   config (`media_providers.<name>`), skips any whose config sets
   `"enabled": false`, and skips any whose `is_available()` returns `False`.
   It returns a `dict` of `{name: instance}`.

At query time the OCP pipeline:

1. Classifies the utterance into a `Signals` (media type, title, artist, genres).
2. Gates the loaded providers with `provider.matches(signals)`, dropping any
   whose routing axes exclude the query.
3. Dispatches `search_safe(signals, lang)` to the surviving providers
   **concurrently** (thread pool).
4. Collects, filters, and ranks the returned `Release` objects across providers,
   then hands the winner(s) to the `ovos-media` daemon to play.

Because providers run in-process, there is no bus round-trip and no per-skill
announce/response handshake.

---

## A minimal provider

A complete provider over a client library that already emits `Release` objects:

```python
from typing import ClassVar, List, Optional, Set

from ovos_utils.log import LOG
from mediavocab import MediaType, Release, Signals, Entity
from mediavocab.taxonomy import PlaybackType
from ovos_plugin_manager.templates.media_provider import MediaProvider

from py_bandcamp import BandCamp


class BandcampMediaProvider(MediaProvider):
    """Search Bandcamp's public catalog for playable music releases."""

    name: ClassVar[str] = "bandcamp"
    media: ClassVar[Set[MediaType]] = {MediaType.MUSIC}
    playback_type: ClassVar[Set[PlaybackType]] = {PlaybackType.AUDIO}

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.max_pages = self.config.get("max_pages", 1)

    def is_available(self) -> bool:
        # public search endpoint, no API key required
        return True

    def search(self, signals: Signals, lang: str = "en-us") -> List[Release]:
        title = (signals.title or "").strip()
        artist = (signals.artist or "").strip()
        query = " ".join(p for p in (artist, title) if p).strip()
        if not query:
            return []

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
            return []
        return results
```

Register it in `pyproject.toml`:

```toml
[project.entry-points."opm.media.provider"]
bandcamp = "ovos_media_provider_bandcamp:BandcampMediaProvider"
```

---

## Existing providers

Each wraps a standalone scraper/client library that emits `mediavocab.Release`
objects, and each replaces a legacy OCP search skill.

| Provider | Entry point | Routing (`media` → `playback_type`) | Replaces |
|---|---|---|---|
| [`ovos-media-provider-youtube`](https://github.com/OpenVoiceOS/ovos-media-provider-youtube) | `youtube` | `MOVIE, EPISODIC_SERIES, MUSIC_VIDEO, PODCAST, TV, GENERIC` → `VIDEO, AUDIO` | `ovos-skill-youtube` |
| [`ovos-media-provider-youtube-music`](https://github.com/OpenVoiceOS/ovos-media-provider-youtube-music) | `youtube_music` | music → `AUDIO` | `ovos-skill-youtube-music` |
| [`ovos-media-provider-bandcamp`](https://github.com/OpenVoiceOS/ovos-media-provider-bandcamp) | `bandcamp` | `MUSIC` → `AUDIO` | `ovos-skill-bandcamp` |
| [`ovos-media-provider-soundcloud`](https://github.com/OpenVoiceOS/ovos-media-provider-soundcloud) | `soundcloud` | `MUSIC, PLAYLIST` → `AUDIO` | `ovos-skill-soundcloud` |
| [`ovos-media-provider-tunein`](https://github.com/OpenVoiceOS/ovos-media-provider-tunein) | `tunein` | `RADIO` → `AUDIO` | `ovos-skill-tunein` |
| [`ovos-media-provider-somafm`](https://github.com/OpenVoiceOS/ovos-media-provider-somafm) | `somafm` | `RADIO` → `AUDIO` | `ovos-skill-somafm` |
| [`ovos-media-provider-pyradios`](https://github.com/OpenVoiceOS/ovos-media-provider-pyradios) | `pyradios` | `RADIO` → `AUDIO` | `ovos-skill-pyradios` |

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
| `@ocp_search` method returning `MediaEntry`/`Playlist` | `search(signals, lang)` returning `list[Release]` |
| Registered as a skill; replies to `ovos.common_play.query` over the bus | Registered under `opm.media.provider`; called in-process |
| Routing implied by the skill's vocab and per-result `media_type` | Explicit three-axis routing (`media`, `playback_type`, `genre_filter`) |
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

# ovos-media documentation

`ovos-media` is the OCP-native media daemon for OpenVoiceOS. It plays audio,
video, and web content on behalf of OVOS, managing the queue, now-playing
state, and playback backends, while broadcasting that state over the
MessageBus and MPRIS/D-Bus. It runs alongside `ovos-audio`, which continues to
handle TTS.

Apache-2.0 · Python 3.10+

> **New here?** Read the [Glossary & core concepts](glossary.md) first (5 minutes).
> It explains OCP, providers vs. backends vs. extractors, `Signals`/`Release`, and
> the mental model the rest of these docs assume.

## Start here

| You are… | Go to |
|----------|-------|
| new to the media stack | [Glossary](glossary.md) → [Getting started](getting-started.md) |
| running or tuning a device | [Getting started](getting-started.md) → [Configuration](configuration.md) |
| writing a search plugin | [Glossary](glossary.md) → [Media providers](media-providers.md) |
| writing a player plugin | [Backends](backends.md) |
| writing a game or interactive skill | [OCP skills](ocp-skills.md) (still a skill, *not* a provider) |
| wiring desktop or `playerctl` | [MPRIS](mpris.md) |
| hacking on the daemon | [Architecture](architecture.md) |
| running HiveMind satellites or a server | [Sessions](sessions.md) |
| migrating from the old stack | [Migration guide](migration-guide.md) |

---

## How it fits together

```
 "play jazz on the kitchen speaker"
              │
              ▼
   ovos-core ─ OCP pipeline (ovos-ocp-pipeline-plugin)
              │   classify the media type + parse the request
              │   query MediaProvider plugins, rank results
              ▼
   ovos-media (this daemon)
              │   pick a playback backend, manage queue / now-playing,
              │   broadcast state over the bus / MPRIS
              ▼
   playback backend (opm.media.audio | .video | .web)
              │   hand the URI to vlc / mplayer / spotify / chromecast / browser …
              ▼
   stream extractor (opm.ocp.extractor) resolves youtube//… , rss//… , file://…
```

Every arrow is a plugin boundary, so each concern can be replaced independently:

| Concern | Plugin group | Examples |
|---|---|---|
| **Find media** (catalog/search) | `opm.media.provider` | youtube, bandcamp, soundcloud, tunein, somafm, pyradios |
| **Play audio** | `opm.media.audio` | vlc, mplayer, simple (cli), ffplay, spotify, chromecast, mass, mpris |
| **Play video** | `opm.media.video` | vlc, mplayer, chromecast |
| **Render web/webview** | `opm.media.web` | rendered directly by the backend plugin |
| **Resolve a stream URI** | `opm.ocp.extractor` | youtube, m3u, rss, files |

Search results flow as [`mediavocab.Release`](https://github.com/TigreGotico/mediavocab)
objects, a typed catalog model shared across the media ecosystem, so a provider
written once feeds both playback and MPRIS metadata.

---

## Documentation

| Document | What it covers |
|---|---|
| [Glossary & core concepts](glossary.md) | **Read first.** Every acronym + the mental model (provider vs. backend vs. extractor, `Signals`/`Release`) |
| [Getting started](getting-started.md) | Install, enable the daemon, run your first playback |
| [Architecture](architecture.md) | The daemon's layers, bus API, state machine, MPRIS integration |
| [Sessions](sessions.md) | The default/local session filter, how a HiveMind server ignores satellite sessions (`validate_source`) |
| [Media providers](media-providers.md) | Writing a catalog/search plugin (`opm.media.provider`), the new search layer |
| [Playback backends](backends.md) | Audio/video/web backend plugins, discovery, writing a custom backend |
| [Configuration](configuration.md) | Full `mycroft.conf` reference for the `media` and `media_providers` keys |
| [MPRIS integration](mpris.md) | D-Bus MPRIS support, external player control, `playerctl` |
| [Migration guide](migration-guide.md) | Moving from the legacy audio service to `ovos-media` |
| [OCP skills](ocp-skills.md) | Media *search* skills are superseded by [media providers](media-providers.md). **Game and interactive skills stay here.** |

---

## Quick config example

```json
{
  "enable_old_audioservice": false,
  "media": {
    "preferred_audio_services": ["vlc", "mplayer", "cli"],
    "enable_mpris": true
  }
}
```

Full reference: [configuration.md](configuration.md).

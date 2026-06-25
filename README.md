# ovos-media

**The OVOS Virtual Media Player** — a standalone daemon that plays audio, video and
web content on behalf of OpenVoiceOS, with per-session playback state, MPRIS/D-Bus
integration and a pluggable backend architecture.

`ovos-media` implements **OCP — OVOS Common Playback**: one logical media player per
session that *every* media voice command targets. It arbitrates both OVOS-initiated
playback ("play jazz") and transport control ("pause", "next", "stop the music"), and
because it bridges to the host OS over MPRIS, even playback OVOS did **not** start — a
browser tab, a desktop player — is controllable by voice as long as it speaks the open
standard. The concept is specified in
[OVOS-OCP-1](https://github.com/OpenVoiceOS/architecture/blob/dev/ovos-ocp-1.md).

`ovos-media` is the modern replacement for the legacy audio service (the
`ovos-ocp-audio-plugin` bundled inside `ovos-audio`). It splits the monolith into
small, swappable pieces: **the OCP pipeline finds media, providers supply catalogs,
backends play streams, extractors resolve URIs.**

---

## How it fits together

```
 "play jazz on the kitchen speaker"
              │
              ▼
   ovos-core ─ OCP pipeline (ovos-ocp-pipeline-plugin)
              │   classify media type + parse the request
              │   query MediaProvider plugins, rank results
              ▼
   ovos-media (this daemon)
              │   pick a playback backend, manage the queue / now-playing,
              │   expose state over the bus / MPRIS / GUI
              ▼
   playback backend (opm.media.audio | .video | .web)
              │   hand the URI to vlc / mpv / spotify / chromecast / browser …
              ▼
   stream extractor (opm.ocp.extractor) resolves youtube//… , rss//… , file://…
```

Every arrow is a plugin boundary, so each piece can be replaced independently:

| Concern | Plugin group | Examples |
|---|---|---|
| **Find media** (catalogs/search) | `opm.media.provider` | youtube, bandcamp, soundcloud, tunein, somafm, pyradios |
| **Play audio** | `opm.media.audio` | vlc, mplayer, simple (cli), ffplay, spotify, chromecast, mass, mpris |
| **Play video** | `opm.media.video` | vlc, mplayer, chromecast |
| **Render web/webview** | `opm.media.web` | (rendered via the GUI WebView) |
| **Resolve a stream URI** | `opm.ocp.extractor` | youtube, m3u, rss, files |

Search results flow as [`mediavocab.Release`](https://github.com/TigreGotico/mediavocab)
objects — a typed catalog model shared across the whole media ecosystem — so a
provider written once works for playback, MPRIS metadata and the GUI alike.

---

## Install

```bash
pip install ovos-media
```

Install at least one playback backend (audio is the minimum to hear anything):

```bash
pip install ovos-media-plugin-vlc        # or -mplayer / -simple / -spotify / -chromecast
```

### Enable it

`ovos-media` runs alongside `ovos-audio` (which keeps handling TTS). Turn off the
legacy audio service and run the daemon:

```json
// mycroft.conf
{
  "enable_old_audioservice": false
}
```

```bash
ovos-media          # start the daemon
```

That's it — ask OVOS to play something and the OCP pipeline will route it here.

---

## Configuration

All configuration lives under the `"media"` key in `mycroft.conf`. The essentials:

```jsonc
{
  "media": {
    // MPRIS / D-Bus integration (off by default)
    "enable_mpris": false,
    // let MPRIS pause/stop other media players on the system
    "manage_external_players": false,

    // order of preference per playback type; the first backend that can
    // handle the URI wins. Users may also name a backend in the utterance.
    "preferred_audio_services": ["vlc", "mplayer", "cli"],
    "preferred_video_services": ["vlc"],

    // declare the backends available to each playback type.
    // "module" is the plugin's entry-point name; "aliases" are spoken names.
    "audio_players": {
      "vlc": { "module": "ovos-media-audio-plugin-vlc", "aliases": ["VLC"], "active": true },
      "cli": { "module": "ovos-media-audio-plugin-cli", "aliases": ["Command Line"], "active": true }
    },
    "video_players": {
      "vlc": { "module": "ovos-media-video-plugin-vlc", "aliases": ["VLC"], "active": true }
    }
  }
}
```

See **[docs/configuration.md](docs/configuration.md)** for every option (per-backend
config, MPRIS roles, GUI update interval, queue behaviour).

---

## Documentation

Start at **[docs/index.md](docs/index.md)**.

- [Getting started](docs/getting-started.md) — install, enable, first playback
- [Architecture](docs/architecture.md) — the daemon, the bus API, the plugin boundaries
- [Media providers](docs/media-providers.md) — write a catalog/search plugin (`opm.media.provider`)
- [Playback backends](docs/backends.md) — audio/video/web backend plugins
- [Configuration reference](docs/configuration.md)
- [MPRIS / D-Bus](docs/mpris.md)
- [Migrating from the legacy audio service](docs/migration-guide.md)

---

## Status

`ovos-media` is the OCP-native playback stack and is opt-in today (enable it by
turning off the legacy audio service). Catalogs are supplied by
[`MediaProvider` plugins](docs/media-providers.md) (`opm.media.provider`); the
legacy OCP *search skills* still work during the transition.

---

## Credits

The original [OCP dataset](https://github.com/NeonGeckoCom/OCP-dataset) used to train
the media classifiers was sponsored by [@NeonGeckoCom](https://github.com/NeonGeckoCom/)
as part of [The OCP Sprint](https://github.com/OpenVoiceOS/ovos-ocp-audio-plugin/issues/74).
More recent media-metadata datasets are maintained by **TigreGotico** and published in
the [Media Metadata collection on Hugging Face](https://huggingface.co/collections/TigreGotico/media-metadata).

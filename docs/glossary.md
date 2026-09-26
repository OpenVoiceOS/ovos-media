# Glossary & core concepts

New to the OVOS media stack? Read this once and the rest of the docs click into
place. It defines every acronym and, more importantly, the handful of concepts
people most often mix up.

## The 30-second mental model

You say *"play some jazz"*. Four kinds of plugin cooperate to make sound come out:

1. a **provider** *finds* candidate tracks (searches catalogs),
2. **ovos-media** (this daemon) *decides* what to play and *tracks* what's playing,
3. a **backend** *plays* it (hands the URL to VLC, mpv, a Chromecast, …),
4. an **extractor** *resolves* a deferred link (e.g. a `youtube//…` placeholder)
   into a real stream URL at the moment of playback.

Each is a separate, swappable plugin. That separation is the whole design.

## The four plugin roles (don't mix these up)

| Role | Entry-point group | Answers the question | Example |
|------|-------------------|----------------------|---------|
| **MediaProvider** | `opm.media.provider` | *"where can I find this?"* | youtube, bandcamp, tunein |
| **Playback backend** | `opm.media.audio` / `.video` / `.web` | *"how do I play this?"* | vlc, mpv, chromecast |
| **Stream extractor** | `opm.ocp.extractor` | *"what's the real URL?"* | youtube, m3u, rss |
| **MPRIS plugin** | (see [mpris.md](mpris.md)) | *"what are other apps playing?"* | the mpris watcher |

> **Provider vs. backend** is the #1 point of confusion. A *provider* is a search
> engine (returns a list of results). A *backend* is a player (turns one result
> into sound/video). "youtube" exists as both a provider (searches YouTube) and is
> played by a backend (vlc/mpv) after the youtube *extractor* resolves the stream.

## Terms

| Term | Meaning |
|------|---------|
| **OCP** | *OpenVoiceOS Common Play*, the framework/protocol for voice-driven media ("play X"). The **OCP pipeline** is the ovos-core stage that classifies a "play …" utterance and asks providers to search. |
| **ovos-media** | This daemon, the OCP-native player. Manages the queue, now-playing state, and playback backends. Runs **alongside `ovos-audio`** (which does TTS). |
| **ovos-audio** | The separate daemon that handles **TTS** (and the *legacy* audio service). Not a replacement for ovos-media; they coexist. |
| **OPM** | *ovos-plugin-manager*, discovers plugins via Python entry points. The `opm.*` groups above are OPM groups. |
| **Signals** | A [`mediavocab.Signals`](https://github.com/TigreGotico/mediavocab) object, the *parsed request* a provider receives: `title`, `artist`, `medium` (a `MediaType`), `content_genres`, … |
| **Release** | A [`mediavocab.Release`](https://github.com/TigreGotico/mediavocab), a *typed, playable catalog entry* a provider returns. The shared currency between search, playback, and MPRIS metadata. |
| **Work** | The creative work a `Release` manifests (`Release.work.title`, `.media_type`, …). |
| **MediaType** | What *kind* of content it is (music, movie, podcast, radio, audiobook, …). |
| **PlaybackType** | What *surface* renders it: `AUDIO`, `VIDEO`, `WEBVIEW`, `MPRIS`. Determines which backend group plays it. |
| **PlayerState** | The player's transport state: `PLAYING`, `PAUSED`, `STOPPED`. (A *paused* track is still `PLAYING_*` at the track level, pause lives only in `PlayerState`.) |
| **MediaState** | The media's lifecycle: buffering, loaded, end-of-media, invalid, … |
| **now_playing** | The single currently-playing `Release` the daemon tracks (`NowPlaying`), mirrored to the bus and MPRIS. |
| **SEI** | *Stream Extractor Identifier*, the prefix in a deferred URI (`youtube//…`, `rss//…`) that names which extractor resolves it. |
| **deferred / deferred URI** | A `Release.uri` of the form `"{sei}//{realuri}"` resolved **at playback time** by an extractor, not at search time. |
| **MPRIS** | The freedesktop D-Bus standard (`org.mpris.MediaPlayer2`) other Linux media apps speak. ovos-media both exposes itself over it and reacts to other players. See [mpris.md](mpris.md). |
| **MessageBus** | The OVOS WebSocket event bus. Everything ovos-media does is observable/driveable as `ovos.common_play.*` messages. |
| **Session** | A MessageBus conversation scope identified by `session_id`. The special id `"default"` means the local/single device. ovos-media only *acts* on the `"default"` session (a HiveMind server ignores satellite sessions); see [Sessions](sessions.md). |
| **validate_source** | The `media` config flag (default `true`) that enables the default-session filter. Set `false` on a satellite that is not getting default-NAT'd sessions so it acts on every session. See [Sessions](sessions.md). |
| **OCP search skill** *(legacy)* | The old way to provide *searchable catalog media* (`OVOSCommonPlaybackSkill` + `@ocp_search`), now **replaced by MediaProviders**. See [ocp-skills.md](ocp-skills.md). |
| **OCP game / interactive skill** | A skill that *is* the experience (interactive fiction, quizzes, "play a game") rather than a searchable catalog. These stay as **skills**, they are **not** deprecated and have no MediaProvider equivalent. See [ocp-skills.md](ocp-skills.md). |

## Which doc do I want?

- *I just want it running* → [Getting started](getting-started.md)
- *I want to tune it* → [Configuration](configuration.md)
- *I'm writing a search plugin* → [Media providers](media-providers.md)
- *I'm writing a player plugin* → [Backends](backends.md)
- *I want desktop / `playerctl` control* → [MPRIS](mpris.md)
- *I want to understand the internals* → [Architecture](architecture.md)
- *I'm running HiveMind satellites / a server* → [Sessions](sessions.md)
- *I'm coming from the old stack* → [Migration guide](migration-guide.md)

---
[Home](../README.md) · [Getting started →](getting-started.md)

# Sessions: the default/local session filter

`ovos-media` is a **single player bound to one device**. It does not manage a
player per remote client; conceptually it only ever drives *its own* device,
identified by the `"default"` session.

This page explains how that plays out in a HiveMind satellite/server topology
and how to configure it.

## The problem

In a single-device install everything shares the `"default"` session, so there
is nothing to think about: every playback command is local and is executed.

In a **HiveMind split** the OCP pipeline (`ovos-ocp-pipeline-plugin`) runs on
the **server**. The pipeline already tracks one player proxy per MessageBus
session and forwards playback commands **stamped with the originating session**
(the satellite's `session_id`). If the server-side `ovos-media` blindly acted on
every `ovos.common_play.*` message it received, a satellite asking to play music
would start playback on the **server's** speakers, the wrong device.

## The rule

`ovos-media` acts only on commands for the **local/"default"** session and
**ignores** commands stamped with any other session id.

- A **server-side** `ovos-media` ignores a satellite's playback command
  (`session_id != "default"`). The satellite has its own embedded `ovos-media`
  that handles it.
- The **satellite's** embedded `ovos-media` sees the command as `"default"` and
  executes it. hivemind-core NATs the satellite's session to `"default"` for the
  satellite-local instance, so from that instance's point of view the request is
  local.

This mirrors how `ovos-audio` scopes TTS to native/local sources
(`ovos_audio.utils.require_default_session`).

## Implementation

A `require_default_session()` decorator
(`ovos_media/utils.py`) gates each playback-**executing** bus handler. A handler
runs only if:

- the message is internal/synthetic (`message is None`), **or**
- `validate_source` is False (act on everything, see below), **or**
- `SessionManager.get(message).session_id == "default"`.

Otherwise it logs at debug level and returns without acting.

Gated handlers (the ones that change playback or persistent state):

| Layer | Handlers |
|---|---|
| `OCPMediaPlayer` (`player.py`) | play, pause, resume, stop, play/pause toggle, next, previous, seek, set track position, playlist set/queue/clear, shuffle set/unset/toggle, repeat set/unset/toggle, duck, cork, uncork (and unduck restore), like, unlike |
| `NowPlaying` (`player.py`) | `handle_external_play` (so a non-default play does not bleed metadata into the local now-playing) |
| `LegacyAudioServiceCompat` (`legacy_api.py`) | `mycroft.audio.service.*`: play, queue, pause, resume, stop, next, prev, set/seek position |

**Read-only query handlers are *not* gated**, `status`, `track_info`,
`get_track_length`, `get_track_position`, `list_backends`. These reply to the
asker (via `message.response`), so answering a remote query is harmless and
useful (e.g. the server-side pipeline can read state).

## Configuration

The filter is on by default. Set it per instance:

```json
{
  "media": {
    "validate_source": true
  }
}
```

- `validate_source: true` (default), only act on the local/`"default"` session.
  Correct for a **server-side** `ovos-media` and for any satellite whose sessions
  are NAT'd to `"default"` by hivemind-core.
- `validate_source: false`, act on **every** session. Use this on a satellite
  that is **not** getting default-NAT'd sessions, so its embedded `ovos-media`
  still executes the playback meant for it.

`MediaService(validate_source=...)` (and `OCPMediaPlayer(validate_source=...)`)
override the config value programmatically; when left unset the config is read.

## Why not a player per session?

`ovos-media` deliberately stays a single global player. Per-session playback
*state* and routing already live in the OCP pipeline (server side) and in each
device's own `ovos-media` (client side). Duplicating that as N virtual players
inside one daemon would also collide on the shared, session-less feedback events
that backend plugins emit (`ovos.common_play.media.state` / `.track.state` /
`.player.state` are emitted as bare messages by `ovos-plugin-manager`'s
`MediaBackend`), so per-session backend feedback cannot be disambiguated without
changing the plugin contract. The single-player + default-session-filter model
keeps the daemon simple and routes correctly in a split.

## See also

- [Architecture](architecture.md), the daemon's layers and bus API
- [Glossary](glossary.md), session, OCP, provider/backend/extractor
- [Configuration](configuration.md), the full `media` config block

---
[← Architecture](architecture.md) · [Home](../README.md) · [Media providers →](media-providers.md)

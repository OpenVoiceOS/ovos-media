# Getting started

`ovos-media` is the OCP-native media daemon for OpenVoiceOS. It plays the
audio/video/web half of the media stack while `ovos-audio` continues to handle
TTS, the two run side by side. Apache-2.0, Python 3.10+.

---

## Installation

### From PyPI

```bash
pip install ovos-media
```

Using `uv`:

```bash
uv pip install ovos-media
```

### Install at least one playback backend

`ovos-media` does not play media itself, it delegates to a backend plugin.
Install at least an audio backend so something can be heard:

```bash
pip install ovos-media-plugin-vlc        # or -mplayer / -simple / -spotify / -chromecast
```

`ovos-media-plugin-vlc` wraps `python-vlc`, which needs the native `libvlc`
system library. `pip install` alone will not error if `libvlc` is missing —
the plugin just silently fails to load, and no audio backend is available.
Install the system package too, e.g. `apt install vlc` (Debian/Ubuntu) or
`libvlc-dev`/`libvlc5` on distros that split the runtime from the dev headers.

For a headless box, `ovos-media-plugin-ffplay` is a good default: it only
needs `ffmpeg`/`ffplay` on the `PATH`, which most systems already have or can
install with a single package (`apt install ffmpeg`), with no separate native
binding to install.

See [Backends](backends.md) for the full list of audio/video/web backends.

### Development / editable install

```bash
git clone https://github.com/OpenVoiceOS/ovos-media
uv pip install -e ovos-media
```

---

## Enable it

`ovos-media` takes over media playback; `ovos-audio` keeps handling TTS. Turn off
the legacy audio service so the two don't both try to play media:

```json
{
  "enable_old_audioservice": false
}
```

Place this in `~/.config/mycroft/mycroft.conf` (or `~/.config/ovos/ovos.conf`).
Keep `ovos-audio` running for TTS, and start `ovos-media` as its own process.

A complete migration walkthrough, including configuration mapping from the old
audio service, is in the [migration guide](migration-guide.md).

Note the naming split when you list backends in config (see
[Backends](backends.md)): the **pip package** for OCP audio/video plugins uses
`-plugin-` (e.g. `ovos-media-plugin-vlc`), but the **`module` name you write
in config** uses `-audio-plugin-` / `-video-plugin-` (e.g.
`ovos-media-audio-plugin-vlc` for audio, `ovos-media-video-plugin-vlc` for
video). Installing the pip package is not enough on its own — the `module`
key in `media.audio_players` / `media.video_players` must match the
entry-point name, not the pip package name.

### Testing in isolation

`ovos-config` resolves `mycroft.conf`/`ovos.conf` through the standard XDG
base directories (`ovos_config/locations.py`, via
`ovos_utils.xdg_utils.xdg_config_home`), so setting `XDG_CONFIG_HOME` to a
throwaway directory before starting `ovos-media` runs it against an isolated
config, without touching your real user config:

```bash
export XDG_CONFIG_HOME=/tmp/ovos-media-test/config
mkdir -p "$XDG_CONFIG_HOME/mycroft"
echo '{"enable_old_audioservice": false}' > "$XDG_CONFIG_HOME/mycroft/mycroft.conf"
ovos-media
```

Model and plugin caches follow `XDG_CACHE_HOME` separately (same
`ovos_utils.xdg_utils` module, `xdg_cache_home`). Overriding only
`XDG_CONFIG_HOME` isolates configuration but not caches — set both env vars
if you need a fully isolated test environment.

---

## Run the daemon

The package installs an `ovos-media` console entry point:

```bash
ovos-media
```

The process connects to the MessageBus, initialises `OCPMediaPlayer`, and signals
readiness via the `ProcessStatus` machinery (`ovos_media/__main__.py`).

### Running it embedded

```python
from ovos_utils import wait_for_exit_signal
from ovos_media.service import MediaService

svc = MediaService()   # connects to the bus, builds OCPMediaPlayer
svc.daemon = True
svc.start()            # starts the daemon thread; run() marks ProcessStatus READY
wait_for_exit_signal()
svc.shutdown()
```

`MediaService.__init__` reads the `media` section of `mycroft.conf` / `ovos.conf`,
opens (or reuses) a `MessageBusClient`, creates an `OCPMediaPlayer`, and wires
up the bus handlers. The thread's `run()` emits the `READY` status signal.
This is exactly what the `ovos-media` console entry point
(`ovos_media/__main__.py`) does.

---

## First playback

With a full OVOS install, ask OVOS to play something and the OCP pipeline
(in `ovos-core`) classifies the request, queries the installed
[media providers](media-providers.md), and routes the winning result to
`ovos-media`:

> "play some jazz"

```python
from ovos_bus_client import MessageBusClient, Message

bus = MessageBusClient()
bus.run_in_thread()

bus.emit(Message("recognizer_loop:utterance",
                 {"utterances": ["play some jazz"], "lang": "en-us"}))
```

This needs `ovos-core` (or another OCP pipeline) running to turn the utterance
into a play request. With only `ovos-media` + `ovos-messagebus` + a backend
(no `ovos-core`, no skills), there is no pipeline to classify "play some jazz",
so send the play request directly instead. `ovos-media` listens for
`ovos.common_play.play` on the bus and expects a `media` dict plus an
optional `playlist` list:

```python
from ovos_bus_client import MessageBusClient, Message

bus = MessageBusClient()
bus.run_in_thread()

track = {
    "uri": "https://example.com/song.mp3",
    "title": "Some Jazz",
    "media_type": 2,     # MediaType.MUSIC
    "playback": 2        # PlaybackType.AUDIO
}

bus.emit(Message("ovos.common_play.play", {
    "media": track,
    "playlist": [track]
}))
```

`media` is required (`handle_play_request` in `ovos_media/player.py` logs a
warning and ignores the message if it is missing). `media_type` and
`playback` are the integer values of the `ovos_utils.ocp.MediaType` and
`ovos_utils.ocp.PlaybackType` enums; common ones are `MediaType.AUDIO` (1),
`MediaType.MUSIC` (2), `MediaType.VIDEO` (3), `MediaType.PODCAST` (6), and
`PlaybackType.AUDIO` (2), `PlaybackType.VIDEO` (1), `PlaybackType.AUDIO_SERVICE`
(3). See `ovos_utils/ocp.py` for the full list. Values can also be sent as the
enum's string name in most OCP tooling, but the handler here reads them
straight off `message.data`, so plain integers (or the raw enum) are the
safest choice.

If `playlist` is omitted, `ovos-media` builds a single-track playlist
containing only `media` (`playlist = message.data.get("playlist") or [media]`
in `handle_play_request`). Either way, `play_media` then calls
`self.playlist.replace(playlist)`, which **replaces** the player's current
playlist outright. This means an `ovos.common_play.play` message always
overwrites whatever was set by a previous `ovos.common_play.playlist.set`
message, whether or not you include a `playlist` key — next/previous track
navigation after a bare `play` (no `playlist` key) will only ever have the
one track to work with. To play a track as part of a larger playlist, include
the full track list under `playlist` in the same `ovos.common_play.play`
message.

To confirm the daemon is live, ping it:

```python
bus.emit(Message("ovos.common_play.ping"))
# expect an "ovos.common_play.pong" reply
```

(handled by `MediaService.handle_ping`).

### Querying status

Every reply on the bus uses the `.response` suffix convention: the reply to
`<msg_type>` is emitted as `<msg_type>.response` (see `Message.response` in
`ovos-bus-client`). To query full player status, send
`ovos.common_play.status` and wait for `ovos.common_play.status.response`
(handled by `OCPMediaPlayer.handle_status` in `ovos_media/player.py`):

```python
from ovos_bus_client import MessageBusClient, Message

bus = MessageBusClient()
bus.run_in_thread()

reply = bus.wait_for_response(Message("ovos.common_play.status"),
                               reply_type="ovos.common_play.status.response",
                               timeout=5)
print(reply.data)
# {'playback_type': ..., 'media_type': ..., 'player_state': ...,
#  'loop_state': ..., 'media_state': ..., 'shuffle': ...,
#  'playlist_position': ..., 'playlist_size': ..., 'title': ...,
#  'artist': ..., 'image': ...}
```

Other single-value queries follow the same pattern, for example
`ovos.common_play.track_info` (replies on
`ovos.common_play.track_info.response` with the current track's metadata),
`ovos.common_play.get_track_length` / `ovos.common_play.get_track_position`,
and `ovos.common_play.list_backends`.

---

## Where things live next

- **Find media**, install [media providers](media-providers.md)
  (`opm.media.provider`) to give OVOS catalogs to search.
- **Play media**, install [backends](backends.md)
  (`opm.media.audio` / `.video` / `.web`) and pick preferences in
  [configuration](configuration.md).
- **Control from the desktop**, enable [MPRIS](mpris.md) to drive playback from
  `playerctl`, KDE Connect, or the GNOME media widget.

---

## Runtime dependencies

| Package | Purpose |
| :--- | :--- |
| `ovos-utils` | OCP enumerations, MessageBus helpers, process utilities |
| `ovos-bus-client` | WebSocket MessageBus client |
| `ovos-config` | Configuration loader (`mycroft.conf` / `ovos.conf`) |
| `ovos-plugin-manager` | Backend and media-provider plugin discovery via entry points |
| `ovos-workshop` | `OVOSCommonPlaybackSkill` base used by the built-in liked-songs catalog, plus OCP decorators |
| `json-database` | Persistent liked-songs storage (`JsonStorageXDG`) |
| `dbus-next` | Async D-Bus implementation used by the MPRIS exporter |

The `extras` install (`pip install ovos-media[extras]`) adds stream-extractor
plugins for YouTube, M3U, RSS, local files, and news feeds.

---

## Supported Python versions

Python 3.10 and above, enforced by `requires-python = ">=3.10"` in
`pyproject.toml`.

---
[← Glossary](glossary.md) · [Home](../README.md) · [Architecture →](architecture.md)

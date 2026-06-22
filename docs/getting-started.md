# Getting started

`ovos-media` is the OCP-native media daemon for OpenVoiceOS. It plays the
audio/video/web half of the media stack while `ovos-audio` continues to handle
TTS — the two run side by side. Apache-2.0, Python 3.10+.

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

`ovos-media` does not play media itself — it delegates to a backend plugin.
Install at least an audio backend so something can be heard:

```bash
pip install ovos-media-plugin-vlc        # or -mplayer / -simple / -spotify / -chromecast
```

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

A complete migration walkthrough — including configuration mapping from the old
audio service — is in the [migration guide](migration-guide.md).

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
from ovos_media.service import MediaService

svc = MediaService()
svc.start()   # starts the daemon thread
svc.run()     # marks ProcessStatus READY; returns
```

`MediaService.__init__` reads the `media` section of `mycroft.conf` / `ovos.conf`,
creates an `OCPMediaPlayer`, and wires up the bus handlers. Calling `run()` emits
the `READY` status signal.

---

## First playback

Once `ovos-media` is running and at least one backend is installed, ask OVOS to
play something — the OCP pipeline classifies the request, queries the installed
[media providers](media-providers.md), and routes the winning result here:

> "play some jazz"

To test over the bus directly:

```python
from ovos_bus_client import MessageBusClient, Message

bus = MessageBusClient()
bus.run_in_thread()

bus.emit(Message("recognizer_loop:utterance",
                 {"utterances": ["play some jazz"], "lang": "en-us"}))
```

To confirm the daemon is live, ping it:

```python
bus.emit(Message("ovos.common_play.ping"))
# expect an "ovos.common_play.pong" reply
```

`MediaService.handle_ping` — `ovos_media/service.py`.

---

## Where things live next

- **Find media** — install [media providers](media-providers.md)
  (`opm.media.provider`) to give OVOS catalogs to search.
- **Play media** — install [backends](backends.md)
  (`opm.media.audio` / `.video` / `.web`) and pick preferences in
  [configuration](configuration.md).
- **Control from the desktop** — enable [MPRIS](mpris.md) to drive playback from
  `playerctl`, KDE Connect, or the GNOME media widget.

---

## Runtime dependencies

| Package | Purpose |
| :--- | :--- |
| `ovos-utils` | OCP enumerations, MessageBus helpers, process utilities |
| `ovos-bus-client` | WebSocket MessageBus client |
| `ovos-config` | Configuration loader (`mycroft.conf` / `ovos.conf`) |
| `ovos-plugin-manager` | Backend and media-provider plugin discovery via entry points |
| `ovos-workshop` | Base application classes |
| `ovos-gui-api-client` | GUI interface (`GUIInterface`) for the media player screen |
| `json-database` | Persistent liked-songs storage (`JsonStorageXDG`) |
| `dbus-next` | Async D-Bus implementation used by the MPRIS exporter |

The `extras` install (`pip install ovos-media[extras]`) adds stream-extractor
plugins for YouTube, M3U, RSS, local files, and news feeds.

---

## Supported Python versions

Python 3.10 and above, enforced by `requires-python = ">=3.10"` in
`pyproject.toml`.

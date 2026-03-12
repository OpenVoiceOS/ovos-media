# Getting Started with ovos-media

`ovos-media` is the OCP-native media service for OpenVoiceOS. It replaces the
audio/video/web playback half of `ovos-audio` while `ovos-audio` continues to
handle TTS output. Version 0.0.1, Apache-2.0, Python 3.10+.

---

## Installation

### From PyPI

```bash
pip install ovos-media
```

Using `uv` (recommended in the OpenVoiceOS workspace):

```bash
uv pip install ovos-media
```

### Development / Editable Install

```bash
git clone https://github.com/OpenVoiceOS/ovos-media
uv pip install -e ovos-media
```

### Note on ovos-audio

`ovos-media` takes over OCP/media playback. `ovos-audio` must still be running
alongside it for TTS output — the two services are complementary, not
mutually exclusive.

---

## Quick Start

### Launching the service from the command line

The package installs an `ovos-media` console entry point (`ovos_media/__main__.py`):

```bash
ovos-media
```

The process connects to the MessageBus, initialises `OCPMediaPlayer`, and
signals readiness via the `ProcessStatus` machinery.

### Launching as a Python service

```python
from ovos_media.service import MediaService

svc = MediaService()
svc.start()   # starts the daemon thread
svc.run()     # sets ProcessStatus to READY; returns immediately
```

`MediaService.__init__` — `ovos_media/service.py:36`
`MediaService.run` — `ovos_media/service.py:89`

The constructor reads the `media` section of `mycroft.conf` / `ovos.conf`,
creates an `OCPMediaPlayer`, and wires up legacy-compat handlers before
returning. Calling `run()` emits the `READY` status signal.

---

## Migrating from ovos-audio

Historically `ovos-audio` bundled both TTS delivery and OCP media playback.
`ovos-media` extracts the media playback side into its own process.

To migrate, disable the old audio service's OCP subsystem:

```json
{
  "enable_old_audioservice": false,
  "disable_ocp": true
}
```

Place this in `~/.config/mycroft/mycroft.conf` (or `~/.config/ovos/ovos.conf`).
Keep `ovos-audio` running; it will continue to handle TTS. Start `ovos-media`
separately.

`MediaService.__init__` — `ovos_media/service.py:36`

---

## First Play — Testing Playback

After `ovos-media` is running and OCP skills are loaded, send a test utterance
via the MessageBus:

```python
from ovos_bus_client import MessageBusClient, Message

bus = MessageBusClient()
bus.run_in_thread()

bus.emit(Message("recognizer_loop:utterance",
                 {"utterances": ["play some jazz"], "lang": "en-us"}))
```

The OCP pipeline will search registered skills, pick the best result, and hand
it to the appropriate backend. You can also use a voice interface: say "play
jazz" to OVOS after skills are loaded.

To verify that `ovos-media` is responding to the bus, ping it:

```python
bus.emit(Message("ovos.common_play.ping"))
# expect an "ovos.common_play.pong" reply
```

`MediaService.handle_ping` — `ovos_media/service.py:69`

---

## Runtime Dependencies

| Package | Purpose |
| :--- | :--- |
| `ovos-workshop` | Base skill/application classes (`OVOSAbstractApplication`, `OVOSCommonPlaybackSkill`) |
| `ovos-utils` | OCP enumerations, MessageBus helpers, process utilities |
| `ovos-bus-client` | WebSocket MessageBus client |
| `ovos-config` | Configuration loader (`~/.config/mycroft/mycroft.conf`) |
| `ovos-plugin-manager` | OCP audio/video/web backend discovery via entry points |
| `ovos-gui-api-client` | GUI interface (`GUIInterface`) for the media player screen |
| `json-database` | Persistent liked-songs playlist storage (`JsonStorageXDG`) |
| `dbus-next` | Async D-Bus implementation used by the MPRIS exporter |

Optional extras (`pip install ovos-media[extras]`) add stream-extractor plugins
for YouTube, M3U, RSS, local files, and news feeds.

---

## Supported Python Versions

Python 3.10 and above. The minimum is enforced by `requires-python = ">=3.10"`
in `pyproject.toml`.

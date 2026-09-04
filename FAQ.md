# FAQ — `ovos-media`

## What is `ovos-media`?

`ovos-media` is the OCP-native audio, video, and web media service for OpenVoiceOS. It handles all media playback and replaces the legacy media handling inside `ovos-audio`. The `ovos-audio` process continues to run alongside it for TTS (text-to-speech) output.

## What is OCP?

OCP stands for OpenVoiceOS Common Play. It is the voice-driven media pipeline:
1. A user says "play jazz".
2. The OCP pipeline plugin classifies the utterance as a media query.
3. Registered OCP skills search their backends and return `MediaEntry` lists.
4. `ovos-media` receives the winning entry, picks the right backend, and starts playback.

See [ocp-skills.md](docs/ocp-skills.md) for the full flow.

## How do I install it?

```bash
pip install ovos-media
# or with uv (recommended in the OpenVoiceOS workspace):
uv pip install ovos-media
```

For development:

```bash
git clone https://github.com/OpenVoiceOS/ovos-media
uv pip install -e ovos-media/
```

## How do I migrate from ovos-audio to ovos-media?

Keep `ovos-audio` running (it handles TTS). Add these keys to `mycroft.conf`:

```json
{
  "enable_old_audioservice": false,
  "disable_ocp": true
}
```

Then install and start `ovos-media`. See [getting-started.md](docs/getting-started.md) and [docs/06-ovos-audio-migration.md](docs/06-ovos-audio-migration.md) for details.

## Where is the configuration?

In `~/.config/mycroft/mycroft.conf` or `~/.config/ovos/ovos.conf`, under the `"media"` key.

```json
{
  "media": {
    "preferred_audio_services": ["vlc"],
    "enable_mpris": true
  }
}
```

Full reference: [configuration.md](docs/configuration.md).

## How do I pick a specific audio/video backend?

```json
{
  "media": {
    "preferred_audio_services": ["vlc", "mpv"],
    "preferred_video_services": ["mpv"],
    "preferred_web_services": ["browser"]
  }
}
```

`OCPMediaPlayer._resolve_preferred_service` — `ovos_media/player.py` — tries each name in order and falls back to any available plugin.

## What audio backends are available?

Any plugin installed from the `opm.plugin.audio` entry-point group. Common ones:

- `ovos-audio-plugin-vlc`
- `ovos-audio-plugin-mpv`
- `ovos-audio-plugin-simple` (uses `paplay`/`aplay`)

See [backends.md](docs/backends.md) for more.

## How does a GUI stay in sync?

`ovos-media` has no GUI client in-process. Every playback state change is
broadcast on the bus (`ovos.common_play.player.state`,
`ovos.common_play.media.state`, `ovos.common_play.track.state`, and a full
snapshot on `ovos.common_play.status.response`). A UI subscribes to those
broadcasts like any other bus client — `ovos-webui` does exactly this.
Individual video/web backend plugins render their own content directly and
report their state over the same bus.

See [architecture.md](docs/architecture.md) for the full picture.

## How do I enable MPRIS?

```json
{
  "media": {
    "enable_mpris": true
  }
}
```

OCP will appear as `org.mpris.MediaPlayer2.OCP` on the D-Bus session bus. Any MPRIS controller (KDE Connect, Plasma widget, `playerctl`) can then control it.

```bash
playerctl --player=OCP status
playerctl --player=OCP next
```

See [mpris.md](docs/mpris.md).

## How do I make ovos-media pause when another media player starts?

```json
{
  "media": {
    "enable_mpris": true,
    "manage_external_players": true
  }
}
```

`OcpMprisExporter` — `ovos_media/mpris.py` — scans the D-Bus session bus and pauses OCP when an external MPRIS player starts playing.

## How do I ignore specific MPRIS players?

```json
{
  "media": {
    "ignored_players": [
      "org.mpris.MediaPlayer2.OCP",
      "org.mpris.MediaPlayer2.plasma-browser-integration",
      "org.mpris.MediaPlayer2.firefox"
    ]
  }
}
```

## How do I control JavaScript and URL changes in the web player?

Per-track: add `javascript_can_open_windows: true` or `allow_url_change: true`
to the track's infocard metadata.

Global fallback via config:

```json
{
  "media": {
    "gui": {
      "javascript_can_open_windows": false,
      "allow_url_change": false
    }
  }
}
```

## What Python versions are supported?

Python 3.10 and above. See `pyproject.toml`.

## How do I run the tests?

```bash
uv run pytest test/ -v --cov=ovos_media --cov-report=term-missing
```

Unit tests: `test/unittests/` (291 tests).
Integration tests: `test/end2end/` (13 tests via `MediaServiceHarness`).

## How do I write an OCP skill?

OCP skills subclass `OVOSCommonPlaybackSkill` from `ovos-workshop` and implement
`search_ocp(phrase, media_type)`. They return a list of `MediaEntry` objects.

Test them with `ovoscope.ocp.OCPTest` — see [ocp-skills.md](docs/ocp-skills.md) and [ovoscope docs](../ovoscope/docs/ocp.md).

## How do I report bugs?

Open an issue on GitHub targeting the `dev` branch:
https://github.com/OpenVoiceOS/ovos-media/issues

## How do I contribute?

1. Fork the repository and create a feature branch from `dev`.
2. Write tests for your changes (`test/unittests/` or `test/end2end/`).
3. Open a PR targeting `dev`. Ensure CI passes.
4. All AI-generated changes must be logged in `MAINTENANCE_REPORT.md`.

## What is the relationship between ovos-media and ovos-audio?

| Responsibility | ovos-audio | ovos-media |
|---|---|---|
| TTS synthesis and playback | YES | NO |
| OCP media playback (audio/video/web) | Legacy only | YES |
| `mycroft.audio.service.*` bus API | YES | NO |
| Volume ducking during TTS | YES | YES |
| MPRIS integration | NO | YES |
| Shuffle / repeat / liked songs | NO | YES |

See [docs/06-ovos-audio-migration.md](docs/06-ovos-audio-migration.md).

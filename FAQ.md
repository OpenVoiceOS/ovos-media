
# FAQ — `ovos-media`

## What is `ovos-media`?
`ovos-media` is the OCP-native audio/video/web media service for OpenVoiceOS. It replaces the legacy `AudioService` inside `ovos-audio` for all media playback, while `ovos-audio` retains TTS output.

## How do I install it?
```bash
uv pip install ovos-media
```
Or for development:
```bash
uv pip install -e ovos-media/
```

## How do I migrate from ovos-audio to ovos-media?
Set these keys in `mycroft.conf` (or `ovos.conf`):
```json
{
  "enable_old_audioservice": false,
  "disable_ocp": true
}
```
Then install and start `ovos-media`. The `ovos-audio` process should still run for TTS.

## How do I configure the preferred audio/video/web backend?
Add to the `media` section of your config:
```json
{
  "media": {
    "preferred_audio_services": ["vlc"],
    "preferred_video_services": ["mpv"],
    "preferred_web_services": ["browser"]
  }
}
```
The player resolves these in order — `OCPMediaPlayer._resolve_preferred_service` — `ovos_media/player.py`.

## How do I control external MPRIS players?
Set `manage_external_players: true` in the `media` config. The MPRIS daemon
(`MprisPlayerCtl` — `ovos_media/mpris.py`) detects external players on D-Bus
and stops internal OCP playback when they start.

To customise ignored players:
```json
{
  "media": {
    "ignored_players": ["org.mpris.MediaPlayer2.OCP"]
  }
}
```

## How do I configure the MPRIS poll interval?
```json
{
  "media": {
    "mpris_poll_interval": 2
  }
}
```
Default is 1 second. See `MprisPlayerCtl.event_loop` — `ovos_media/mpris.py`.

## How do I allow JavaScript / URL changes in the web player?
Per-track: add `javascript_can_open_windows: true` or `allow_url_change: true`
to the track's infocard metadata.

Global fallback (config):
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
Resolved in `OCPGUIInterface.update_current_track` — `ovos_media/gui.py`.

## Where do I report bugs?
Open an issue on the GitHub repository targeting the `dev` branch.

## How do I run tests?
```bash
uv run pytest test/ -v --cov=ovos_media --cov-report=term-missing
```

## How do I contribute?
1. Fork the repository and create a feature branch from `dev`.
2. Write tests for your changes.
3. Open a PR targeting the `dev` branch.
4. Ensure CI passes before requesting review.

## What Python versions are supported?
Python 3.10 and above. See `pyproject.toml`.

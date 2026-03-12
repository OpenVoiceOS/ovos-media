
# Quick Facts — `ovos-media`

OCP-native audio/video/web media service for OpenVoiceOS.

| Feature | Details |
|---------|---------|
| Package Name | `ovos-media` |
| Version | `0.0.1` (pre-release; targeting `0.0.1` stable) |
| License | Apache-2.0 |
| Repository | [https://github.com/OpenVoiceOS/ovos-media](https://github.com/OpenVoiceOS/ovos-media) |
| Python Support | >=3.10 |

## Key Classes

| Class | File | Description |
|-------|------|-------------|
| `MediaService` | `ovos_media/service.py` | Main service thread; connects bus, owns `OCPMediaPlayer` |
| `OCPMediaPlayer` | `ovos_media/player.py` | Virtual media player managing playback across all backends |
| `NowPlaying` | `ovos_media/player.py` | Tracks currently-playing media via bus events |
| `OCPMediaCatalog` | `ovos_media/player.py` | Manages liked songs and OCP skill card announcements |
| `OCPGUIInterface` | `ovos_media/gui.py` | Qt5 GUI state manager with 6 display states |
| `MprisPlayerCtl` | `ovos_media/mpris.py` | D-Bus MPRIS daemon; integrates with external media players |
| `BaseMediaService` | `ovos_media/media_backends/base.py` | Plugin-loading base class for audio/video/web backends |
| `AudioService` | `ovos_media/media_backends/audio.py` | Audio playback backend |
| `VideoService` | `ovos_media/media_backends/video.py` | Video playback backend |
| `WebService` | `ovos_media/media_backends/web.py` | Web/browser playback backend |

## Entry Points

### Scripts
- `ovos-media`: `ovos_media.__main__:main`

## Configuration Keys (`mycroft.conf` → `media` section)

| Key | Default | Description |
|-----|---------|-------------|
| `enable_mpris` | `false` | Advertise OCP over MPRIS and manage external players |
| `manage_external_players` | `false` | Stop OCP when an external MPRIS player starts |
| `ignored_players` | `[OCP, plasma-browser]` | MPRIS player names to ignore |
| `mpris_poll_interval` | `1` | Seconds between MPRIS player scans |
| `preferred_audio_services` | `[]` | Ordered list of preferred audio backend names |
| `preferred_video_services` | `[]` | Ordered list of preferred video backend names |
| `preferred_web_services` | `[]` | Ordered list of preferred web backend names |
| `gui.javascript_can_open_windows` | `false` | Global web player JS popup policy |
| `gui.allow_url_change` | `false` | Global web player URL-change policy |

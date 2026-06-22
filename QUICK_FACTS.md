# Quick Facts — `ovos-media`

OCP-native audio/video/web media service for OpenVoiceOS.

| Feature | Details |
|---------|---------|
| Package name | `ovos-media` |
| Version | `0.0.1` |
| License | Apache-2.0 |
| Repository | https://github.com/OpenVoiceOS/ovos-media |
| Python support | >=3.10 |
| Entry point | `ovos-media` → `ovos_media.__main__:main` |

## Key Classes

| Class | File | Description |
|-------|------|-------------|
| `MediaService` | `ovos_media/service.py` | Main service thread; connects to the MessageBus, owns `OCPMediaPlayer` |
| `OCPMediaPlayer` | `ovos_media/player.py` | Virtual media player; state machine, backend dispatch, GUI updates |
| `NowPlaying` | `ovos_media/player.py` | Tracks currently-playing media; `as_dict` property returns metadata dict |
| `OCPMediaCatalog` | `ovos_media/player.py` | Manages liked songs and OCP skill card announcements |
| `OcpMprisExporter` | `ovos_media/mpris.py` | D-Bus MPRIS daemon; Role A = expose OCP, Role B = manage external players |
| `MprisPlayerCtl` | `ovos_media/mpris.py` | Backward-compat alias for `OcpMprisExporter` |
| `BaseMediaService` | `ovos_media/media_backends/base.py` | OPM plugin-loading base for audio/video/web backends |
| `AudioService` | `ovos_media/media_backends/audio.py` | Audio playback backend (OPM group: `opm.plugin.audio`) |
| `VideoService` | `ovos_media/media_backends/video.py` | Video playback backend |
| `WebService` | `ovos_media/media_backends/web.py` | Web/browser playback backend |
| `LegacyAudioServiceCompat` | `ovos_media/legacy_api.py` | Handles `mycroft.audio.service.*` bus API for backward compat |

## Entry Points

| Name | Target |
|------|--------|
| `ovos-media` (script) | `ovos_media.__main__:main` |

## Key Bus Messages Handled

| Message type | Handler | Description |
|---|---|---|
| `ovos.common_play.ping` | `MediaService.handle_ping` | Replies with `ovos.common_play.pong` |
| `ovos.common_play.home` | `MediaService.handle_home` | Refreshes GUI |
| `ovos.common_play.search.start` | `MediaService.handle_search_start` | Shows loading state in GUI |
| `opm.audio.query` | `MediaService.handle_opm_audio_query` | Reports installed backends |
| `ovos.common_play.pause` | `OCPMediaPlayer.handle_pause_request` | Pauses playback |
| `ovos.common_play.resume` | `OCPMediaPlayer.handle_resume_request` | Resumes playback |
| `ovos.common_play.stop` | `OCPMediaPlayer.handle_stop_request` | Stops playback |
| `ovos.common_play.next` | `OCPMediaPlayer.handle_next_request` | Next track |
| `ovos.common_play.prev` | `OCPMediaPlayer.handle_prev_request` | Previous track |
| `ovos.common_play.seek` | `OCPMediaPlayer.handle_seek_request` | Seek by seconds or to position |
| `recognizer_loop:audio_output_start` | `OCPMediaPlayer` | Duck volume during TTS |
| `mycroft.stop` | `OCPMediaPlayer` | Global stop |

## Configuration Keys (`mycroft.conf` → `"media"` section)

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enable_mpris` | bool | `false` | Expose OCP as MPRIS D-Bus player |
| `manage_external_players` | bool | `false` | Pause OCP when external MPRIS player starts |
| `ignored_players` | list | `[OCP, plasma-browser]` | D-Bus player names to ignore |
| `mpris_poll_interval` | int | `1` | Seconds between external player scans |
| `preferred_audio_services` | list | `[]` | Ordered preferred audio backend plugin names |
| `preferred_video_services` | list | `[]` | Ordered preferred video backend plugin names |
| `preferred_web_services` | list | `[]` | Ordered preferred web backend plugin names |
| `native_sources` | list | `["debug_cli","audio"]` | Trusted bus message sources |
| `gui.javascript_can_open_windows` | bool | `false` | Web player JS popup policy |
| `gui.allow_url_change` | bool | `false` | Web player URL-change policy |

## Test Coverage (2026-03-12)

| File | Cover |
|------|-------|
| `ovos_media/__main__.py` | 94% |
| `ovos_media/legacy_api.py` | 87% |
| `ovos_media/media_backends/base.py` | 89% |
| `ovos_media/media_backends/{audio,video,web}.py` | 100% |
| `ovos_media/mpris.py` | 37% |
| `ovos_media/player.py` | 46% |
| `ovos_media/service.py` | 89% |
| `ovos_media/utils.py` | 100% |
| **TOTAL** | **53%** |

291 unit tests + 13 integration tests in `test/end2end/`.

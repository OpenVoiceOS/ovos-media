# Backend Plugins

This document describes the backend plugin system in `ovos-media` (version 0.0.1, Apache-2.0, Python 3.10+). Every behavioural claim cites a source location as `ClassName.method — path/to/file.py:LINE`.

---

## Overview

`ovos-media` does not implement audio or video playback itself. Actual media playback is delegated to backend plugins discovered at runtime via `ovos-plugin-manager` (OPM). There are three backend types, each managed by a dedicated `BaseMediaService` subclass:

| Backend type | Manager class | Content type |
| :--- | :--- | :--- |
| Audio | `AudioService` — `ovos_media/media_backends/audio.py:8` | Audio streams and files |
| Video | `VideoService` — `ovos_media/media_backends/video.py:8` | Video streams and files |
| Web | `WebService` — `ovos_media/media_backends/web.py:8` | Web views (`MediaType.WEBVIEW`) |

All three classes inherit from `BaseMediaService` — `ovos_media/media_backends/base.py:17`, which provides shared plugin loading, bus event registration, and backend-selection logic.

---

## Plugin Discovery

Plugin discovery runs at startup in `BaseMediaService.load_services` — `ovos_media/media_backends/base.py:75`.

The method:

1. Calls the injected `plugin_loader` callable to obtain a dict of `{plugin_name: plugin_class}` for all installed OPM plugins of that type.
2. Iterates the `media.<namespace>_players` configuration block (e.g. `media.audio_players` for audio). Each entry names a plugin module and optional aliases. Plugins absent from the installed set are skipped with an error log; plugins with `active: false` are skipped with an info log.
3. Instantiates each accepted plugin and appends it to either the `local` or `remote` list depending on whether the instance is a subclass of `RemoteAudioPlayerBackend`, `RemoteVideoPlayerBackend`, or `RemoteWebPlayerBackend` — `ovos_media/media_backends/base.py:97`.
4. Concatenates `local + remote` into `self.services` — `ovos_media/media_backends/base.py:106`, ensuring local backends are checked before remote ones.
5. Registers the `ovos.common_play.media.state` bus event and the per-namespace service control events — `ovos_media/media_backends/base.py:113`.
6. Sets a `MonotonicEvent` (`self._loaded`) to signal that loading is complete — `ovos_media/media_backends/base.py:127`.

### Preferred backend resolution

`OCPMediaPlayer._resolve_preferred_service` — `ovos_media/player.py:650` reads the preferred backend list from `AudioService.get_preferred_players` — `ovos_media/media_backends/audio.py:21` (or the equivalent for video/web), which returns the value of `config.get("preferred_audio_services")` / `preferred_video_services` / `preferred_web_services`. It then walks `media_service.services` looking for a backend whose `.name` or `.aliases` match any name in that list. The first match is returned as the preferred backend; if no match is found, `None` is returned and the default URI-type matching logic applies.

---

## Audio Backends

- **Class**: `AudioService` — `ovos_media/media_backends/audio.py:8`
- **OPM plugin loader**: `ovos_plugin_manager.ocp.find_ocp_audio_plugins`
- **Config key**: `media.audio_players`
- **Preferred service config key**: `media.preferred_audio_services`

Example well-known plugins: `ovos-audio-plugin-vlc`, `ovos-audio-plugin-mpv`, `ovos-audio-plugin-simple`.

Audio backends support: play, pause, resume, stop, seek, volume ducking, track position query, and track length query. URI type support is plugin-specific (e.g. `http`, `https`, `file`).

---

## Video Backends

- **Class**: `VideoService` — `ovos_media/media_backends/video.py:8`
- **OPM plugin loader**: `ovos_plugin_manager.ocp.find_ocp_video_plugins`
- **Config key**: `media.video_players`
- **Preferred service config key**: `media.preferred_video_services`

Video backends are selected when `now_playing.playback == PlaybackType.VIDEO` — `ovos_media/player.py:802`. They render video content; the GUI namespace used for display is managed by the backend plugin, not by the `"ovos.common_play"` GUIInterface.

---

## Web Backends

- **Class**: `WebService` — `ovos_media/media_backends/web.py:8`
- **OPM plugin loader**: `ovos_plugin_manager.ocp.find_ocp_web_plugins`
- **Config key**: `media.web_players`
- **Preferred service config key**: `media.preferred_web_services`

Web backends are selected when `now_playing.playback == PlaybackType.WEBVIEW` — `ovos_media/player.py:807`. They display arbitrary web URLs inside a GUI WebView component.

---

## BaseMediaService API

`BaseMediaService` — `ovos_media/media_backends/base.py:17` provides the following methods that backend manager classes inherit. Individual plugin instances (subclasses of `MediaBackend` from OPM) implement the actual playback logic.

### `available_backends()`

`BaseMediaService.available_backends — ovos_media/media_backends/base.py:43`

Returns a dict of `{backend_name: {"supported_uris": [...], "remote": bool}}` for every loaded service instance. Used by `MediaService.handle_opm_audio_query` — `ovos_media/service.py:92` to respond to OPM discovery queries.

### `play(uri, preferred_service=None)`

`BaseMediaService.play — ovos_media/media_backends/base.py:250`

Selects the appropriate backend for `uri` (by URI scheme prefix) and calls `backend.load_track(uri)`. Selection order:

1. `preferred_service` if it supports the URI scheme.
2. `self.current` (the previously active backend) if it supports the URI scheme.
3. The first entry in `self.services` that supports the URI scheme.

If no backend supports the URI, playback is skipped with a log message.

### `pause()` / `resume()` / `stop()`

`BaseMediaService.pause — ovos_media/media_backends/base.py:165`
`BaseMediaService.resume — ovos_media/media_backends/base.py:179`
`BaseMediaService.stop — ovos_media/media_backends/base.py:210`

Delegate to `self.current.pause()` / `resume()` / `stop()` and emit the corresponding OCP state events. `stop` only fires if at least 1 second has elapsed since playback started (guard against accidental immediate stop) — `ovos_media/media_backends/base.py:219`.

### `lower_volume()` / `restore_volume()`

`BaseMediaService.lower_volume — ovos_media/media_backends/base.py:227`
`BaseMediaService.restore_volume — ovos_media/media_backends/base.py:241`

Ducking hooks. `lower_volume` calls `self.current.lower_volume()` and sets `self.volume_is_low = True`. `restore_volume` calls `self.current.restore_volume()` and clears the flag. These are invoked by the `ovos.common_play.duck` / `ovos.common_play.unduck` bus events, which are also aliased to the legacy `recognizer_loop:audio_output_start` / `recognizer_loop:audio_output_end` events — `ovos_media/player.py:418`.

### `handle_media_state_change(message)`

`BaseMediaService.handle_media_state_change — ovos_media/media_backends/base.py:133`

Listens for `ovos.common_play.media.state`. When `MediaState.LOADED_MEDIA` is received and `self.current` is set, calls `self.current.play()` and emits the appropriate `ovos.common_play.track.state` (PLAYING_AUDIO, PLAYING_VIDEO, or PLAYING_WEBVIEW depending on `self.namespace`).

### `wait_for_load(timeout=180)`

`BaseMediaService.wait_for_load — ovos_media/media_backends/base.py:155`

Blocks until plugin loading completes or the timeout expires. Returns `True` on success.

---

## Playback Type Routing

`OCPMediaPlayer.play` — `ovos_media/player.py:763` checks `self.playback_type` (which reads `self.now_playing.playback`) and routes to the correct service:

```
PlaybackType.AUDIO   -> audio_service.play(uri, preferred_service)
PlaybackType.VIDEO   -> video_service.play(uri, preferred_service)
PlaybackType.WEBVIEW -> web_service.play(uri, preferred_service)
PlaybackType.SKILL   -> emit ovos.common_play.<skill_id>.play
```

The `preferred_service` argument in each case is resolved by `_resolve_preferred_service` — `ovos_media/player.py:650`.

Before routing, `OCPMediaPlayer.validate_stream` — `ovos_media/player.py:675` calls `NowPlaying.extract_stream()` to resolve any stream extractor identifiers (SEIs) into real URIs. If no GUI is connected or `force_audioservice` is set in config, the playback type is coerced to `PlaybackType.AUDIO` regardless of the original value — `ovos_media/player.py:693`.

---

## Writing a Custom Backend Plugin

Backend plugins are OPM `AudioBackend` (or `VideoBackend` / `WebBackend`) subclasses, not `BaseMediaService` subclasses. `BaseMediaService` is the manager; the plugin is the worker.

### Minimal `pyproject.toml` registration

```toml
[project.entry-points."opm.plugin.audio"]
my-audio-backend = "my_package.plugin:MyAudioBackend"
```

For video plugins use the group `"opm.plugin.video"`. For web plugins use `"opm.plugin.web"`.

### Minimal skeleton class

```python
from ovos_plugin_manager.templates.media import AudioBackend


class MyAudioBackend(AudioBackend):
    """Minimal custom audio backend for ovos-media."""

    def __init__(self, config: dict, bus) -> None:
        super().__init__(config, bus)

    @property
    def supported_uris(self) -> list[str]:
        """Return the URI schemes this backend can play."""
        return ["http", "https", "file"]

    def load_track(self, uri: str) -> None:
        """Load a URI; call self.ocp_start() when playback begins."""
        # TODO: implement media loading
        self.ocp_start()

    def play(self) -> None:
        """Begin playback of the loaded track."""

    def pause(self) -> None:
        """Pause playback."""

    def resume(self) -> None:
        """Resume paused playback."""

    def stop(self) -> bool:
        """Stop playback. Return True if something was stopped."""
        return True

    def lower_volume(self) -> None:
        """Reduce volume for TTS ducking."""

    def restore_volume(self) -> None:
        """Restore volume after TTS ducking."""

    def get_track_position(self) -> int:
        """Return current position in milliseconds."""
        return 0

    def set_track_position(self, milliseconds: int) -> None:
        """Seek to position in milliseconds."""

    def get_track_length(self) -> int:
        """Return total track duration in milliseconds."""
        return 0
```

The `BaseMediaService.load_services` call instantiates the plugin as `MyAudioBackend(plug_cfg, bus)` — `ovos_media/media_backends/base.py:94`. The `plug_cfg` dict comes from the `media.audio_players.<player_name>` configuration block.

# Backend plugins

Backends are the **playback** layer of `ovos-media`: single-track players that
take a resolved URI and play it. They are distinct from
[media providers](media-providers.md), which are the *search/catalog* layer that
finds what to play. The daemon picks a backend per track based on the playback
type and the configured preferences.

---

## Overview

`ovos-media` does not implement audio or video playback itself. Actual media playback is delegated to backend plugins discovered at runtime via `ovos-plugin-manager` (OPM). There are three backend types, each managed by a dedicated `BaseMediaService` subclass:

| Backend type | Manager class | Content type | Entry-point group |
| :--- | :--- | :--- | :--- |
| Audio | `AudioService` (`ovos_media/media_backends/audio.py`) | Audio streams and files | `opm.media.audio` |
| Video | `VideoService` (`ovos_media/media_backends/video.py`) | Video streams and files | `opm.media.video` |
| Web | `WebService` (`ovos_media/media_backends/web.py`) | Web views (`PlaybackType.WEBVIEW`) | `opm.media.web` |

All three classes inherit from `BaseMediaService` (`ovos_media/media_backends/base.py`), which provides shared plugin loading, bus event registration, and backend-selection logic.

### Dual-target plugins (ovos-media and legacy ovos-audio)

A backend plugin can serve **both** the new `ovos-media` daemon and the legacy
`ovos-audio` AudioService at once. Plugins do this by declaring two entry points
in their `pyproject.toml`:

```toml
[project.entry-points."opm.media.audio"]
ovos-media-audio-plugin-vlc = "ovos_media_plugin_vlc:VLCOCPAudioService"

[project.entry-points."mycroft.plugin.audioservice"]
ovos-vlc = "ovos_media_plugin_vlc:load_service"
```

- `opm.media.audio` (/`.video`/`.web`) is what `ovos-media` discovers and loads.
- `mycroft.plugin.audioservice` is the legacy group consumed by `ovos-audio`'s
  old AudioService.

The published plugins (`ovos-media-plugin-vlc`, `-mplayer`, `-simple`, `-spotify`,
`-chromecast`, `-ffplay`, `-mass`, `-mpris`) ship both so the same install works
on either stack. When writing your own backend you only need the
`opm.media.audio` group for `ovos-media`; add the legacy group only if you also
want to support unmigrated `ovos-audio` deployments.

---

## Plugin Discovery

Plugin discovery runs at startup in `BaseMediaService.load_services`.

The method:

1. Calls the injected `plugin_loader` callable to obtain a dict of `{plugin_name: plugin_class}` for all installed OPM plugins of that type.
2. Iterates the `media.<namespace>_players` configuration block (e.g. `media.audio_players` for audio). Each entry names a plugin `module` and optional `aliases`. Plugins absent from the installed set are skipped with an error log; plugins with `active: false` are skipped with an info log.
3. Instantiates each accepted plugin as `plugin_class(plug_cfg, bus)` and appends it to either the `local` or `remote` list depending on whether the instance is a `RemoteAudioPlayerBackend` / `RemoteVideoPlayerBackend` / `RemoteWebPlayerBackend`.
4. Concatenates `local + remote` into `self.services`, ensuring local backends are checked before remote ones.
5. Registers the `ovos.common_play.media.state` bus event and the per-namespace `ovos.<namespace>.service.*` control events.
6. Sets a `MonotonicEvent` (`self._loaded`) to signal that loading is complete.

If no backends load at all, an error is logged and a `MediaState.NO_MEDIA` event
is emitted, since all playback for that namespace would otherwise silently fail.

### Preferred backend resolution

`OCPMediaPlayer._resolve_preferred_service` reads the preferred backend list from `<Service>.get_preferred_players()`, which returns the value of `preferred_audio_services` / `preferred_video_services` / `preferred_web_services` (falling back to every loaded backend's name when no preference is configured). It then walks `media_service.services` looking for a backend whose `.name` or `.aliases` match any name in that list. The first match is returned as the preferred backend; if no match is found, `None` is returned and the default URI-type matching logic in `BaseMediaService.play` applies.

---

## Audio Backends

- **Class**: `AudioService` (`ovos_media/media_backends/audio.py`)
- **Entry-point group**: `opm.media.audio`
- **OPM plugin loader**: `ovos_plugin_manager.ocp.find_ocp_audio_plugins`
- **Config key**: `media.audio_players`
- **Preferred service config key**: `media.preferred_audio_services`

Example plugins (pip package → entry-point `module` name):
`ovos-media-plugin-vlc` (`ovos-media-audio-plugin-vlc`),
`ovos-media-plugin-mplayer` (`ovos-media-audio-plugin-mplayer`),
`ovos-media-plugin-simple` (`ovos-media-audio-plugin-cli`),
`ovos-media-plugin-ffplay` (`ovos-media-audio-plugin-ffplay`),
`ovos-media-plugin-spotify` (`ovos-media-audio-plugin-spotify`),
`ovos-media-plugin-chromecast` (remote, `ovos-media-audio-plugin-chromecast`),
`ovos-media-plugin-mass` (Music Assistant),
`ovos-media-plugin-mpris` (drives an external MPRIS player).

Audio backends support: play, pause, resume, stop, seek, volume ducking, track position query, and track length query. URI type support is plugin-specific (e.g. `http`, `https`, `file`).

---

## Video Backends

- **Class**: `VideoService` (`ovos_media/media_backends/video.py`)
- **Entry-point group**: `opm.media.video`
- **OPM plugin loader**: `ovos_plugin_manager.ocp.find_ocp_video_plugins`
- **Config key**: `media.video_players`
- **Preferred service config key**: `media.preferred_video_services`

Example plugins: `ovos-media-plugin-vlc` (`ovos-media-video-plugin-vlc`),
`ovos-media-plugin-mplayer` (`ovos-media-video-plugin-mplayer`),
`ovos-media-plugin-chromecast` (`ovos-media-video-plugin-chromecast`).

Video backends are selected when `now_playing.playback == PlaybackType.VIDEO`. They render video content directly (their own window, a Chromecast target, a browser tab, …); `ovos-media` only reports their state over the bus.

---

## Web Backends

- **Class**: `WebService` (`ovos_media/media_backends/web.py`)
- **Entry-point group**: `opm.media.web`
- **OPM plugin loader**: `ovos_plugin_manager.ocp.find_ocp_web_plugins`
- **Config key**: `media.web_players`
- **Preferred service config key**: `media.preferred_web_services`

Web backends are selected when `now_playing.playback == PlaybackType.WEBVIEW`. They render arbitrary web URLs directly (a browser tab, an embedded webview, …).

---

## BaseMediaService API

`BaseMediaService` (`ovos_media/media_backends/base.py`) provides the following methods that backend manager classes inherit. Individual plugin instances (subclasses of `MediaBackend` from OPM) implement the actual playback logic.

### `available_backends()`

Returns a dict of `{backend_name: {"supported_uris": [...], "remote": bool}}` for every loaded service instance. Used by the `opm.audio.query` handler in `OCPBusApi` to respond to OPM discovery queries.

### `play(uri, preferred_service=None)`

Selects the appropriate backend for `uri` (by URI scheme prefix) and calls `backend.load_track(uri)`. Selection order:

1. `preferred_service` if it supports the URI scheme.
2. `self.current` (the previously active backend) if it supports the URI scheme.
3. The first entry in `self.services` that supports the URI scheme.

If no backend supports the URI, playback is skipped with a log message.

### `pause()` / `resume()` / `stop()`

Delegate to `self.current.pause()` / `resume()` / `stop()` and emit the corresponding OCP state events. `stop` only fires if at least 1 second has elapsed since playback started (guard against accidental immediate stop).

### `lower_volume()` / `restore_volume()`

Ducking hooks. `lower_volume` calls `self.current.lower_volume()` and sets `self.volume_is_low = True`. `restore_volume` calls `self.current.restore_volume()` and clears the flag. These are invoked by the `ovos.common_play.duck` / `ovos.common_play.unduck` bus events, and by ovos-audio's `ovos.audio.output.started` / `ovos.audio.output.ended` events, which it emits unconditionally on every TTS output.

### `handle_media_state_change(message)`

Listens for `ovos.common_play.media.state`. When `MediaState.LOADED_MEDIA` is received and `self.current` is set, calls `self.current.play()` and emits the appropriate `ovos.common_play.track.state` (`PLAYING_AUDIO`, `PLAYING_VIDEO`, or `PLAYING_WEBVIEW` depending on `self.namespace`).

### `wait_for_load(timeout=180)`

Blocks until plugin loading completes or the timeout expires. Returns `True` on success.

---

## Playback Type Routing

`OCPMediaPlayer.play` checks `self.playback_type` (which reads `self.now_playing.playback`) and routes to the correct service:

```
PlaybackType.AUDIO   -> audio_service.play(uri, preferred_service)
PlaybackType.VIDEO   -> video_service.play(uri, preferred_service)
PlaybackType.WEBVIEW -> web_service.play(uri, preferred_service)
PlaybackType.SKILL   -> emit ovos.common_play.<skill_id>.play
```

The `preferred_service` argument in each case is resolved by `_resolve_preferred_service`.

Before routing, `OCPMediaPlayer.validate_stream` calls `NowPlaying.extract_stream()` to resolve any stream extractor identifiers (SEIs) into real URIs. If `playback_mode` is set to `PlaybackMode.FORCE_AUDIO` (the enum member or its name as a string), the playback type is coerced to `PlaybackType.AUDIO` regardless of the original value. `PlaybackType.SKILL` and `PlaybackType.MPRIS` skip stream extraction entirely (the skill or external player owns the stream).

---

## Writing a Custom Backend Plugin

Backend plugins subclass `AudioPlayerBackend` (or `VideoPlayerBackend` / `WebPlayerBackend`) from `ovos_plugin_manager.templates.media`, **not** `BaseMediaService`. `BaseMediaService` is the manager that loads and selects backends; the plugin is the worker that drives a player.

### Minimal `pyproject.toml` registration

```toml
[project.entry-points."opm.media.audio"]
my-audio-backend = "my_package.plugin:MyAudioBackend"
```

For video plugins use the group `opm.media.video`; for web plugins use `opm.media.web`.

### Minimal skeleton class

```python
from ovos_plugin_manager.templates.media import AudioPlayerBackend


class MyAudioBackend(AudioPlayerBackend):
    """Minimal custom audio backend for ovos-media."""

    def __init__(self, config=None, bus=None):
        super().__init__(config, bus)

    def supported_uris(self) -> list:
        """Return the URI schemes this backend can play."""
        return ["http", "https", "file"]

    def play(self) -> None:
        """Begin playback of the currently loaded track (self._now_playing)."""

    def stop(self) -> bool:
        """Stop playback. Return True if something was stopped."""
        return True

    def pause(self) -> None:
        """Pause playback."""

    def resume(self) -> None:
        """Resume paused playback."""

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

The base class provides `load_track(uri, metadata=None)` (which stores the URI and emits `MediaState.LOADED_MEDIA`) and the `ocp_start` / `ocp_stop` / `ocp_pause` / `ocp_resume` helpers that emit the OCP state events for you. Subclasses implement the abstract transport methods above; `supported_uris` is a method, not a property.

`BaseMediaService.load_services` instantiates the plugin as `MyAudioBackend(plug_cfg, bus)`. The `plug_cfg` dict comes from the `media.audio_players.<player_name>` configuration block.

Remote backends (servers, casting targets) subclass `RemoteAudioPlayerBackend` / `RemoteVideoPlayerBackend` / `RemoteWebPlayerBackend` instead, which the manager always checks *after* local backends so playback starts locally by default.

---

## See also

- [Architecture](architecture.md), where backends sit in the daemon
- [Media providers](media-providers.md), the search/catalog layer that supplies playables
- [Configuration](configuration.md), `audio_players` / `video_players` / `web_players` and the preferred-service keys
- [MPRIS integration](mpris.md), controlling playback from the desktop

---
[← Media providers](media-providers.md) · [Home](../README.md) · [Configuration →](configuration.md)

# Next Steps

## Priority 1: Fix Obvious Bugs

### Hardcoded MA URL in ovos-skill-mass
`ovos-skill-mass/__init__.py` line 29: `url = "http://100.88.41.41:8095"` overrides the settings value.
Fix: remove that line and use `self.settings.get("url")` only.

### Blocking Event in MAssBaseService.handle_player_ping
`ovos-media-plugin-mass/ovos_media_plugin_mass/media.py`: `threading.Event().wait(20)` creates a new Event and waits 20 seconds unconditionally. This blocks the bus handler thread.
Fix: use `time.sleep(20)` or a proper interval mechanism, or convert to a daemon thread that polls on a schedule.

### next()/previous() missing from MAssAudioService (legacy)
`MAssAudioService` (for `ovos-audio`) logs an error on `next()`/`previous()`. The MA client already supports these (`queue_command_next`/`queue_command_previous`).
Fix: implement them using the queue API (the queue_id must be fetched from `active_queue`).

---

## Priority 2: Migrate ovos-audio and Deprecate ovos-ocp-audio-plugin

See [06 - ovos-audio and Migration](06-ovos-audio-migration.md) for full detail.

`ovos-ocp-audio-plugin` is the last major blocker to a clean architecture. The complication is that `ovos-audio` treats it specially — it is NOT loaded through normal plugin discovery. `AudioService.load_services()` explicitly pops `ovos_common_play` from the found plugins and loads it via a hardcoded `find_ocp()` call with a direct import of `OCPAudioBackend`. This means removing OCP from `ovos-audio` requires changes in `ovos-audio` itself, not just uninstalling the plugin.

### Steps

1. Ensure `ovos-media` handles all `PlaybackType` values the old plugin handled (AUDIO, VIDEO, WEBVIEW, SKILL — the last one being an edge case).
2. Flip the two TODO defaults in `ovos_audio/service.py`:
   - `enable_old_audioservice` default: `True` → `False`
   - `disable_ocp` default: `False` → `True`
   - These must be done with a deprecation warning cycle first (emit loud warnings for one or two releases before flipping).
3. Remove `AudioService.find_ocp()`, the `self.ocp` field, and the `ovos_common_play` exclusion from `load_services()`.
4. Remove the deprecated `handle_opm_audio_query()` and `get_audio_options()` handlers.
5. Consider whether `AudioService` itself is still needed after OCP removal (it may collapse into `PlaybackService`).
6. Archive `ovos-ocp-audio-plugin` on GitHub and mark it deprecated in `PACKAGE_INVENTORY.md`.
7. Remove `ClassicAudioServiceInterface` bridge from the pipeline plugin (currently bridges `ovos.common_play.*` → `mycroft.audio.service.*` for the old plugin).

---

## Priority 3: GUI Decoupling in ovos-media

Currently `ovos-media` still pushes Qt5 QML pages via `ovos_media/gui.py`. This must be replaced by the GUI adapter plugin system (`opm.gui_adapter`) being developed in the GUI refactor.

### Plan

1. Remove `ovos_media/gui.py` direct GUI calls from `OCPMediaCatalog` in `player.py`.
2. Instead, emit structured template calls via the GUI adapter interface (same pattern as `ovos-workshop` skills after the GUI refactor — `show_player()`, `show_list()`, etc.).
3. Ship an `ovos-legacy-mycroft-gui-plugin` adapter that renders the existing QML when connected.
4. The `ovos_media/qt5/` directory becomes part of the legacy GUI plugin, not `ovos-media` core.

This mirrors exactly what was done for skills in Phase C of the GUI refactor.

### New Template Methods Needed

The GUI adapter interface needs media-player-specific templates that don't exist yet in the 21-template set:
- `show_player(track, artist, album, image, position, duration, state)` — now playing
- `show_search_results(results)` — disambiguation / result list
- `show_playlist(tracks)` — current playlist view
- `show_media_search(query)` — searching animation

These should be added to `AbstractGUIPlugin` in `ovos-plugin-manager`.

---

## Priority 4: Extract OCPMediaCatalog Skill Logic

`ovos_media/player.py` inherits `OVOSCommonPlaybackSkill` to provide:
- Liked songs search (`@ocp_search`)
- Featured media browse (`@ocp_featured_media`)

These could be extracted into a small standalone skill (`ovos-skill-ocp-catalog`) that does not carry the full player daemon. This would remove `ovos-workshop` as a hard dependency of `ovos-media`.

---

## Priority 5: ovos-media-plugin-mass Improvements

### URI Resolution Transparency
When the pipeline selects a `library://` URI result from `ovos-skill-mass`, only the MA backend can play it. If MA is misconfigured, the failure is silent or confusing. Consider:
- Adding a URI validation step in `OCPMediaCatalog` that checks for a capable backend before committing to a result
- Or having the skill annotate results with a `required_backend` field that the pipeline can use for routing

### Player Selection by Voice
The MA plugin registers aliases (e.g. `"HomeLabRenderer"`, `"Home Lab Renderer"`) so users can say "play ... on the home lab renderer". The pipeline plugin needs to extract the target player from the utterance and pass it as context. This is partially supported by the `aliases` config — verify it works end-to-end.

### WebSocket vs HTTP
The current `SimpleHTTPMusicAssistantClient` uses the MA HTTP API with a custom JSON command format. MA's native client library uses WebSocket. The HTTP approach is simpler but may lag behind the MA API as it evolves. Consider switching to `music-assistant-client` (official async WS library) in a daemon thread, or monitoring the HTTP API for deprecation.

### State Polling
Real-time state from MA (track change, end of track, player disconnected) currently uses approximation (`PlaybackTimestampTracker`). MA supports WebSocket events for all of these. Connecting to the WS event stream would allow:
- Accurate end-of-track detection (currently the tracker polls)
- Track change notifications for playlist navigation
- Player availability changes without polling

---

## Priority 6: Packaging and Entry Points

`ovos-media-plugin-mass` currently registers two entry points:
- `opm.audio` (for legacy `ovos-audio`)
- `opm.media_audio` (for `ovos-media`)

Verify these match the actual entry point group names expected by `ovos-plugin-manager`. The plugin manager loads backends by entry point group — mismatched group names mean silent failure.

Similarly, `ovos-skill-mass` should be registered via the standard OVOS skill entry point so it can be auto-discovered and loaded.

---

## Open Questions

1. **Should `ovos-media` keep the `OCPMediaCatalog` skill identity?** The player being a skill is an implementation convenience but conceptually wrong. The cleaner split is: player daemon handles playback; a separate skill handles liked-songs search and browse.

2. **What happens to SKILL playback type?** The old `ovos-ocp-audio-plugin` supports `PlaybackType.SKILL` where a skill handles its own playback (e.g. Spotify skill). Does `ovos-media` need to support this? If yes, how does it delegate?

3. **Multi-room / multi-player routing** — MA supports multiple speakers per room. The current plugin model creates one OVOS backend per MA player. With proper session/site_id routing (from the GUI refactor), a future improvement could auto-route to the "closest" player based on which satellite is speaking.

4. **`ovos-skill-local-media` vs `ovos-ocp-files-plugin`** — there are two packages for local file playback. Which is canonical going forward?

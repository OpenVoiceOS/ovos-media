# Changelog

## [2.0.0a3](https://github.com/OpenVoiceOS/ovos-media/tree/2.0.0a3) (2026-08-17)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/2.0.0a2...2.0.0a3)

**Merged pull requests:**

- refactor: single bus-edge registration layer [\#160](https://github.com/OpenVoiceOS/ovos-media/pull/160) ([JarbasAl](https://github.com/JarbasAl))

## [2.0.0a2](https://github.com/OpenVoiceOS/ovos-media/tree/2.0.0a2) (2026-08-17)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/2.0.0a1...2.0.0a2)

**Merged pull requests:**

- refactor: extract bus payload schemas module [\#155](https://github.com/OpenVoiceOS/ovos-media/pull/155) ([JarbasAl](https://github.com/JarbasAl))

## [2.0.0a1](https://github.com/OpenVoiceOS/ovos-media/tree/2.0.0a1) (2026-08-17)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/1.0.0a1...2.0.0a1)

**Breaking changes:**

- refactor!: drop in-process GUI and per-namespace service bus surface [\#153](https://github.com/OpenVoiceOS/ovos-media/pull/153) ([JarbasAl](https://github.com/JarbasAl))

## [1.0.0a1](https://github.com/OpenVoiceOS/ovos-media/tree/1.0.0a1) (2026-08-16)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.27a1...1.0.0a1)

**Breaking changes:**

- refactor!: drop legacy mycroft.audio compat surfaces [\#151](https://github.com/OpenVoiceOS/ovos-media/pull/151) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.27a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.27a1) (2026-08-16)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.26a1...0.4.27a1)

**Merged pull requests:**

- fix: seed mpris signal-created meta with identity and always register the empty song\_name keyword fallback [\#149](https://github.com/OpenVoiceOS/ovos-media/pull/149) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.26a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.26a1) (2026-08-15)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.25a1...0.4.26a1)

**Merged pull requests:**

- fix: guard non-list tracks payload and populate player\_meta before mpris signal branches write to it [\#147](https://github.com/OpenVoiceOS/ovos-media/pull/147) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.25a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.25a1) (2026-08-15)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.24a1...0.4.25a1)

**Merged pull requests:**

- fix: guard external-play tracks and play\_media track types from crashing bus handlers [\#145](https://github.com/OpenVoiceOS/ovos-media/pull/145) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.24a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.24a1) (2026-08-15)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.23a1...0.4.24a1)

**Merged pull requests:**

- fix: guard preferred-backend matching and validate play\_media payloads before mutation [\#143](https://github.com/OpenVoiceOS/ovos-media/pull/143) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.23a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.23a1) (2026-08-15)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.22a1...0.4.23a1)

**Merged pull requests:**

- fix: flatten nested/generator media\_types, guard backend name lookup, guard empty legacy play entries [\#141](https://github.com/OpenVoiceOS/ovos-media/pull/141) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.22a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.22a1) (2026-08-14)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.21a1...0.4.22a1)

**Merged pull requests:**

- fix: adult-filter set bypass, backend selection substring match, unlocked liked\_songs reader, play\_prev asymmetry [\#139](https://github.com/OpenVoiceOS/ovos-media/pull/139) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.21a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.21a1) (2026-08-14)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.20a1...0.4.21a1)

**Merged pull requests:**

- fix: isolate backend supported\_uris failures, guard MPRIS Volume, lock liked\_songs, narrow injection check [\#137](https://github.com/OpenVoiceOS/ovos-media/pull/137) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.20a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.20a1) (2026-08-14)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.19a1...0.4.20a1)

**Merged pull requests:**

- fix: sanitize raw playlist dicts, guard MPRIS Metadata, widen injection-char check, fix play-count race [\#135](https://github.com/OpenVoiceOS/ovos-media/pull/135) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.19a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.19a1) (2026-08-14)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.18a1...0.4.19a1)

**Merged pull requests:**

- fix: sanitize nested playlists, clamp MPRIS position, dispatch video seek [\#133](https://github.com/OpenVoiceOS/ovos-media/pull/133) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.18a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.18a1) (2026-08-14)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.17a1...0.4.18a1)

**Merged pull requests:**

- fix: reject non-finite media values and harden legacy track lists [\#131](https://github.com/OpenVoiceOS/ovos-media/pull/131) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.17a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.17a1) (2026-08-14)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.16a1...0.4.17a1)

**Merged pull requests:**

- fix: validate bus-fed media metadata and message routing types [\#129](https://github.com/OpenVoiceOS/ovos-media/pull/129) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.16a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.16a1) (2026-08-14)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.15a2...0.4.16a1)

**Merged pull requests:**

- fix: MPRIS Position wire type and stop/pause-all iteration race [\#127](https://github.com/OpenVoiceOS/ovos-media/pull/127) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.15a2](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.15a2) (2026-08-14)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.15a1...0.4.15a2)

**Merged pull requests:**

- chore: state invariants in comments, descriptive test file names [\#121](https://github.com/OpenVoiceOS/ovos-media/pull/121) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.15a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.15a1) (2026-08-14)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.14a1...0.4.15a1)

**Merged pull requests:**

- fix: invoke backend pause/resume exactly once per request [\#124](https://github.com/OpenVoiceOS/ovos-media/pull/124) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.14a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.14a1) (2026-08-14)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.13a1...0.4.14a1)

**Merged pull requests:**

- fix: resume corked playback when the utterance is handled [\#122](https://github.com/OpenVoiceOS/ovos-media/pull/122) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.13a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.13a1) (2026-08-14)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.12a1...0.4.13a1)

**Merged pull requests:**

- fix: shuffle failure bounds and dead-player duck handler leak [\#119](https://github.com/OpenVoiceOS/ovos-media/pull/119) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.12a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.12a1) (2026-08-14)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.11a1...0.4.12a1)

**Merged pull requests:**

- fix: shuffled playback actually plays, mpris reflection always constructs, playlist.set validates first [\#117](https://github.com/OpenVoiceOS/ovos-media/pull/117) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.11a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.11a1) (2026-08-13)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.10a1...0.4.11a1)

**Merged pull requests:**

- fix: certification round — console script argv, honest queue.finished, playback-evidence resets, gui teardown [\#115](https://github.com/OpenVoiceOS/ovos-media/pull/115) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.10a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.10a1) (2026-08-13)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.9a1...0.4.10a1)

**Merged pull requests:**

- fix: UX quick wins — help flag, spoken failures, honest error messages, provider docs [\#113](https://github.com/OpenVoiceOS/ovos-media/pull/113) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.9a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.9a1) (2026-08-13)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.8a1...0.4.9a1)

**Merged pull requests:**

- fix: lifecycle hygiene — full teardown, store tolerance, guarded like, late query binding [\#111](https://github.com/OpenVoiceOS/ovos-media/pull/111) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.8a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.8a1) (2026-08-13)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.7a2...0.4.8a1)

**Merged pull requests:**

- fix: a new play request cancels the pending invalid-stream retry [\#109](https://github.com/OpenVoiceOS/ovos-media/pull/109) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.7a2](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.7a2) (2026-08-13)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.7a1...0.4.7a2)

**Merged pull requests:**

- docs: first-playback payload, status reply convention, backend system deps [\#106](https://github.com/OpenVoiceOS/ovos-media/pull/106) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.7a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.7a1) (2026-08-13)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.6a1...0.4.7a1)

**Merged pull requests:**

- fix: scope stop signalling to the active service and cancel the invalid retry on stop [\#105](https://github.com/OpenVoiceOS/ovos-media/pull/105) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.6a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.6a1) (2026-08-13)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.5a1...0.4.6a1)

**Merged pull requests:**

- fix: single-writer end-of-track path, stop semantics, bounded retries [\#103](https://github.com/OpenVoiceOS/ovos-media/pull/103) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.5a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.5a1) (2026-08-13)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.4a1...0.4.5a1)

**Merged pull requests:**

- fix: playback state machine, autoplay, GUI seekbar payload, session-gated search [\#101](https://github.com/OpenVoiceOS/ovos-media/pull/101) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.4a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.4a1) (2026-08-13)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.3a2...0.4.4a1)

**Merged pull requests:**

- fix: mpris Position unit \(ms-\>us\) and honest \_stop\_player failure handling [\#99](https://github.com/OpenVoiceOS/ovos-media/pull/99) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.3a2](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.3a2) (2026-08-13)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.2a1...0.4.3a2)

**Merged pull requests:**

- test: e2e seek pins expect milliseconds per the OPM MediaBackend contract [\#97](https://github.com/OpenVoiceOS/ovos-media/pull/97) ([JarbasAl](https://github.com/JarbasAl))
- fix: daemon startup crash, backend seek API, silent play failures, config robustness [\#96](https://github.com/OpenVoiceOS/ovos-media/pull/96) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.2a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.2a1) (2026-08-13)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.1a1...0.4.2a1)

**Merged pull requests:**

- fix: legacy audio API coexistence gaps [\#93](https://github.com/OpenVoiceOS/ovos-media/pull/93) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.1a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.1a1) (2026-08-13)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.4.0a1...0.4.1a1)

**Merged pull requests:**

- fix: bound mpris retry loop, tolerate real-world metadata shapes, deliver mpris config [\#92](https://github.com/OpenVoiceOS/ovos-media/pull/92) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.0a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.4.0a1) (2026-08-13)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.3.4a1...0.4.0a1)

**Merged pull requests:**

- feat: now-playing voice intents \(WhatSong/WhatAlbum/WhatArtist\) + shuffle on/off [\#90](https://github.com/OpenVoiceOS/ovos-media/pull/90) ([JarbasAl](https://github.com/JarbasAl))

## [0.3.4a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.3.4a1) (2026-08-13)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.3.3a5...0.3.4a1)

## [0.3.3a5](https://github.com/OpenVoiceOS/ovos-media/tree/0.3.3a5) (2026-08-13)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.3.3a4...0.3.3a5)

**Merged pull requests:**

- fix\(deps\): bump ecosystem dependency floors to current majors [\#87](https://github.com/OpenVoiceOS/ovos-media/pull/87) ([JarbasAl](https://github.com/JarbasAl))
- chore: drop uv.lock [\#86](https://github.com/OpenVoiceOS/ovos-media/pull/86) ([JarbasAl](https://github.com/JarbasAl))
- docs: cross-link the technical manual [\#75](https://github.com/OpenVoiceOS/ovos-media/pull/75) ([JarbasAl](https://github.com/JarbasAl))

## [0.3.3a4](https://github.com/OpenVoiceOS/ovos-media/tree/0.3.3a4) (2026-08-02)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.3.3a2...0.3.3a4)

**Merged pull requests:**

- chore: remove duplicate publish-alpha workflow [\#84](https://github.com/OpenVoiceOS/ovos-media/pull/84) ([JarbasAl](https://github.com/JarbasAl))

## [0.3.3a2](https://github.com/OpenVoiceOS/ovos-media/tree/0.3.3a2) (2026-08-02)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.3.3a3...0.3.3a2)

## [0.3.3a3](https://github.com/OpenVoiceOS/ovos-media/tree/0.3.3a3) (2026-08-02)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.3.3a1...0.3.3a3)

**Merged pull requests:**

- ci: fire alpha release on merged PRs, publish to PyPI [\#81](https://github.com/OpenVoiceOS/ovos-media/pull/81) ([JarbasAl](https://github.com/JarbasAl))

## [0.3.3a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.3.3a1) (2026-08-02)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.3.2a2...0.3.3a1)

**Merged pull requests:**

- fix: NowPlaying orjson serialization TypeError [\#76](https://github.com/OpenVoiceOS/ovos-media/pull/76) ([JarbasAl](https://github.com/JarbasAl))

## [0.3.2a2](https://github.com/OpenVoiceOS/ovos-media/tree/0.3.2a2) (2026-07-31)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.3.2a1...0.3.2a2)

**Merged pull requests:**

- docs: rewrite README in Simplified Technical English [\#73](https://github.com/OpenVoiceOS/ovos-media/pull/73) ([JarbasAl](https://github.com/JarbasAl))

## [0.3.2a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.3.2a1) (2026-07-26)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.3.1a1...0.3.2a1)

**Merged pull requests:**

- fix: remove the media.state listener in remove\_listeners\(\) \(leaked handler\) [\#70](https://github.com/OpenVoiceOS/ovos-media/pull/70) ([JarbasAl](https://github.com/JarbasAl))

## [0.3.1a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.3.1a1) (2026-07-18)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.3.0a3...0.3.1a1)

**Merged pull requests:**

- fix: consistent MediaEntry representation and live GUI search results [\#64](https://github.com/OpenVoiceOS/ovos-media/pull/64) ([JarbasAl](https://github.com/JarbasAl))

## [0.3.0a3](https://github.com/OpenVoiceOS/ovos-media/tree/0.3.0a3) (2026-07-17)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.3.0a2...0.3.0a3)

**Merged pull requests:**

- docs: add pre-release WIP notice to README [\#62](https://github.com/OpenVoiceOS/ovos-media/pull/62) ([JarbasAl](https://github.com/JarbasAl))

## [0.3.0a2](https://github.com/OpenVoiceOS/ovos-media/tree/0.3.0a2) (2026-06-26)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.3.0a1...0.3.0a2)

**Merged pull requests:**

- chore: drop scratch files [\#60](https://github.com/OpenVoiceOS/ovos-media/pull/60) ([JarbasAl](https://github.com/JarbasAl))

## [0.3.0a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.3.0a1) (2026-06-26)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.2.0a4...0.3.0a1)

**Merged pull requests:**

- feat: only act on the default/local session \(validate\_source\) [\#58](https://github.com/OpenVoiceOS/ovos-media/pull/58) ([JarbasAl](https://github.com/JarbasAl))

## [0.2.0a4](https://github.com/OpenVoiceOS/ovos-media/tree/0.2.0a4) (2026-06-25)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.2.0a3...0.2.0a4)

**Merged pull requests:**

- docs: 10/10 pass — glossary, audience routing, game-skill carve-out [\#56](https://github.com/OpenVoiceOS/ovos-media/pull/56) ([JarbasAl](https://github.com/JarbasAl))

## [0.2.0a3](https://github.com/OpenVoiceOS/ovos-media/tree/0.2.0a3) (2026-06-25)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.2.0a2...0.2.0a3)

## [0.2.0a2](https://github.com/OpenVoiceOS/ovos-media/tree/0.2.0a2) (2026-06-25)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.2.0a1...0.2.0a2)

**Merged pull requests:**

- docs: complete + verify ovos-media documentation [\#52](https://github.com/OpenVoiceOS/ovos-media/pull/52) ([JarbasAl](https://github.com/JarbasAl))
- test: e2e daemon lifecycle across all backend types + run e2e in CI [\#51](https://github.com/OpenVoiceOS/ovos-media/pull/51) ([JarbasAl](https://github.com/JarbasAl))

## [0.2.0a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.2.0a1) (2026-06-25)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.1.0a1...0.2.0a1)

**Merged pull requests:**

- feat: stop OCP playback on external MPRIS takeover [\#49](https://github.com/OpenVoiceOS/ovos-media/pull/49) ([JarbasAl](https://github.com/JarbasAl))

## [0.1.0a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.1.0a1) (2026-06-25)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.0.3a1...0.1.0a1)

**Merged pull requests:**

- feat: reflect external MPRIS players as OCP now\_playing \(bus primitive\) [\#47](https://github.com/OpenVoiceOS/ovos-media/pull/47) ([JarbasAl](https://github.com/JarbasAl))

## [0.0.3a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.0.3a1) (2026-06-25)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.0.2a4...0.0.3a1)

**Merged pull requests:**

- fix: remove reference to non-existent TrackState.PAUSED\_AUDIO [\#45](https://github.com/OpenVoiceOS/ovos-media/pull/45) ([JarbasAl](https://github.com/JarbasAl))

## [0.0.2a4](https://github.com/OpenVoiceOS/ovos-media/tree/0.0.2a4) (2026-06-22)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.0.2a3...0.0.2a4)

**Merged pull requests:**

- docs: modernize README + overhaul /docs \(Virtual Media Player, MediaProviders\) [\#43](https://github.com/OpenVoiceOS/ovos-media/pull/43) ([JarbasAl](https://github.com/JarbasAl))

## [0.0.2a3](https://github.com/OpenVoiceOS/ovos-media/tree/0.0.2a3) (2026-06-22)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.0.2a2...0.0.2a3)

**Merged pull requests:**

- docs: scope dataset credits \(NeonGecko = original OCP dataset, TigreGotico = newer\) [\#41](https://github.com/OpenVoiceOS/ovos-media/pull/41) ([JarbasAl](https://github.com/JarbasAl))

## [0.0.2a2](https://github.com/OpenVoiceOS/ovos-media/tree/0.0.2a2) (2026-06-22)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/0.0.2a1...0.0.2a2)

**Merged pull requests:**

- docs: drop NeonGecko sponsorship credit [\#39](https://github.com/OpenVoiceOS/ovos-media/pull/39) ([JarbasAl](https://github.com/JarbasAl))

## [0.0.2a1](https://github.com/OpenVoiceOS/ovos-media/tree/0.0.2a1) (2026-06-22)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/V0.0.1a22...0.0.2a1)

**Closed issues:**

- When music is playing, gui complains about missing file [\#22](https://github.com/OpenVoiceOS/ovos-media/issues/22)
- No luck with ovos-media and OCP plugin [\#21](https://github.com/OpenVoiceOS/ovos-media/issues/21)

**Merged pull requests:**

- feat: modernize and stabilize ovos-media \(OCPMediaPlayer rework, tests, GUI decoupling\) [\#37](https://github.com/OpenVoiceOS/ovos-media/pull/37) ([JarbasAl](https://github.com/JarbasAl))
- chore\(deps\): update dependency ovos-plugin-manager to v2 [\#33](https://github.com/OpenVoiceOS/ovos-media/pull/33) ([renovate[bot]](https://github.com/apps/renovate))
- chore\(deps\): update dependency ovos-config to v2 [\#32](https://github.com/OpenVoiceOS/ovos-media/pull/32) ([renovate[bot]](https://github.com/apps/renovate))
- chore\(deps\): update dependency ovos-bus-client to v1 [\#31](https://github.com/OpenVoiceOS/ovos-media/pull/31) ([renovate[bot]](https://github.com/apps/renovate))
- chore\(deps\): update dependency python to 3.14 [\#25](https://github.com/OpenVoiceOS/ovos-media/pull/25) ([renovate[bot]](https://github.com/apps/renovate))
- chore: Configure Renovate [\#24](https://github.com/OpenVoiceOS/ovos-media/pull/24) ([renovate[bot]](https://github.com/apps/renovate))

## [V0.0.1a22](https://github.com/OpenVoiceOS/ovos-media/tree/V0.0.1a22) (2024-04-09)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/V0.0.1a21...V0.0.1a22)

## [V0.0.1a21](https://github.com/OpenVoiceOS/ovos-media/tree/V0.0.1a21) (2024-04-09)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/V0.0.1a20...V0.0.1a21)

## [V0.0.1a20](https://github.com/OpenVoiceOS/ovos-media/tree/V0.0.1a20) (2024-03-29)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/V0.0.1a19...V0.0.1a20)

**Fixed bugs:**

- fix: switch case [\#20](https://github.com/OpenVoiceOS/ovos-media/pull/20) ([mikejgray](https://github.com/mikejgray))

## [V0.0.1a19](https://github.com/OpenVoiceOS/ovos-media/tree/V0.0.1a19) (2024-03-29)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/V0.0.1a18...V0.0.1a19)

## [V0.0.1a18](https://github.com/OpenVoiceOS/ovos-media/tree/V0.0.1a18) (2024-03-29)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/V0.0.1a17...V0.0.1a18)

## [V0.0.1a17](https://github.com/OpenVoiceOS/ovos-media/tree/V0.0.1a17) (2024-03-21)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/V0.0.1a16...V0.0.1a17)

## [V0.0.1a16](https://github.com/OpenVoiceOS/ovos-media/tree/V0.0.1a16) (2024-03-10)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/V0.0.1a15...V0.0.1a16)

## [V0.0.1a15](https://github.com/OpenVoiceOS/ovos-media/tree/V0.0.1a15) (2024-02-07)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/V0.0.1a14...V0.0.1a15)

## [V0.0.1a14](https://github.com/OpenVoiceOS/ovos-media/tree/V0.0.1a14) (2024-02-02)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/V0.0.1a13...V0.0.1a14)

## [V0.0.1a13](https://github.com/OpenVoiceOS/ovos-media/tree/V0.0.1a13) (2024-02-02)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/V0.0.1a12...V0.0.1a13)

## [V0.0.1a12](https://github.com/OpenVoiceOS/ovos-media/tree/V0.0.1a12) (2024-02-02)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/V0.0.1a11...V0.0.1a12)

## [V0.0.1a11](https://github.com/OpenVoiceOS/ovos-media/tree/V0.0.1a11) (2024-01-29)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/V0.0.1a10...V0.0.1a11)

## [V0.0.1a10](https://github.com/OpenVoiceOS/ovos-media/tree/V0.0.1a10) (2024-01-28)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/V0.0.1a9...V0.0.1a10)

## [V0.0.1a9](https://github.com/OpenVoiceOS/ovos-media/tree/V0.0.1a9) (2024-01-27)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/V0.0.1a8...V0.0.1a9)

## [V0.0.1a8](https://github.com/OpenVoiceOS/ovos-media/tree/V0.0.1a8) (2024-01-27)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/V0.0.1a7...V0.0.1a8)

## [V0.0.1a7](https://github.com/OpenVoiceOS/ovos-media/tree/V0.0.1a7) (2024-01-27)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/V0.0.1a6...V0.0.1a7)

## [V0.0.1a6](https://github.com/OpenVoiceOS/ovos-media/tree/V0.0.1a6) (2024-01-27)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/V0.0.1a5...V0.0.1a6)

## [V0.0.1a5](https://github.com/OpenVoiceOS/ovos-media/tree/V0.0.1a5) (2024-01-25)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/V0.0.1a4...V0.0.1a5)

## [V0.0.1a4](https://github.com/OpenVoiceOS/ovos-media/tree/V0.0.1a4) (2024-01-12)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/V0.0.1a3...V0.0.1a4)

## [V0.0.1a3](https://github.com/OpenVoiceOS/ovos-media/tree/V0.0.1a3) (2024-01-10)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-media/compare/379c62b2b7f6d6ff6f5fb59d1feb683bbbb56f41...V0.0.1a3)



\* *This Changelog was automatically generated by [github_changelog_generator](https://github.com/github-changelog-generator/github-changelog-generator)*

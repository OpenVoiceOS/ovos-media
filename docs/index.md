
# `ovos-media` — Documentation Index

> ovos-core audio daemon client

## Overview

`ovos-media` is part of the OpenVoiceOS platform. See the
[repository](https://github.com/OpenVoiceOS/ovos-media) for source code and issue tracking.

## Quick Links

| Resource | Path |
|----------|------|
| Machine-readable facts | [`../QUICK_FACTS.md`](../QUICK_FACTS.md) |
| Common questions | [`../FAQ.md`](../FAQ.md) |
| Change log | [`../MAINTENANCE_REPORT.md`](../MAINTENANCE_REPORT.md) |
| Known issues | [`../AUDIT.md`](../AUDIT.md) |
| Improvement proposals | [`../SUGGESTIONS.md`](../SUGGESTIONS.md) |

## GUI Integration

GUI rendering is handled via `GUIInterface("ovos.common_play")` from `ovos_bus_client.apis.gui`.
`OCPMediaPlayer._update_gui()` — `ovos_media/player.py` — calls `gui.show_media_player()` with
`now_playing`, `playlist`, `search_results`, and `state` on every playback state change.
Individual backend plugins (audio, video, web) handle their own rendering in separate GUI namespaces.
The `ovos_media/gui.py` file and `OCPGUIInterface` class have been removed.

## Documentation

- [01 History And Architecture](01-history-and-architecture.md)
- [02 Current State](02-current-state.md)
- [03 Music Assistant Integration](03-music-assistant-integration.md)
- [04 Next Steps](04-next-steps.md)
- [05 Ocp Pipeline](05-ocp-pipeline.md)
- [06 Ovos Audio Migration](06-ovos-audio-migration.md)
- [07 Ocp Pipeline Ml Classifier](07-ocp-pipeline-ml-classifier.md)
- [08 Gui Decoupling Plan](08-gui-decoupling-plan.md)
- [Readme](README.md)

## Cross-References

- [OpenVoiceOS Workspace — AGENTS.md](../AGENTS.md)
- [Package Inventory](../PACKAGE_INVENTORY.md)

> **Note**: This `docs/index.md` is a stub generated for compliance.
> It should be enriched with architecture diagrams, API references,
> and usage examples specific to `ovos-media`.

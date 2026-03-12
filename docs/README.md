# ovos-media Documentation

This directory documents the media subsystem of OpenVoiceOS — its history, current state, open problems, and planned next steps.

## Documents

- [01 - History and Architecture](01-history-and-architecture.md) — How we got here: Mycroft hack, GUI coupling, and the long untangling
- [02 - Current State](02-current-state.md) — Component inventory and what each piece does today
- [03 - Music Assistant Integration](03-music-assistant-integration.md) — How `ovos-skill-music-assistant` and `ovos-media-plugin-mass` fit in
- [04 - Next Steps](04-next-steps.md) — Planned work: deprecation of ocp-audio-plugin, GUI decoupling, known issues
- [05 - OCP Pipeline Plugin](05-ocp-pipeline.md) — How `ocp-pipeline-plugin` works: classification, search dispatch, session tracking, legacy bridges
- [06 - ovos-audio and Migration](06-ovos-audio-migration.md) — How `ovos-audio` specially hosts OCP today, the two config flags, and what must change in `ovos-audio` during migration
- [07 - OCP Pipeline: ML Classifier Plan](07-ocp-pipeline-ml-classifier.md) — Plan to replace padatious intent matching + `voc_match_media` keyword chain with a hierarchical Model2Vec classifier
- [08 - GUI Decoupling Plan](08-gui-decoupling-plan.md) — How to remove all custom QML from `ovos-media`, use only GUI adapter templates, and move QML assets to the Qt5 legacy plugin

## Quick Summary

The OVOS media stack has gone through three distinct phases:

1. **Mycroft era** — OCP was hacked into a mycroft-audio plugin (`ovos-ocp-audio-plugin`) because upstream rejected the PRs. Everything was monolithic: NLP, player state, and GUI rendering were entangled in one blob.
2. **Extraction era** — NLP was extracted into `ovos-ocp-pipeline-plugin`. Player logic into `ovos-media`. GUI coupling was reduced but never fully removed.
3. **Current (proof of concept)** — `ovos-media` is a standalone audio daemon. `ovos-ocp-audio-plugin` is still alive but deprecated. Music Assistant integration (`ovos-skill-mass` + `ovos-media-plugin-mass`) works cleanly against the new stack.

The goal is to fully deprecate `ovos-ocp-audio-plugin` once `ovos-media` covers all its use cases, and to complete GUI decoupling via the GUI adapter plugin system (see the GUI refactor tracked separately).

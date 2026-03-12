# OCP Pipeline: Replacing Classifiers with Hierarchical Model2Vec

## Current Classifier System

The OCP pipeline uses three distinct classification mechanisms, each serving a different purpose:

### 1. Padatious/Padacioso — OCP intent detection

`OCPPipelineMatcher` trains a per-language `IntentContainer` (padatious if available, padacioso as fallback) on locale files at startup:

```python
intents = ["play.intent", "open.intent", "media_stop.intent",
           "next.intent", "prev.intent", "pause.intent",
           "resume.intent", "save_game.intent", "load_game.intent"]
```

These handle `match_high` / `match_medium` / `match_low` — detecting whether an utterance is a media control command at all (e.g. "play jazz", "pause", "next track").

Problems:
- Requires locale files per language (currently in `ocp_pipeline/locale/`)
- Padatious requires a training step per language at startup, padacioso is much slower
- Pattern matching: no generalization to phrasing not in the locale files
- Only works for languages that have locale files

### 2. voc_match_media — media type classification

A massive if/elif chain of vocabulary file keyword matches:

```python
if self.voc_match(query, "MusicKeyword", lang=lang):
    return MediaType.MUSIC, 0.6
elif self.voc_match(query, "PodcastKeyword", lang=lang):
    return MediaType.PODCAST, 0.6
# ... 20+ more branches
return MediaType.GENERIC, 0.0
```

Problems:
- No semantic understanding — "I want to hear some Bach" → `MediaType.GENERIC`
- Hard-coded confidence values (0.4 / 0.6 / 0.7) with no calibration
- Requires vocabulary files per language per media type
- Falls back to `MediaType.GENERIC` on anything not covered
- If/elif ordering creates implicit priority — first match wins regardless of confidence

### 3. AhocorasickNER — runtime entity extraction

Populated dynamically as OCP skills announce themselves. Maps skill aliases to entity labels:

```python
self.ner.add_word("music_streaming_service", "Spotify")
self.ner.add_word("podcast_streaming_service", "Spotify")  # if skill supports both
```

Used to extract "play X on **Spotify**" → entity `music_streaming_service: "Spotify"`.

**This component must stay** — it is dynamic and populated at runtime from loaded skill metadata. It cannot be replaced by a pretrained model because the skill inventory is user-specific.

---

## The New Approach: Hierarchical Model2Vec

`train_hierarchical.py` trains a `StaticModelForHierarchicalClassification` with:

```
sentence → StaticModel encoder → text_emb
                                    │
                ┌───────────────────┴────────────────────┐
                │                                        │
         domain_head(emb)    intent_head(cat(emb, softmax(domain_logits)))
                │                                        │
         domain_logits                           intent_logits
                                         (masked to domain intents at inference)
```

**Inference:**
1. `domain_pred = argmax(domain_logits)`
2. Mask `intent_logits` to zero out intents NOT in the predicted domain
3. `intent_pred = argmax(masked_intent_logits)`

This architecture fits the OCP problem perfectly:
- **Domain** = is this a play request, a control command, something else?
- **Intent** = given it's a play request, what media type?

---

## Proposed Domain/Intent Hierarchy for OCP

### Domains

| Domain label | Meaning |
|---|---|
| `ocp_play` | A request to play/search media |
| `ocp_control` | A transport control command |
| `other` | Not an OCP utterance |

`other` exists so the model can output a low-confidence score for the OCP domains, giving the pipeline a threshold to reject non-media utterances.

### Intents per domain

**ocp_play** (maps directly to `MediaType`):

| Intent | MediaType | Example utterances |
|---|---|---|
| `music` | `MediaType.MUSIC` | "play some jazz", "I want to hear Bach" |
| `podcast` | `MediaType.PODCAST` | "play a podcast about history" |
| `radio` | `MediaType.RADIO` | "put on the radio", "play BBC Radio 4" |
| `audiobook` | `MediaType.AUDIOBOOK` | "read me a book", "play an audiobook" |
| `news` | `MediaType.NEWS` | "play the news", "what's happening today" |
| `movie` | `MediaType.MOVIE` | "play a movie", "I want to watch Inception" |
| `tv` | `MediaType.TV` | "put on Breaking Bad", "watch some TV" |
| `documentary` | `MediaType.DOCUMENTARY` | "play a documentary about space" |
| `anime` | `MediaType.ANIME` | "play some anime" |
| `cartoon` | `MediaType.CARTOON` | "put on a cartoon" |
| `short_film` | `MediaType.SHORT_FILM` | "play a short film" |
| `video` | `MediaType.VIDEO` | "play a video", "show me a video" |
| `game` | `MediaType.GAME` | "start a game", "play a game" |
| `generic` | `MediaType.GENERIC` | "play something", "put something on" |

**ocp_control** (maps to intent handler):

| Intent | Handler | Example utterances |
|---|---|---|
| `pause` | `handle_pause_intent` | "pause", "stop playing" |
| `resume` | `handle_resume_intent` | "resume", "continue", "unpause" |
| `stop` | `handle_stop_intent` | "stop", "turn it off" |
| `next` | `handle_next_intent` | "next", "skip", "next song" |
| `prev` | `handle_prev_intent` | "previous", "go back", "last track" |
| `like` | `handle_like_intent` | "like this", "I like this song" |

### domain_intent_mask

The mask is built automatically from the training data. Crucially, `music` intent will only fire when `domain=ocp_play`, never when `domain=ocp_control`. This prevents the current situation where a pause command could theoretically score as `MediaType.MUSIC`.

---

## Dataset Structure

The hierarchical model uses a CSV with columns: `lang`, `domain`, `intent`, `sentence`.

Example rows:
```
en,ocp_play,music,"play some jazz"
en,ocp_play,music,"I want to hear Bach"
en,ocp_play,podcast,"play a podcast about history"
en,ocp_control,pause,"pause"
en,ocp_control,pause,"stop the music"
en,ocp_control,next,"next track"
es,ocp_play,music,"pon algo de jazz"
```

The existing OCP dataset used to train locale files (`ocp_pipeline/locale/`) and the vocabulary files are the seed data source. The `gather_dataset.py` and `augment_dataset.py` scripts in `ovos-m2v-pipeline/train/` can augment this via LLM.

**Key requirement:** every OCP utterance must have a `domain` AND an `intent`. Generic play requests without an explicit media type go to `intent=generic`. Transport commands go to `ocp_control`.

---

## Integration Plan

### Phase 1: Add as optional model, no breaking changes

1. Add `StaticModelForHierarchicalClassification` loading to `OCPPipelineMatcher.__init__` when a model path is configured:
   ```json
   {
     "ocp_pipeline": {
       "m2v_model": "Jarbas/ocp-m2v-hierarchical-multilingual"
     }
   }
   ```
2. Implement `classify_media_m2v(utterance, lang)`:
   - Calls `model.predict_proba([utterance])`
   - Returns `(MediaType, float)` — maps predicted intent label to `MediaType` enum
   - Falls back to `voc_match_media()` if model is not loaded or confidence is below threshold
3. Implement `is_ocp_query_m2v(utterance, lang)`:
   - Returns `(bool, float)` based on `domain_probs["ocp_play"]`
   - If `domain_pred == "ocp_play"` and `prob > threshold` → it's an OCP query
   - This replaces the padatious `play.intent` check for `match_high`/`match_medium`

### Phase 2: Replace padatious intent matching

The padatious/padacioso `IntentContainer` handles 9 intent types. The hierarchical model handles `ocp_play` and the `ocp_control` variants. Replace `match_high`/`match_medium`/`match_low` logic:

**Current flow:**
```python
match = self.intent_matchers[lang].calc_intent(utterance)
# match.name → "play", "pause", "next" etc.
```

**New flow:**
```python
domain, intent, domain_prob, intent_prob = self.m2v_classify(utterance)
# domain → "ocp_play" or "ocp_control"
# intent → "music", "pause", "next" etc.
```

The OCP intent events (`ocp:play`, `ocp:pause`, etc.) are still emitted by the pipeline — only the detection mechanism changes.

`match_high` → domain_prob > conf_high
`match_medium` → domain_prob > conf_medium
`match_low` → domain_prob > conf_low

### Phase 3: Replace voc_match_media

Once the model is validated:
1. Remove `voc_match_media()` and its entire if/elif chain
2. Remove `classify_media()` wrapper (or reduce it to call the model)
3. Remove `DocumentaryKeyword.voc`, `MusicKeyword.voc`, etc. from `locale/`
4. Keep `voc_match_media()` as a fallback only if `m2v_model` is not configured (for offline/lightweight setups)

### Phase 4: Remove padatious dependency

1. Remove `load_intent_files()`, `register_ocp_intents()`, and the `IntentContainer` logic
2. Remove `ocp_pipeline/locale/` intent sample files (`.intent` files)
3. Remove `padatious`/`padacioso` from `requirements.txt`

The remaining locale files (vocabulary files like `MusicKeyword.voc`) can also be removed in phase 3.

---

## What Changes in opm.py

| Current code | Replacement |
|---|---|
| `self.intent_matchers[lang].calc_intent(utterance)` | `self.m2v_model.predict_proba([utterance])` |
| `self.voc_match_media(query, lang)` | domain/intent probs from hierarchical model |
| `self.classify_media(query, lang)` | intent prob mapped to `MediaType` |
| `self.is_ocp_query(query, lang)` | `domain_prob["ocp_play"] > threshold` |
| `self.load_intent_files()` | model load from HuggingFace or local path |
| `self.register_ocp_intents()` (padatious part) | no-op / removed |

### What does NOT change

- `self.ner` (`AhocorasickNER`) — still needed for runtime entity extraction from skill metadata
- `register_ocp_api_events()` — bus event wiring unchanged
- `OCPPlayerProxy` per-session tracking — unchanged
- `LegacyCommonPlay` bridge — unchanged (it's about legacy skill compat, not NLP)
- `ClassicAudioServiceInterface` bridge — unchanged
- All `handle_*_intent` handlers — unchanged; they still fire on `ocp:play`, `ocp:pause` etc.
- `_search()` dispatch to OCP skills — unchanged

---

## Model Publishing

Models trained with `train_hierarchical.py` should be published to HuggingFace under the `Jarbas/` org with naming convention:

```
Jarbas/ocp-m2v-hierarchical-{base_model_short}-{langs}
```

Examples:
- `Jarbas/ocp-m2v-hierarchical-potion-multilingual-128M` — multilingual, large
- `Jarbas/ocp-m2v-hierarchical-potion-base-32M-en` — English-only, medium
- `Jarbas/ocp-m2v-hierarchical-potion-base-4M-en` — English-only, tiny (embedded devices)

The default in config should be the multilingual model. English-only or tiny models can be user-selected for resource-constrained setups.

---

## Open Questions

1. **Should `ocp_control` intents be in the same hierarchical model as `ocp_play`?** Or should there be a separate model just for transport commands (pause/stop/next/prev) that is simpler and faster? The control intents are very short and unambiguous — a flat classifier or even padatious may be sufficient for them, and mixing with play intents bloats the training data.

2. **Threshold calibration.** The current `voc_match_media` returns fixed confidences (0.4–0.7). The hierarchical model produces calibrated probabilities — but `match_high/medium/low` thresholds need to be re-tuned against real utterances to maintain the same pipeline behavior.

3. **Fallback for unsupported languages.** If the multilingual model wasn't trained on a language, the vocabulary-based `voc_match_media` may still outperform it. Consider keeping `voc_match_media` as an explicit fallback for languages not in the model's training set.

4. **Confidence for `MediaType.GENERIC`.** When the model is uncertain and outputs `intent=generic`, what confidence is returned? The pipeline currently uses `0.0` for GENERIC to signal "dispatch to all skill types". The model may output non-zero probability for generic — need to decide whether to map this to `0.0` or use the model's actual probability.

5. **Per-domain intent accuracy.** The `train_hierarchical.py` evaluation already generates per-domain intent accuracy. Use this to monitor whether `ocp_play` → media type classification is actually better than the current keyword matching before committing to removing `voc_match_media`.

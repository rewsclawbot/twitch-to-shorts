# Claw Improvements — Integration Guide

**Branch:** `feature/claw-improvements`  
**Date:** 2026-02-14  
**Tests:** 395 passing (28 new)

---

## New Modules

### 1. Title Optimizer (`src/title_optimizer.py`)

**What:** AI-powered title rewriting with built-in A/B testing.

**How it works:**
- 50% of clips get AI-rewritten titles (treatment), 50% keep originals (control)
- A/B split is deterministic per clip_id (MD5 hash, same approach as `_choose_template`)
- Uses OpenAI gpt-4o-mini for rewrites — emotional hooks, curiosity gaps, action verbs, emoji
- Graceful fallback: any failure returns the original title

**Integration point in `main.py`:**
```python
from src.title_optimizer import optimize_title

# In _process_single_clip(), before upload:
optimized_title = optimize_title(clip.title, clip.streamer, clip.game_name, clip.id)
# Pass optimized_title to upload_short() instead of clip.title
```

**Environment variables:**
| Variable | Default | Description |
|---|---|---|
| `TITLE_OPTIMIZER_ENABLED` | `"false"` | Set to `"true"` to enable |
| `OPENAI_API_KEY` | (none) | Required for LLM rewrites |

**A/B Testing:** Track performance by comparing views on optimized vs original titles. The `_should_optimize(clip_id)` function is deterministic, so you can always check which group a clip belongs to.

---

### 2. Thumbnail Text Overlay (`src/thumbnail_enhancer.py`)

**What:** Adds bold text overlays to auto-extracted thumbnails for higher CTR.

**How it works:**
- Auto-detects system bold fonts (Arial Bold, Helvetica Bold, DejaVu Sans Bold)
- Auto-sizes text based on image dimensions
- Smart word wrap (2 lines max, truncation with "...")
- White text with 3px black stroke for readability on any background

**Integration point in `main.py`:**
```python
from src.thumbnail_enhancer import enhance_thumbnail

# In _process_single_clip(), after extract_thumbnail():
enhanced_path = enhance_thumbnail(thumbnail_path, clip.title)
# Pass enhanced_path to set_thumbnail()
```

**Environment variables:**
| Variable | Default | Description |
|---|---|---|
| `THUMBNAIL_TEXT_ENABLED` | `"true"` | Enabled by default; set to `"false"` to disable |

**Dependencies:** Pillow (already in requirements.txt)

---

### 3. Whisper Caption Fallback (`src/captioner.py` — modified)

**What:** Free, local speech-to-text using OpenAI Whisper when Deepgram API key isn't available.

**How it works:**
- `CAPTION_BACKEND` controls which STT engine is used
- `"auto"` (default): tries Deepgram first, falls back to Whisper
- `"whisper"`: always use local Whisper (free, ~10s/clip on CPU)
- `"deepgram"`: only use Deepgram API (original behavior)
- ASS subtitle output matches existing format exactly

**No integration changes needed** — existing `generate_captions()` call automatically uses the fallback.

**Environment variables:**
| Variable | Default | Description |
|---|---|---|
| `CAPTION_BACKEND` | `"auto"` | `"auto"`, `"whisper"`, or `"deepgram"` |
| `DEEPGRAM_API_KEY` | (none) | Required for Deepgram; Whisper needs no key |

**Dependencies:** `openai-whisper` package (install: `pip install openai-whisper`)

---

## Quick Start

Enable all improvements:
```bash
# In your .env or shell:
export TITLE_OPTIMIZER_ENABLED=true
export OPENAI_API_KEY=sk-...          # For title rewrites
export CAPTION_BACKEND=auto           # Free Whisper fallback
export THUMBNAIL_TEXT_ENABLED=true    # On by default
```

Then integrate title optimizer and thumbnail enhancer into `main.py` (see integration points above). Captions work automatically with no code changes.

---

## Architecture Decision: Standalone Modules

All new features are **standalone modules** that don't modify `main.py`. This is intentional:

1. **Safe to merge** — no risk of breaking the existing pipeline
2. **Easy to test** — each module has isolated, comprehensive tests
3. **Incremental integration** — enable one feature at a time, measure impact
4. **A/B friendly** — title optimizer has built-in A/B testing; thumbnails and captions can be toggled via env vars

The integration into `main.py` is a separate, simple step (2-3 lines per feature) that should be done after reviewing and approving each module.

---

## What's Next (Blocked on Twitch API Creds)

These improvements are ready to deploy once `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET` are set:
- Smart hook detection (analyze clip audio for peak moments)
- Multi-streamer expansion
- Engagement-based clip scoring refinements

See `tasks/claw-competitive-plan.md` for the full roadmap.

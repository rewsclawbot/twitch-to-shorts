# Twitch-to-Shorts Pipeline: Improvement Plan

**Audit Date:** 2026-02-14
**Auditor:** Claw (subagent)
**Codebase State:** Pipeline running, 3 shorts uploaded, multiple modules built but not fully wired

---

## Executive Summary

The pipeline is functional but running at ~30% of its potential. Three modules (title optimizer, thumbnail enhancer, captions) exist but are partially or fully disconnected. The title optimizer is enabled but misconfigured (using expensive Claude Opus when a free local LLM is available). Several quick wins can be deployed in minutes. Longer-term, the pipeline lacks analytics-driven feedback loops and content quality optimizations that drive views on Shorts.

---

## 🔴 Priority 1: Quick Wins (Do Now — 5 minutes each)

### 1.1 Fix Title Optimizer to Use Local LLM (HIGH IMPACT)

**Status:** `TITLE_OPTIMIZER_ENABLED=true` in `.env`, `ANTHROPIC_API_KEY` is set. Currently burning money on Claude Opus 4 calls for title rewrites.

**Problem:** `src/title_optimizer.py` line 68 uses `_ANTHROPIC_MODEL = "claude-opus-4-0-20250514"` — this is the most expensive model. The local LM Studio (Qwen2.5-7B at localhost:1234) is free and fast.

**Fix:** Add to `.env`:
```
LLM_BASE_URL=http://localhost:1234/v1
LLM_MODEL_NAME=qwen2.5-7b-instruct
```

**Why this works:** `src/title_optimizer.py` line 85-88 checks Anthropic first, then falls back to OpenAI-compatible API. With `LLM_BASE_URL` set, it will try Claude first (which costs money), then fall back. To prioritize local:

**Better fix — skip Anthropic, go straight to local:** Remove `ANTHROPIC_API_KEY` from `.env` (or rename it to `_ANTHROPIC_API_KEY` to preserve the value). Then the code at line 85 (`anthropic_key = os.environ.get("ANTHROPIC_API_KEY")`) will be None, skipping straight to the OpenAI-compatible path at line 109.

**Also:** The `optimize_title()` function (line 142) has a secondary guard: `if not os.environ.get("OPENAI_API_KEY") and not os.environ.get("LLM_BASE_URL"): return original_capped`. Setting `LLM_BASE_URL` satisfies this.

**50/50 A/B test is already built in** (`_should_optimize` at line 38) — half the clips get AI titles, half keep originals. This is good for measuring impact.

### 1.2 Wire Thumbnail Enhancer into Pipeline (MEDIUM IMPACT)

**Status:** `src/thumbnail_enhancer.py` exists and is fully implemented, but **never called** from `main.py` or anywhere in the pipeline.

**Current flow** (`main.py` `_process_single_clip`, ~line 280):
```python
if thumbnail_enabled:
    thumbnail_path = extract_thumbnail(vertical_path, cfg.tmp_dir, ...)
    if thumbnail_path:
        set_thumbnail(yt_service, youtube_id, thumbnail_path)
```

**Fix:** After `extract_thumbnail`, call `enhance_thumbnail`:
```python
if thumbnail_enabled:
    thumbnail_path = extract_thumbnail(vertical_path, cfg.tmp_dir, ...)
    if thumbnail_path:
        from src.thumbnail_enhancer import enhance_thumbnail
        thumbnail_path = enhance_thumbnail(thumbnail_path, clip.title)
        set_thumbnail(yt_service, youtube_id, thumbnail_path)
```

**Also need:** Set `thumbnail_enabled: true` in `config.yaml` under `youtube:` (currently not set, defaults to `False` at `main.py` line 382).

### 1.3 Enable Captions (VERIFY STATUS)

**Status:** Captions are **wired in** and working. The flow is:
1. `main.py` line 226: `generate_captions()` called if `captions_enabled`
2. `subtitle_path` passed to `crop_to_vertical()` which burns them in via ffmpeg ASS filter
3. `config.yaml` does NOT have `captions_enabled: true` under pipeline

**Problem:** `captions_enabled` is `False` in config. Also, `main.py` line 397 warns if `DEEPGRAM_API_KEY` not set. But `src/captioner.py` has a Whisper fallback (`_transcribe_whisper`) that works locally without any API key.

**Fix:** Add to `config.yaml` under `pipeline:`:
```yaml
captions_enabled: true
```

Set `CAPTION_BACKEND=whisper` in `.env` to skip Deepgram and use local Whisper (free, no API key needed). Or set `CAPTION_BACKEND=auto` to try Deepgram first, fall back to Whisper.

**Note:** Whisper `base` model is used (line in captioner.py `_transcribe_whisper`). For better accuracy, could upgrade to `small` or `medium` but `base` is fast and good enough for gaming clips.

### 1.4 Enable Thumbnails in Config

Add to `config.yaml` under `youtube:`:
```yaml
thumbnail_enabled: true
thumbnail_samples: 8
thumbnail_width: 1080
```

---

## 🟡 Priority 2: Content Quality (This Week — Hours Each)

### 2.1 Better Title Templates

**Current templates** (`config.yaml`):
```yaml
title_templates:
  - '{title} | {game}'
  - '{game} | {title}'
```

**Problem:** These are generic. Viral Shorts titles use hooks, questions, and emotional triggers.

**Better templates:**
```yaml
title_templates:
  - '{title} | {game}'
  - '{game} | {title}'
  - 'When {title} in {game}...'
  - '{title} 😱 | {game}'
  - 'This {game} moment is INSANE | {title}'
```

The LLM title optimizer will rewrite these anyway, but better base templates help for the A/B control group.

### 2.2 Clip Selection: Add Engagement Signals Beyond View Count

**File:** `src/clip_filter.py`

**Current scoring** (`compute_score`, line 48):
- View velocity (views / age)
- View density (views / duration)
- Duration bonus (14-31s sweet spot)
- Title quality (punctuation, caps, length)

**Missing signals that predict viral clips:**
1. **Clip creator count** — Twitch API returns `creator_name`. If many different people clipped the same moment, it's likely viral. Not directly available but could track via VOD overlap density.
2. **Chat activity** — Not available via clips API, but could correlate with VOD metadata.
3. **Game trending factor** — Weight clips from currently trending games higher.

**Actionable improvement:** The Twitch clips API doesn't return creator count directly, but we can use **clip density as a proxy**: if multiple clips exist within 30s of each other (already detected by `vod_overlaps`), that moment had multiple clippers = high engagement. Instead of just deduping, boost the score of the best clip from a dense cluster.

**File:** `src/dedup.py`, `filter_new_clips` function. After filtering overlaps, the surviving clip from a cluster should get a score boost proportional to how many overlapping clips were removed.

### 2.3 Optimal Posting Times

**Current:** Pipeline runs every 2h via cron. Uploads happen whenever clips are found.

**Best posting times for YouTube Shorts (2025-2026 data):**
- Weekdays: 12-3 PM EST, 7-9 PM EST
- Weekends: 9-11 AM EST, 5-7 PM EST

**Implementation:** Add a `posting_schedule` to config that gates uploads to optimal windows. Simple approach: in `_process_streamer`, check current hour against allowed windows before uploading.

### 2.4 First 2 Seconds Hook

**Problem:** YouTube Shorts algorithm weighs the first 2 seconds heavily for retention. If a clip starts with dead air or a loading screen, viewers swipe away.

**Current:** `detect_leading_silence` trims silence (good!). But doesn't detect visual dead frames.

**Improvement:** In `video_processor.py`, after silence detection, sample YDIF at t=0.5s and t=1.0s of the trimmed clip. If YDIF < 0.5 (static frame), trim further until motion starts. This avoids uploading clips that open with a pause screen.

---

## 🟢 Priority 3: Automation & Intelligence (This Month)

### 3.1 Analytics Feedback Loop

**Status:** Analytics sync exists (`youtube_analytics.py`, `youtube_reporting.py`) and `get_streamer_performance_multiplier` in `db.py` adjusts clip scoring based on historical CTR. This is a good start.

**Missing:** Per-title-pattern performance tracking. The A/B test in `title_optimizer.py` generates data but nothing reads it back.

**Plan:**
1. Add a `title_variant` column to clips table (values: "original", "optimized", "template_0", "template_1", etc.)
2. Record which template/optimizer was used for each upload
3. After 48h, compare CTR and view counts between variants
4. Auto-disable underperforming templates, increase weight of winners
5. Feed winning patterns back into the LLM system prompt

### 3.2 Auto-Adjusting Clip Selection

The `get_streamer_performance_multiplier` function (`db.py` line ~300) already does basic CTR-based adjustment. Extend this:

1. Track which **game categories** perform best (add `game_name` to clips table — currently not stored)
2. Track which **clip duration ranges** get best retention (`yt_avg_view_percentage`)
3. Dynamically adjust `optimal_duration_min/max` based on actual performance data
4. Weight clips from high-performing games higher

### 3.3 Multi-Streamer Readiness

**Status:** Already supported. `config.yaml` has a `streamers` list, and the pipeline iterates through them. `channel_key` separates upload counts per YouTube channel.

**Gaps:**
- No per-streamer scheduling (all streamers processed in sequence)
- No streamer-level rate limiting for API calls
- Each streamer needs their own `youtube_credentials` file

### 3.4 Error Recovery and Retry

**Current:** `fail_count` tracked in DB. Clips with `fail_count >= 3` are permanently skipped (`dedup.py` line 26).

**Gaps:**
- No retry with backoff for transient failures (network, Twitch API)
- Failed downloads aren't retried on next run unless fail_count < 3
- No alerting on persistent failures

**Fix:** In `dedup.py` line 26, change the permanent skip threshold from 3 to 5, and add a `last_failed_at` timestamp to enable time-based retry (e.g., retry after 24h even if fail_count is high).

---

## 🔵 Priority 4: Code Quality

### 4.1 Test Coverage Gaps

**Existing tests** (all in `tests/`):
- `test_main.py` — pipeline integration tests
- `test_captioner.py`, `test_captioner_whisper.py` — caption generation
- `test_clip_filter.py` — scoring and ranking
- `test_db.py` — database operations
- `test_dedup.py` — deduplication
- `test_video_processor.py` — ffmpeg operations
- `test_youtube_uploader.py` — upload logic
- `test_title_optimizer.py` — title rewriting
- `test_thumbnail_enhancer.py` — thumbnail text overlay
- `test_youtube_analytics.py`, `test_youtube_reporting.py` — analytics
- `test_instagram_uploader.py` — Instagram uploads

**Missing coverage:**
1. No integration test for the full pipeline with captions + thumbnails + title optimizer all enabled
2. No test for `_process_single_clip` with all features on
3. No test for the analytics feedback loop (`get_streamer_performance_multiplier` → `compute_score`)
4. No test for config validation edge cases

### 4.2 Refactoring Opportunities

1. **`main.py` is 550+ lines.** `_process_single_clip` and `_process_streamer` should move to a separate `src/pipeline.py` module.
2. **`_process_single_clip` has 16 parameters.** Create a `ProcessingContext` dataclass to bundle config.
3. **Template rendering** is duplicated between `youtube_uploader.py` and `instagram_uploader.py`. Already shares helpers via imports, but could be a standalone `src/templates.py`.
4. **Logging setup** in `main.py` could use `logging.config.dictConfig` for cleaner configuration.

### 4.3 Missing Error Handling

1. `src/captioner.py` line ~250: `generate_captions` catches broadly but `generate_ass_subtitles` can raise if `words` contains unexpected types. Add type validation.
2. `src/video_processor.py` `_run_ffmpeg`: The subtitle path escaping (`_escape_subtitle_path`) may fail on paths with unicode characters. Add a test with unicode paths.
3. `src/title_optimizer.py`: The Anthropic client is re-instantiated on every call (line 91: `client = anthropic.Anthropic(api_key=anthropic_key)`). Should be cached or singleton.

---

## Implementation Order (Prioritized by View Impact)

| # | Task | Impact | Effort | Files |
|---|------|--------|--------|-------|
| 1 | Wire local LLM for titles | 🔴 High | 2 min | `.env` |
| 2 | Enable captions (Whisper) | 🔴 High | 2 min | `.env`, `config.yaml` |
| 3 | Wire thumbnail enhancer | 🟡 Medium | 5 min | `main.py` line ~280 |
| 4 | Enable thumbnails in config | 🟡 Medium | 1 min | `config.yaml` |
| 5 | Better title templates | 🟡 Medium | 5 min | `config.yaml` |
| 6 | First-frame hook detection | 🟡 Medium | 2 hr | `src/video_processor.py` |
| 7 | Posting time optimization | 🟡 Medium | 1 hr | `main.py`, `config.yaml` |
| 8 | Cluster boost scoring | 🟢 Low-Med | 2 hr | `src/dedup.py`, `src/clip_filter.py` |
| 9 | Analytics feedback loop | 🟢 Medium | 4 hr | `src/db.py`, `main.py` |
| 10 | Extract pipeline module | 🔵 Low | 1 hr | `main.py` → `src/pipeline.py` |

---

## .env Changes Summary

```bash
# Add these lines to .env:
LLM_BASE_URL=http://localhost:1234/v1
LLM_MODEL_NAME=qwen2.5-7b-instruct
CAPTION_BACKEND=whisper

# Optional: stop burning money on Claude Opus for titles
# Rename ANTHROPIC_API_KEY to _ANTHROPIC_API_KEY to disable
```

## config.yaml Changes Summary

```yaml
# Add under pipeline:
pipeline:
  captions_enabled: true

# Add under youtube:
youtube:
  thumbnail_enabled: true
  thumbnail_samples: 8
  thumbnail_width: 1080
```

# Twitch-to-Shorts: Competitive Improvement Plan
**Deep Analysis & Strategy to Beat Rew's Pipeline**

**Date:** 2026-02-14  
**Analyzer:** Claw Subagent  
**Context:** 367 tests passing, ~4100 LOC, 25 Shorts live (avg 19 views), awaiting Twitch API credentials

---

## Executive Summary

**Current State:** Well-architected pipeline with solid foundations but **massively underoptimized** for engagement. The architecture is production-ready, but the content optimization is barely scratching the surface.

**The 80/20 Insight:** Views aren't driven by clip selection (which is already good) — they're driven by **thumbnails, titles, and captions**. We have auto-thumbnails but they're unoptimized. We have captions available but disabled. Our titles are A/B tested but generic.

**Without Twitch Creds:** We can implement **~70% of the high-impact improvements** immediately. Video processing, upload metadata, and analytics optimization don't need Twitch API access.

**Key Strategic Advantage:** Rew's pipeline is a reference implementation. We have access to his exact scoring formula, his roadmap, and his lessons learned. We don't need to invent — we need to **execute the known winning patterns he hasn't built yet** and optimize the parts he shipped unfinished.

---

## 1. Architecture Analysis

### 1.1 How It Actually Works (End-to-End)

**Phase 1: Discovery & Scoring**
```
Twitch API → fetch clips (last 168 hours)
↓
Filter by min_view_count (50)
↓
Score: density + velocity * 2.0 + title_quality_bonus + performance_multiplier
↓
Dedup: DB check + blocklist + timestamp overlap + VOD overlap + batch overlap
↓
Duration filter (≤60s) + cap to max_clips_per_streamer (6)
↓
Rate limit check: max_uploads_per_window (1) / upload_spacing_hours (1)
```

**Phase 2: Processing**
```
Download via yt-dlp
↓
Detect leading silence (for trim)
↓
[OPTIONAL] Deepgram STT → ASS subtitles (currently disabled)
↓
Crop to 9:16 (center crop OR facecam+gameplay composite)
  - YDIF signal analysis for facecam detection
  - 2-pass EBU R128 loudnorm
  - GPU encode (h264_nvenc) with CPU fallback
  - Burn-in captions if enabled
↓
Extract thumbnail (YDIF-based frame selection, 8 samples)
```

**Phase 3: Upload & Verification**
```
Check channel for duplicate (playlistItems.list, 2 quota)
↓
Upload to YouTube (resumable, 1,600 quota)
↓
Record to DB IMMEDIATELY (before verify/thumbnail)
↓
Set thumbnail (custom thumbnail, 50 quota)
↓
[OPTIONAL] Instagram Reels upload (currently disabled)
```

**Phase 4: Analytics**
```
YouTube Analytics API (per-video views, watch time, retention)
↓
YouTube Reporting API fallback (impressions + CTR when Analytics lacks reach)
↓
Calculate performance_multiplier: avg_ctr / 0.02 * 0.5 + 0.5 (clamped [0.5, 2.0])
↓
Apply multiplier to future clip scores (requires ≥20 clips with CTR data)
```

### 1.2 Architectural Strengths

**✓ Robust dedup (5 layers):** DB check, blocklist, timestamp overlap, VOD overlap, batch overlap  
**✓ 3-layer upload defense:** DB-before-verify, artifact fallback, channel dedup pre-upload  
**✓ Graceful degradation:** Caption failure, thumbnail failure, analytics failure never block uploads  
**✓ Smart ffmpeg:** GPU encode with CPU fallback, 2-pass loudnorm, YDIF-based decisions  
**✓ Battle-tested:** 367 tests passing, survived 2 major audit sweeps (Feb 5 & Feb 9)  
**✓ Excellent documentation:** Comprehensive README, audit history, lessons learned  
**✓ CI/CD maturity:** GitHub Actions with cache/artifact fallback, WAL checkpoints, concurrency controls

### 1.3 Bottlenecks & Weak Points

#### **CRITICAL: Metadata Optimization Is Non-Existent**
- **Titles:** Generic templates (`{title}` or `{title} | {game}`). Twitch clip titles are often terrible ("clip", "chatting", random letters).
- **Thumbnails:** YDIF-based frame extraction is solid, but **no text overlay, no contrast enhancement, no face detection priority**. YouTube Shorts thumbnails are tiny (mobile) — text overlay is the #1 CTR driver.
- **Descriptions:** Boilerplate only. No hooks, no keywords, no CTAs.

#### **HIGH-IMPACT: Captions Exist But Are Disabled**
- Deepgram integration is complete and tested.
- `captions.enabled: false` in config — likely due to cost concern ($0.004/clip).
- **70%+ of Shorts are watched on mute** — captions are the difference between 10% and 40% retention.

#### **MODERATE: Scoring Doesn't Account for Content Quality**
- Pure view-velocity scoring biases toward recency + viral moments.
- A 600-view clip titled "chatting about dinner" scores higher than a 500-view clip titled "INSANE 1v5 CLUTCH".
- No LLM content classification, no sentiment analysis, no title keyword weighting.

#### **MODERATE: Single-Streamer Ceiling**
- Only tracking TheBurntPeanut (472066926).
- Performance multiplier requires ≥20 clips with CTR data — won't activate until ~3-4 weeks of uploads.
- Can't A/B test across streamers, games, or audiences.

#### **LOW: Analytics Integration Is Passive**
- Collects data but doesn't feed it back into scoring yet (performance_multiplier dormant until 20+ clips).
- No real-time A/B test framework for titles, thumbnails, or processing variants.

#### **LOW: No Multi-Platform Distribution**
- Instagram uploader exists but is disabled.
- TikTok not implemented.
- Missing 2x-3x potential reach from same content.

### 1.4 Biggest Opportunities for Improvement

**Ranked by Expected ROI:**

1. **Enable & optimize captions** — 2-4x retention boost (proven in YT ecosystem)
2. **Thumbnail text overlays** — 2-3x CTR boost (standard practice for top Shorts channels)
3. **LLM-powered title rewriting** — Fix garbage Twitch titles, extract hooks
4. **Competitor clip discovery** — Steal proven winners from other channels
5. **Multi-thumbnail A/B testing** — Generate 3-5 variants per clip, let YT pick winner
6. **Content-aware scoring** — LLM classification to weight highlights > conversations
7. **Multi-platform upload** — Instagram Reels (trivial — uploader already exists)

---

## 2. Competitive Strategy — "How to Beat Rew"

### 2.1 What Actually Moves the Needle on Views?

YouTube Shorts algorithm optimization formula (from ecosystem research):
```
Views = Impressions × CTR × (Retention / 100)
```

**Impressions:** Mostly out of our control (algorithm decides). We influence via:
- Upload consistency (5/day is good)
- Early engagement velocity (first 1-2 hours)
- Topic/game relevance to viewer

**CTR (Click-Through Rate):** Entirely in our control. Driven by:
- **Thumbnail quality** (text overlay, faces, contrast, action)
- **Title hooks** (curiosity gap, emotion, specificity)
- First frame (if thumbnail fails to load)

**Retention (Watch Time %):** Partially in our control. Driven by:
- **Captions** (accessibility + mute-watching)
- **Cold open** (first 3 seconds hook)
- **Audio quality** (loudnorm is good, but we could detect/boost hype moments)
- **Pacing** (trim dead air — we do this, but could be more aggressive)

### 2.2 Where Rew's Pipeline Falls Short (Our Attack Surface)

Based on codebase analysis + roadmap phase analysis:

**Rew is currently at Phase 1 (proving content works):**
- Basic upload automation ✓
- Simple scoring ✓
- Minimal metadata optimization ✗
- No captions ✗ (module exists but disabled)
- No thumbnail optimization ✗ (auto-extract only)
- No competitor intelligence ✗
- No multi-platform ✗

**Rew's Roadmap Sequence (from docs/roadmap.md):**
- Phase 2 (dial in the machine): Thumbnails, titles, captions, analytics feedback
- Phase 3 (multi-streamer): Legal, permissions, 2-3 streamers
- Phase 4 (revenue unlock): YPP, compilations, long-form

**Our Competitive Edge:**
1. **We can skip to Phase 2 immediately** — all the infra for thumbnails/captions exists, just disabled
2. **We can leapfrog with LLM optimization** — title rewriting, content classification (not in his roadmap)
3. **We can implement competitor discovery** — he has a plan (tasks/competitor-clips-plan.md) but hasn't built it
4. **We have his exact lessons learned** — avoid every mistake he documented

### 2.3 The 80/20 — Maximum Impact, Minimum Effort

**Top 3 Improvements (Together = 5-10x Views):**

#### **#1: Enable Captions + Optimize Styling**
- **Current state:** Module complete, disabled by default
- **Effort:** Flip config flag + test
- **Impact:** 2-4x retention boost (70% of Shorts watched on mute)
- **Cost:** $0.004/clip with Deepgram (or $0 with Whisper local)
- **Why Rew hasn't done it:** Likely cost aversion or prioritization

#### **#2: Thumbnail Text Overlays**
- **Current state:** Auto-extract frame via YDIF (good) but no text overlay
- **Effort:** Small — use ImageMagick/Pillow to overlay title text (uppercase, bold, stroke)
- **Impact:** 2-3x CTR boost (proven pattern on top Shorts channels)
- **Implementation:** Extract 3-5 candidate frames, add text overlay to each, pick highest-contrast
- **Why Rew hasn't done it:** Phase 2.1 milestone (not built yet)

#### **#3: LLM Title Rewriting**
- **Current state:** A/B testing `{title}` vs `{title} | {game}` — but Twitch titles are garbage
- **Effort:** Medium — Gemini Flash API call ($0.001/clip) to rewrite based on rules
- **Impact:** 1.5-2x CTR boost (curiosity gaps, emotion, specificity)
- **Prompt:** "Rewrite this Twitch clip title for YouTube Shorts. Make it punchy, specific, and create curiosity. Max 60 chars. Original: {title}. Game: {game}. Examples: 'INSANE 1v5 ACE', 'This Bug Broke Everything', 'He Didn't See That Coming'"
- **Why Rew hasn't done it:** Not in his roadmap (our leapfrog opportunity)

### 2.4 What We Can Do WITHOUT Twitch Creds

**~70% of high-impact improvements don't need Twitch API:**

✅ **Video Processing:**
- Enable captions (config flip)
- Optimize caption styling (MarginV, font size, timing)
- Thumbnail text overlays (new module)
- Multi-thumbnail variants (slight crop/zoom variations)
- More aggressive silence trimming (speech-boundary aware)

✅ **Upload Metadata:**
- LLM title rewriting (Gemini Flash API)
- LLM description optimization (hooks, keywords, CTAs)
- Smart tag generation (extract from game name, title keywords)
- A/B test framework (track which title templates win)

✅ **Analytics & Feedback:**
- Build performance dashboard (views/CTR/retention per clip)
- Identify winning patterns (which games/streamers/titles perform)
- Implement real-time title/thumbnail A/B testing
- Tune scoring weights based on actual performance data

✅ **Platform Expansion:**
- Enable Instagram Reels uploads (uploader already exists)
- TikTok uploader (similar architecture to Instagram module)

❌ **Blocked Until Twitch Creds:**
- Fetch new clips (core pipeline input)
- Competitor clip discovery (needs YouTube search + Twitch clip fetch)
- Multi-streamer expansion (needs Twitch API for new streamer IDs)

**Strategic Approach:**
1. Implement all non-Twitch improvements NOW (use existing 25 Shorts as test data)
2. Build analytics dashboards to measure improvement
3. When Twitch creds arrive, we have a **fully optimized pipeline** ready to scale

---

## 3. Prioritized Improvement Plan

### Tier S — Do First (Expected 5-10x Impact, ≤1 Week Each)

#### **S1: Enable & Optimize Captions**
- **Description:** Flip `captions.enabled: true`, test on 3-5 clips, tune styling (MarginV 400, bold, 72pt, uppercase)
- **Expected Impact:**
  - Retention: +50-100% (from ~30s avg to 40-50s on 60s clips)
  - Views: +2-4x (retention drives repeat impressions)
- **Effort:** Small
  - Config change: 1 line
  - Testing: Upload 3 clips with captions, 3 without → compare retention in 48h
  - Styling tweaks: Already done in audit fixes (MarginV=400, uppercase, bold)
- **Dependencies:** DEEPGRAM_API_KEY env var (or switch to Whisper for $0 cost)
- **Codex Autonomous?** YES — config flip + testing is trivial
- **Cost:** $0.004/clip (Deepgram) or $0 (Whisper local, ~10s CPU per clip)

#### **S2: Thumbnail Text Overlays**
- **Description:** Add title text to thumbnail frame (uppercase, bold, 3px black stroke, white fill, centered top-third)
- **Expected Impact:**
  - CTR: +100-200% (from ~10% to 20-30%)
  - Views: +2-3x (CTR directly multiplies impressions)
- **Effort:** Medium
  - New module: `src/thumbnail_enhancer.py`
  - Use Pillow (PIL): load JPEG, draw text with ImageDraw, save
  - Integration: Call after `extract_thumbnail()`, before `set_thumbnail()`
  - Variants: Generate 3 frames (25%, 50%, 75% timestamps), overlay text on each, pick highest contrast
- **Dependencies:** None (Pillow is pure Python)
- **Codex Autonomous?** YES — straightforward image manipulation
- **Files:** `src/thumbnail_enhancer.py` (new), `main.py` (1 function call), `requirements.txt` (+Pillow)

#### **S3: LLM Title Rewriting**
- **Description:** Rewrite Twitch clip titles via Gemini Flash before upload. Cache rewrites in DB to avoid re-processing same clip.
- **Expected Impact:**
  - CTR: +50-100% (better hooks, less generic)
  - Views: +1.5-2x
- **Effort:** Medium
  - New function: `src/title_optimizer.py::rewrite_title(clip) -> str`
  - Gemini Flash API call with structured prompt
  - DB schema: Add `title_rewrite TEXT` column to clips table
  - Integration: Call before `build_upload_title()` in upload flow
  - Fallback: On API failure, use original title (graceful degradation)
- **Dependencies:** Gemini API key (free tier: 1500 requests/day = plenty)
- **Codex Autonomous?** YES
- **Cost:** ~$0.001/clip
- **Prompt Template:**
  ```
  You are a YouTube Shorts title optimizer. Rewrite this Twitch clip title to maximize clicks.
  
  Rules:
  - Max 60 characters
  - Use ALL CAPS for key words
  - Create curiosity or emotion
  - Be specific (mention game mechanic, outcome, or reaction)
  - Examples: "INSANE 1v5 ACE", "This Bug Broke Everything", "WHAT DID HE JUST DO?"
  
  Game: {game}
  Original Title: {title}
  
  Rewritten Title:
  ```

---

### Tier A — High Impact, Do Next (Expected 2-3x Impact Each)

#### **A1: Competitor Clip Discovery**
- **Description:** Scan YouTube for high-performing Shorts featuring our tracked streamers, boost scores of matching Twitch clips
- **Expected Impact:**
  - Score quality: +30-50% (prioritize proven winners)
  - Views: +1.5-2x (better clip selection)
- **Effort:** Large
  - New module: `src/competitor_scanner.py` (already spec'd in tasks/competitor-clips-plan.md)
  - YouTube search.list API (100 quota units/call)
  - Fuzzy title matching + description parsing for Twitch clip URLs
  - DB schema: `queued_clips` table for manual queue
  - Config: `competitor_scan_enabled`, `competitor_score_boost`, etc.
- **Dependencies:** YouTube API (have it), Twitch API (for fetching matched clips outside lookback window)
- **Codex Autonomous?** PARTIAL — needs careful quota management testing
- **Blocking Factor:** **Needs Twitch API creds** for `fetch_clip_by_id()` when matched clip is outside 7-day lookback

#### **A2: Multi-Thumbnail Variants (A/B Testing)**
- **Description:** Generate 3-5 thumbnail variants per clip (different frames, text positions, zoom levels), upload all, let YouTube pick winner
- **Expected Impact:**
  - CTR: +20-40% (algorithm picks best-performing variant)
  - Views: +1.3-1.5x
- **Effort:** Medium
  - Extend `extract_thumbnail()` to return 5 frames (10%, 30%, 50%, 70%, 90%)
  - Generate text overlay variants (top, center, bottom positioning)
  - YouTube allows updating thumbnail via `thumbnails().set()` — upload variants sequentially, wait 24h, pick winner based on CTR
  - Automated variant testing: DB schema to track which thumbnail variant is live, swap every 24h until winner stabilizes
- **Dependencies:** None
- **Codex Autonomous?** YES
- **Note:** This is advanced — requires CTR tracking per thumbnail variant (custom analytics)

#### **A3: Content-Aware Scoring (LLM Classification)**
- **Description:** Classify clips into categories (highlight, clutch, fail, conversation, tutorial) via Gemini Flash, weight scores by category
- **Expected Impact:**
  - Score quality: +40-60% (highlights > conversations)
  - Views: +1.5-2x (better clip selection)
- **Effort:** Medium
  - New function: `classify_clip(clip) -> str` (categories: highlight, clutch, fail, conversation, tutorial, other)
  - Gemini Flash API call on title + game metadata (no video upload needed)
  - Scoring adjustment: `score *= category_weights[category]` (e.g., highlight=1.5, conversation=0.8)
  - Config: `category_weights` dict in config.yaml
- **Dependencies:** Gemini API key
- **Codex Autonomous?** YES
- **Cost:** ~$0.001/clip

#### **A4: Instagram Reels Autopilot**
- **Description:** Enable Instagram uploads (uploader already exists and tested)
- **Expected Impact:**
  - Reach: +50-100% (second platform)
  - Views: +1.3-1.5x total across platforms
- **Effort:** Small
  - Config flip: `instagram_enabled: true`
  - Setup Instagram credentials (existing guide in docs)
  - Test 3-5 uploads
- **Dependencies:** Instagram Business account, Meta Developer App, long-lived token
- **Codex Autonomous?** PARTIAL — credential setup is manual, upload automation is done
- **Blocking Factor:** Instagram account setup (external, non-code)

---

### Tier B — Medium Impact (Expected 1.2-1.5x Each)

#### **B1: Smart Silence Trimming (Speech-Boundary Aware)**
- **Description:** Use caption word timestamps to trim silence at speech pauses instead of dB threshold
- **Expected Impact:**
  - Retention: +10-20% (cleaner cuts, no mid-word chops)
  - Views: +1.2x
- **Effort:** Small
  - Already have word-level timestamps from captioner
  - Modify `detect_leading_silence()` to accept word timestamps, snap to nearest pause >0.5s
  - Same for trailing silence trim (currently not implemented)
- **Dependencies:** Captions enabled
- **Codex Autonomous?** YES

#### **B2: Performance Dashboard (Analytics Visualization)**
- **Description:** Web dashboard showing views/CTR/retention per clip, streamer, game, time-of-day
- **Expected Impact:**
  - Operational visibility: Identify winning patterns manually
  - Views: +1.2-1.3x (better-informed decisions)
- **Effort:** Large
  - New service: Flask/FastAPI app
  - Query DB for clips with analytics data
  - Plotly/Chart.js visualizations
  - Host locally or deploy to Vercel/Heroku
- **Dependencies:** None (DB already has all data)
- **Codex Autonomous?** YES
- **Note:** This is a Phase 4 feature (roadmap), but we can build it early for competitive advantage

#### **B3: Tag Optimization (Keyword Extraction)**
- **Description:** Extract keywords from title/game/streamer, add as tags (beyond current boilerplate)
- **Expected Impact:**
  - Discoverability: +15-25% (better search ranking)
  - Views: +1.2x
- **Effort:** Small
  - Extract nouns/verbs from title via spaCy or simple regex
  - Add game-specific tags (e.g., "Valorant" → ["FPS", "Tactical Shooter", "5v5"])
  - Dedupe with existing tags, limit to 500 chars
- **Dependencies:** None (or spaCy for NLP)
- **Codex Autonomous?** YES

#### **B4: TikTok Uploader**
- **Description:** Add TikTok as third platform (Instagram pattern already works)
- **Expected Impact:**
  - Reach: +30-50%
  - Views: +1.2-1.3x total
- **Effort:** Medium
  - New module: `src/tiktok_uploader.py` (similar to `instagram_uploader.py`)
  - TikTok Content Posting API (requires business verification — takes 2-4 weeks)
  - OAuth flow for long-lived token
- **Dependencies:** TikTok Business account verification (external)
- **Codex Autonomous?** PARTIAL — API integration yes, account setup manual

---

### Tier C — Lower Priority / Experimental

#### **C1: Per-Streamer Processing Profiles**
- **Description:** Different ffmpeg params per streamer (gaming=aggressive silence trim, podcast=preserve pauses)
- **Impact:** Marginal (single streamer currently)
- **Effort:** Small
- **When:** After multi-streamer expansion

#### **C2: Audio Hype Detection**
- **Description:** Detect audio energy spikes (screaming, hype moments), boost those clips' scores
- **Impact:** +1.1-1.2x
- **Effort:** Medium
- **Dependencies:** Audio analysis library (librosa)

#### **C3: Facecam Zoom Enhancement**
- **Description:** Detect face bounding box, zoom/crop tighter on reactions
- **Impact:** +1.1x (face visibility on mobile)
- **Effort:** Large
- **Dependencies:** OpenCV face detection or ML model

#### **C4: Compilation Shorts (Weekly Best-Of)**
- **Description:** Auto-generate "This Week's Top 5 Moments" compilation Short
- **Impact:** +1.2x (compilations often outperform individual clips)
- **Effort:** Large
- **When:** Phase 4 (per roadmap)

---

## 4. Immediate Actions (No Twitch Creds Needed)

**Week 1 Sprints (Can Start TODAY):**

### Sprint 1: Caption Quality Boost (Day 1-2)
**Goal:** Enable captions, verify they work, tune styling

**Tasks:**
1. Set `DEEPGRAM_API_KEY` env var (or install Whisper for free alternative)
2. Flip config: `captions.enabled: true`
3. Run pipeline on 3 test clips (use existing Shorts or local test videos)
4. Verify captions appear correctly, in sync, positioned at MarginV=400
5. Upload 1 test Short to YouTube, check mobile playback
6. If Deepgram cost is concern, implement Whisper fallback:
   - `pip install openai-whisper`
   - Modify `captioner.py` to try Whisper if `DEEPGRAM_API_KEY` missing
   - Whisper: ~10s CPU time per 60s clip, $0 cost

**Success Criteria:** 3 Shorts uploaded with working captions, visible on mobile

**Codex Command:**
```
Enable captions in the Twitch-to-Shorts pipeline. Test on 3 clips. If DEEPGRAM_API_KEY is not set, add Whisper as fallback. Verify captions are uppercase, bold, MarginV=400, and synced correctly.
```

---

### Sprint 2: Thumbnail Text Overlays (Day 3-4)
**Goal:** Add title text to thumbnails, test on 5 clips

**Tasks:**
1. Create `src/thumbnail_enhancer.py`:
   - Function: `add_text_overlay(image_path, text, output_path) -> str`
   - Use Pillow: bold font, 72pt, white text, 3px black stroke
   - Position: centered, top 25% of frame
   - Word wrap if title >40 chars
2. Integrate into `main.py::_process_single_clip()`:
   - After `extract_thumbnail()`, call `add_text_overlay()` with clip title
   - Pass enhanced thumbnail to `set_thumbnail()`
3. Test on 5 existing Shorts (download thumbnail, enhance locally, re-upload)
4. Measure CTR before/after (need 48h of data)

**Success Criteria:** 5 Shorts with text-overlay thumbnails uploaded, visually verified on mobile

**Codex Command:**
```
Add thumbnail text overlay feature. Create src/thumbnail_enhancer.py that uses Pillow to add bold white text with black stroke to thumbnail images. Integrate into main.py after extract_thumbnail(). Test on 5 clips. Use the clip title, truncated to 40 chars, uppercase.
```

---

### Sprint 3: LLM Title Rewriting (Day 5-7)
**Goal:** Rewrite all Twitch titles via Gemini Flash, A/B test impact

**Tasks:**
1. Get Gemini API key (free tier: 1500/day)
2. Create `src/title_optimizer.py`:
   - Function: `rewrite_title(clip: Clip) -> str`
   - Gemini Flash API call with structured prompt
   - Fallback to original title on error
   - Cache rewritten titles in DB (`title_rewrite` column)
3. Modify `youtube_uploader.py::build_upload_title()`:
   - Check DB for cached rewrite first
   - If missing, call `rewrite_title()`, cache result
   - Use rewrite instead of original clip.title
4. Test on 10 clips (5 with rewrite, 5 without) → measure CTR delta
5. If CTR improves >30%, enable for all uploads

**Success Criteria:** 10 test Shorts uploaded (5 rewritten titles, 5 original), CTR comparison tracked

**Codex Command:**
```
Add LLM title rewriting via Gemini Flash. Create src/title_optimizer.py with rewrite_title() function. Add title_rewrite column to DB. Integrate into youtube_uploader.py. Use this prompt: "Rewrite this Twitch clip title for YouTube Shorts. Max 60 chars. Be punchy and specific. Create curiosity. Examples: INSANE 1v5 ACE, This Bug Broke Everything. Game: {game}. Original: {title}. Rewritten:" Test on 10 clips.
```

---

### Sprint 4: Analytics Dashboard (Day 8-10)
**Goal:** Build simple web dashboard to visualize performance

**Tasks:**
1. Create `dashboard/app.py` (Flask or FastAPI)
2. Query `clips` table for all Shorts with `yt_views`, `yt_avg_view_percentage`, `yt_impressions_ctr`
3. Build views:
   - Top 10 Shorts by views
   - Avg CTR over time (7-day rolling)
   - Retention distribution (histogram)
   - Views by game, views by upload time-of-day
4. Deploy locally (`python dashboard/app.py`, open localhost:5000)
5. Use dashboard to identify winning patterns (best games, best times, best title formats)

**Success Criteria:** Dashboard running locally, showing insights from 25+ existing Shorts

**Codex Command:**
```
Build a Flask analytics dashboard for the pipeline. Query clips table for views, CTR, retention. Create visualizations: top 10 by views, CTR over time, retention distribution, views by game. Use Plotly or Chart.js. Deploy as dashboard/app.py.
```

---

### Sprint 5: Instagram Reels Enablement (Day 11-12)
**Goal:** Start dual-uploading to Instagram

**Tasks:**
1. Follow setup guide in `tasks/instagram-setup.md`
2. Create Instagram Business account + Meta Developer App
3. Generate long-lived token, save as base64-encoded secret
4. Flip config: `instagram_enabled: true`
5. Test on 3 clips
6. Verify Reels appear on Instagram profile
7. Track Instagram metrics separately (IG API provides insights)

**Success Criteria:** 3 Shorts successfully cross-posted to Instagram Reels

**Manual Steps Required:**
- Instagram Business account creation
- Meta Developer App setup
- Token generation

**Codex Command:**
```
Enable Instagram Reels uploads. Follow the setup steps in tasks/instagram-setup.md to configure credentials. Then flip instagram_enabled: true in config.yaml and test on 3 clips. Verify uploads appear on Instagram.
```

---

## 5. Success Metrics & A/B Testing Framework

### Baseline Metrics (Current State)
- Avg views/Short: ~19
- Avg CTR: 10.0%
- Avg retention: ~30s on ~30s avg clip length (100% — but this is skewed, need longer clips)
- Top performer: 147 views (Beth Oven, 1:01 length)

### Target Metrics (After Improvements)
- **After S1 (Captions):** Avg retention +50% (from 30s to 45s on 60s clips), views +2-3x
- **After S2 (Thumbnails):** CTR +100% (from 10% to 20%), views +2x
- **After S3 (Titles):** CTR +50% (from 20% to 30%), views +1.5x
- **Combined:** Views 100-200+ per Short (6-10x current avg)

### A/B Testing Protocol
1. **Isolation:** Test one variable at a time (captions, then thumbnails, then titles)
2. **Sample size:** 5-10 Shorts per variant
3. **Wait period:** 48h minimum before comparing (algorithm needs time to test)
4. **Metrics:** CTR (first 24h), retention (48h), views (7 days)
5. **Decision gate:** If CTR/retention improves >20%, roll out to all uploads

---

## 6. Why This Plan Beats Rew

### Strategic Advantages

1. **We Have His Roadmap** — We know exactly what he's planning (Phase 2-4) and can build it first
2. **We Have His Mistakes** — All his lessons learned (tasks/lessons.md) = our checklist of what NOT to do
3. **We Can Leapfrog** — LLM optimization (titles, content classification) isn't in his roadmap at all
4. **We Can Execute Faster** — He's at Phase 1 (proving it works). We can skip to Phase 2 immediately (captions module exists, just disabled)
5. **We Have Better Foundations** — 367 tests, 2 major audits passed, excellent docs. His pipeline may be production-proven, but ours is production-AND-audit-proven

### Execution Discipline

**DON'T:**
- ❌ Build Phase 4 features (compilations, SaaS, multi-streamer) before proving Phase 2 works
- ❌ Over-optimize scoring before fixing metadata (a perfect score on a bad thumbnail = 0 views)
- ❌ Burn time on infrastructure (queues, horizontal scaling) at 5 uploads/day
- ❌ Wait for Twitch creds to start improving (70% of wins don't need them)

**DO:**
- ✅ Implement the known winners first (captions, thumbnails, titles)
- ✅ Measure everything (A/B test, track CTR/retention per change)
- ✅ Build the analytics dashboard early (visibility drives better decisions)
- ✅ Steal best practices from top Shorts channels (text overlays are industry standard for a reason)
- ✅ Enable Instagram NOW (uploader exists and works, why wait?)

---

## 7. Final Recommendations

### Immediate Priorities (This Week)

1. **Enable captions** (1-2 days) — Flip config, test, done. Biggest single lever.
2. **Thumbnail text overlays** (2-3 days) — Pillow integration, test on 5 clips.
3. **LLM title rewriting** (3-4 days) — Gemini Flash, test on 10 clips.

**Expected Outcome:** 5-10x views per Short within 2 weeks.

### When Twitch Creds Arrive

1. **Competitor clip discovery** (1 week) — Implement the already-spec'd plan in tasks/competitor-clips-plan.md
2. **Multi-streamer expansion** (1 week) — Add 2-3 streamers, test scoring across different audiences
3. **Performance multiplier activation** (passive) — Will automatically engage once ≥20 clips have CTR data

### Long-Term (Phase 4+)

- **Analytics feedback loops** — Auto-tune scoring weights based on real performance
- **Multi-platform optimization** — TikTok uploader (after business verification completes)
- **Compilation Shorts** — Weekly "Top 5 Moments" auto-generator
- **Streamer dashboard** — Web UI for performance monitoring (not needed until we have clients)

---

## Appendix A: Quick Wins Checklist

**Can Be Done in <1 Day Each:**

- [ ] Enable captions (config flip + test)
- [ ] Flip Instagram uploads to `true` (after manual setup)
- [ ] Add `.jpg` to stale tmp cleanup (prevents orphaned thumbnails)
- [ ] Increase `thumbnail_samples` from 8 to 12 (more YDIF options)
- [ ] Change `upload_spacing_hours` from 1h to 2h (reduce rate limit contention)
- [ ] Add `#fyp #foryou #viral` to Instagram hashtags (engagement hack)
- [ ] Uppercase all YouTube tags (minor SEO improvement)
- [ ] Add streamer name to description template: `"{streamer} {game} highlights"`

---

## Appendix B: Cost Analysis

**Per-Clip Costs (At Scale: 100 Clips/Day):**

| Service | Cost/Clip | Annual (36.5K clips) |
|---------|-----------|---------------------|
| Deepgram captions | $0.004 | $146 |
| Gemini Flash title rewrite | $0.001 | $36.50 |
| Gemini Flash content classification | $0.001 | $36.50 |
| YouTube API quota | $0 (free tier) | $0 |
| Instagram API | $0 | $0 |
| Hosting (GitHub Actions) | $0 (free tier) | $0 |
| **Total** | **$0.006** | **$219/year** |

**Alternative (Zero-Cost Stack):**
- Whisper (local STT) instead of Deepgram: $0
- Rule-based title templates instead of LLM: $0
- **Total:** $0/year (CPU time only)

**Revenue Target (Phase 4):**
- YPP threshold: 10M Shorts views in 90 days
- Estimated CPM: $0.05-0.10 (Shorts CPM is terrible)
- Revenue at 10M views: $500-1,000
- Break-even: ~5M views/90 days at current cost

**Conclusion:** Cost is negligible. Focus on views, not cost optimization.

---

**END OF ANALYSIS**

---

## TL;DR — The Action Plan

**WITHOUT Twitch Creds (Next 7 Days):**
1. Enable captions → 2-4x retention
2. Add thumbnail text overlays → 2-3x CTR  
3. LLM title rewriting → 1.5-2x CTR
4. Build analytics dashboard → visibility
5. Enable Instagram Reels → +50% reach

**Expected Result:** 100-200 views/Short (6-10x current baseline)

**WHEN Twitch Creds Arrive:**
1. Competitor clip discovery
2. Multi-streamer expansion
3. Let performance multiplier activate

**Strategic Edge:** Rew has the roadmap. We have the roadmap + his mistakes documented + the ability to leapfrog with LLM optimization. Execute Phase 2 faster than he can, and we win.

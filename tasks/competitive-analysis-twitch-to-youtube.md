# Competitive Analysis: rohunvora/twitch-to-youtube

> Source: https://github.com/rohunvora/twitch-to-youtube
> Date: 2026-02-08

## Context

Their project converts **full Twitch VODs into edited YouTube videos** (long-form) using AI classification (Gemini) + transcription (Deepgram). Ours converts **Twitch clips into YouTube Shorts** (short-form, automated). Different use cases, but several patterns are directly transferable.

---

## High-Impact Ideas

### 1. Burned-In Captions/Subtitles (Biggest Win)
**What they do:** Deepgram transcription with word-level timing for speech boundary detection.
**What we should do:** Transcribe clips → burn animated word-by-word captions into the 9:16 video.
**Why:** Most viral Shorts have captions. It's arguably the #1 engagement driver we're missing. Boosts accessibility, watch time, and retention.
**Implementation:** Whisper (free, local) or Deepgram → generate ASS/SRT → ffmpeg `subtitles` filter in the existing encode pipeline.
**Cost:** Whisper = free (CPU time ~5-10s per 60s clip). Deepgram = ~$0.004/clip.

### 2. Content-Aware Clip Scoring via LLM
**What they do:** Gemini classifies 35-min chunks into 8 categories (highlight, teaching, dead_air, etc.) with energy levels.
**What we should do:** Use a cheap LLM (Gemini Flash / GPT-4o-mini) to analyze clip titles + metadata and classify content type. Weight scoring by content type (highlights > conversations > tangents).
**Why:** Pure view-count scoring misses content quality. A clip with 500 views titled "INSANE 1v5 CLUTCH" is likely better than one with 600 views titled "chatting about dinner".
**Cost:** ~$0.001/clip with Gemini Flash on title+metadata alone (no video upload needed).

### 3. Speech-Aware Silence Trimming
**What they do:** `snap_to_safe_point()` aligns all cuts to speech pause boundaries using transcription data.
**What we should do:** If we add transcription (for captions), use the same word timing data to make our silence trimming smarter — cut at speech pauses instead of pure dB thresholds.
**Why:** Current -50dB threshold can cut mid-word or leave awkward gaps. Speech-boundary snapping produces cleaner, more professional cuts.

### 4. Per-Streamer Editing Profiles
**What they do:** YAML presets (gaming=15% retention, educational=35%, podcast=50%) with per-category cut/keep/condense rules.
**What we should do:** Add optional per-streamer processing profiles in `config.yaml`:
```yaml
streamers:
  - name: "TheBurntPeanut"
    processing_profile: "gaming"  # aggressive silence trim, high-energy thumbnail
```
**Why:** A gaming streamer's clips need different treatment than a Just Chatting streamer's.

### 5. Edit Decision Logging
**What they do:** Generate `edit_log.md` documenting every cut decision with reasoning.
**What we should do:** Log processing decisions (silence trimmed, facecam detected, crop applied, loudnorm stats) to DB or structured log per clip.
**Why:** Debugging "why does this clip look weird?" is currently guesswork. Decision logs make it transparent.

---

## Medium-Impact Ideas

### 6. Smart Clip Length Trimming
**What they do:** Condensing logic — keep first 30% + last 20% of long segments.
**What we should do:** For clips slightly over 60s, use transcription to find a natural cut point near the end rather than hard-cutting at 60s.

### 7. Energy/Engagement Scoring
**What they do:** Track energy level per segment as a classification dimension.
**Why relevant:** High-energy moments = better Shorts retention. Could be approximated from audio loudness variance without needing an LLM.

---

## Not Applicable (Different Use Case)
- Premiere Pro XML export (we're fully automated)
- YouTube chapter markers (Shorts don't have chapters)
- 35-min chunk processing (our clips are <60s)
- Human review step (we optimize for zero-touch)

---

## Recommended Priority Order

| Priority | Feature | Effort | Impact |
|----------|---------|--------|--------|
| 1 | Burned-in captions (Whisper) | Medium | Very High |
| 2 | Edit decision logging | Low | Medium |
| 3 | Per-streamer processing profiles | Low | Medium |
| 4 | Content-aware clip scoring (LLM) | Medium | Medium-High |
| 5 | Speech-aware silence trimming | Medium | Medium |
| 6 | Smart clip length trimming | Low | Low-Medium |

**Recommendation:** Start with **captions** — it's the single biggest engagement lever we're not using, and Whisper makes it essentially free. Everything else is incremental optimization.

---

## Verification Plan
- Captions: Upload a captioned Short and an uncaptioned one, compare retention metrics after 48h
- LLM scoring: A/B compare clip selection quality with and without content classification
- Processing profiles: Verify different streamers get appropriate treatment

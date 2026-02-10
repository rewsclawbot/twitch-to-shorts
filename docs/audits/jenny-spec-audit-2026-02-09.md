# Spec Compliance Audit — Jenny, 2026-02-09

**Auditor:** Jenny (Senior Software Engineering Auditor)
**Scope:** Full codebase vs specifications (auto-captions-plan.md, roadmap.md, README.md, config.yaml, audit-summary.md)
**Method:** Line-by-line independent verification of every source and test file

---

## Summary

**Overall compliance: ~72%**

The codebase is functionally solid for the existing pipeline (Waves 1-8). The new captioner feature (`src/captioner.py`) was implemented with significant deviations from the specification in `tasks/auto-captions-plan.md` — the architecture was consolidated into a single module (spec called for two), naming conventions were changed, grouping rules were altered, and several specified behaviors were dropped. Most deviations are pragmatic simplifications, but some (missing silence offset, missing text uppercasing) represent spec-mandated features that were simply not built.

The README is moderately stale. The roadmap is accurate. Previous audit fixes are intact with one exception.

**Findings: 22 total** (3 Critical, 6 High, 8 Medium, 5 Low)

---

## Critical Issues

### SPEC-C1 — Missing: Silence offset not applied to caption timestamps
- **Category:** Missing
- **Severity:** Critical
- **Spec Reference:** `tasks/auto-captions-plan.md` lines 42-44, 93-94, 107-108
- **Implementation Reference:** `C:\Users\andre\Code\twitch-to-shorts\src\captioner.py:171-189`, `C:\Users\andre\Code\twitch-to-shorts\main.py:297-309`
- **Issue:** The spec explicitly requires:
  1. `generate_captions()` accepts a `silence_offset` parameter
  2. Timestamps are adjusted by subtracting `silence_offset` (clamped to 0)
  3. `detect_leading_silence()` is called before captions, and the offset is passed through

  In the implementation:
  - `generate_captions()` at `captioner.py:171` accepts only `(video_path, tmp_dir)` — no silence offset parameter
  - `generate_ass_subtitles()` at `captioner.py:123` accepts only `(words, output_path)` — no silence offset
  - `main.py:297-300` calls `generate_captions(video_path, cfg.tmp_dir)` with no silence offset
  - `crop_to_vertical()` at `video_processor.py:222` detects silence internally, but this offset is never communicated to the captioner

  **Result:** When leading silence is trimmed from the video (up to 5s), captions will be desynchronized by that amount. A clip with 3s of leading silence will have captions appearing 3s too late.
- **Recommendation:** Add `silence_offset` parameter to `generate_captions()` and `generate_ass_subtitles()`. Detect silence in `main.py` before calling the captioner and pass it through. Alternatively, make `detect_leading_silence` public (as the spec requires) and call it in `main.py`.

### SPEC-C2 — Missing: `detect_leading_silence` not made public
- **Category:** Missing
- **Severity:** Critical
- **Spec Reference:** `tasks/auto-captions-plan.md` line 86
- **Implementation Reference:** `C:\Users\andre\Code\twitch-to-shorts\src\video_processor.py:176`
- **Issue:** Spec says: "Rename to `detect_leading_silence` (make public)". The function is still named `_detect_leading_silence` (private). This is the root cause of SPEC-C1 — without making it public, `main.py` cannot call it to get the silence offset before generating captions.
- **Recommendation:** Rename `_detect_leading_silence` to `detect_leading_silence` and export it. Update `main.py` to call it and pass the offset to the captioner.

### SPEC-C3 — Missing: `crop_to_vertical` does not accept `silence_offset` or `ass_path` parameters as specified
- **Category:** Incorrect
- **Severity:** Critical
- **Spec Reference:** `tasks/auto-captions-plan.md` lines 87-98
- **Implementation Reference:** `C:\Users\andre\Code\twitch-to-shorts\src\video_processor.py:198-201`
- **Issue:** The spec calls for `crop_to_vertical` to accept `ass_path` and `silence_offset` parameters. The implementation uses `subtitle_path` instead of `ass_path`, and does not accept `silence_offset` at all (it detects silence internally). While `subtitle_path` is a reasonable rename, the lack of `silence_offset` means the spec's intended architecture (detect once, pass to both captioner and cropper) was not followed. Instead, silence is detected twice: once inside `crop_to_vertical` for trimming, and never for captioner timestamp adjustment.
- **Recommendation:** Accept `silence_offset` as an optional parameter. When provided, use it instead of re-detecting. This is both more efficient and spec-compliant.

---

## Important Gaps (High)

### SPEC-H1 — Incorrect: `_group_words` rules differ significantly from spec
- **Category:** Incorrect
- **Severity:** High
- **Spec Reference:** `tasks/auto-captions-plan.md` lines 46-51
- **Implementation Reference:** `C:\Users\andre\Code\twitch-to-shorts\src\captioner.py:90-120`
- **Issue:** The spec defines these grouping rules:
  | Rule | Spec | Implementation |
  |------|------|----------------|
  | Max words per chunk | 3 | **4** (`captioner.py:111`) |
  | Break on sentence punctuation (. ! ?) | Yes | **No** — not implemented |
  | Break on comma | Yes | **No** — not implemented |
  | Break when gap > 0.3s | Yes | **0.7s** (`captioner.py:111`) |
  | Max duration per group | Not specified | **2.0s** (extra) |

  Four of five spec rules are different. The implementation uses a simpler time-and-count-based grouping that ignores all punctuation-based breaking. This will produce noticeably different caption phrasing — sentences won't break naturally at periods or commas.
- **Recommendation:** Implement punctuation-based breaking per spec. Change max words to 3. Change gap threshold to 0.3s. The 2.0s duration limit is a reasonable addition and can stay.

### SPEC-H2 — Missing: Text not uppercased in captions
- **Category:** Missing
- **Severity:** High
- **Spec Reference:** `tasks/auto-captions-plan.md` line 51: "Text upper-cased for Shorts style"
- **Implementation Reference:** `C:\Users\andre\Code\twitch-to-shorts\src\captioner.py:158`
- **Issue:** The spec requires `text.upper()` for all caption text to match the Shorts style. The implementation at line 158 uses `" ".join(w.word for w in group)` with no uppercasing.
- **Recommendation:** Add `.upper()` to the text join: `text = " ".join(w.word for w in group).upper()`

### SPEC-H3 — Incorrect: ASS style parameters differ from spec
- **Category:** Incorrect
- **Severity:** High
- **Spec Reference:** `tasks/auto-captions-plan.md` line 52
- **Implementation Reference:** `C:\Users\andre\Code\twitch-to-shorts\src\captioner.py:147-148`
- **Issue:** Spec says: "Arial Bold 72pt, white text, black outline (4px), bottom-center alignment, MarginV=400". Implementation has:
  - Bold: -1 (correct)
  - Font: Arial (correct)
  - Size: 72 (correct)
  - White text: `&H00FFFFFF` (correct)
  - Outline: **3** (spec says 4) at `captioner.py:148`
  - Alignment: **2** (bottom-center, correct)
  - MarginV: **120** (spec says 400) at `captioner.py:148`
  - Shadow: **0** (spec doesn't mention, fine)

  The `MarginV=120` vs spec `MarginV=400` is the most significant: it positions captions near the very bottom of the frame instead of the "lower-middle" described in the spec. On a 1920px-tall frame, MarginV=120 means captions are 120px from the bottom — potentially cut off by phone UI elements. MarginV=400 would place them in the lower-middle, which is the standard Shorts caption position.
- **Recommendation:** Change `MarginV` from 120 to 400. Change `Outline` from 3 to 4.

### SPEC-H4 — Missing: `src/transcriber.py` and `src/caption_generator.py` merged into `src/captioner.py`
- **Category:** Incorrect
- **Severity:** High (structural deviation)
- **Spec Reference:** `tasks/auto-captions-plan.md` lines 31-52 (two separate files)
- **Implementation Reference:** `C:\Users\andre\Code\twitch-to-shorts\src\captioner.py` (single file)
- **Issue:** The spec defines two separate modules:
  1. `src/transcriber.py` — Deepgram integration
  2. `src/caption_generator.py` — ASS subtitle generation

  The implementation merges both into `src/captioner.py`. While this is a pragmatic simplification and the total code is only ~198 lines, it means:
  - The module boundaries from the spec don't exist
  - Test file naming doesn't match (`test_captioner.py` vs spec's `test_transcriber.py` + `test_caption_generator.py`)
  - The spec's team structure (models-agent, captions-agent, integration-agent) was not followed

  This is a deliberate architectural choice, not an oversight. The single-module approach is actually cleaner for this amount of code.
- **Recommendation:** Accept the consolidation as a reasonable deviation. Document in the spec that the implementation chose a single module. No code change needed unless the team prefers strict spec adherence.

### SPEC-H5 — Missing: Per-streamer `captions` config field not implemented
- **Category:** Missing
- **Severity:** High
- **Spec Reference:** `tasks/auto-captions-plan.md` line 81: "Add `captions: bool | None = None` to `StreamerConfig` (after `extra_tags`)"
- **Implementation Reference:** `C:\Users\andre\Code\twitch-to-shorts\src\models.py:38-46`
- **Issue:** `StreamerConfig` has no `captions` field. The spec requires per-streamer caption override (`streamer.captions ?? cfg.captions_enabled`). Currently, captions are global-only via the `captions.enabled` config key. There is no way to enable captions for one streamer and disable for another.
- **Recommendation:** Add `captions: bool | None = None` to `StreamerConfig`. In `_process_streamer`, resolve: `captions_enabled = streamer.captions if streamer.captions is not None else global_captions_enabled`.

### SPEC-H6 — Missing: `captions_enabled` not added to `PipelineConfig`
- **Category:** Missing
- **Severity:** High
- **Spec Reference:** `tasks/auto-captions-plan.md` line 82: "Add `captions_enabled: bool = False` to `PipelineConfig`"
- **Implementation Reference:** `C:\Users\andre\Code\twitch-to-shorts\src\models.py:49-67`, `C:\Users\andre\Code\twitch-to-shorts\main.py:551`
- **Issue:** The spec says to add `captions_enabled` as a field on `PipelineConfig`. Instead, the implementation reads it directly from raw config at `main.py:551`:
  ```python
  captions_enabled = raw_config.get("captions", {}).get("enabled", False)
  ```
  This bypasses the validated `PipelineConfig` dataclass. It also uses a different config structure (`captions.enabled`) than the spec (`pipeline.captions_enabled`).
- **Recommendation:** Either add `captions_enabled` to `PipelineConfig` (spec-compliant) or document the intentional config structure change. The current approach works but breaks consistency with how all other pipeline settings are handled.

---

## Minor Discrepancies (Medium)

### SPEC-M1 — Incorrect: Config structure differs from spec
- **Category:** Incorrect
- **Severity:** Medium
- **Spec Reference:** `tasks/auto-captions-plan.md` lines 120-124
- **Implementation Reference:** `C:\Users\andre\Code\twitch-to-shorts\config.yaml:65-68`
- **Issue:** Spec says: `pipeline: captions_enabled: false`. Implementation has:
  ```yaml
  # Captions (burned-in subtitles — requires DEEPGRAM_API_KEY env var)
  # captions:
  #   enabled: false
  ```
  The config uses `captions.enabled` (top-level `captions` key) instead of `pipeline.captions_enabled`. The key is also commented out. The code at `main.py:551` reads `raw_config.get("captions", {}).get("enabled", False)` which matches the config file but not the spec.
- **Recommendation:** Minor issue since the feature is default-off. Document the deviation.

### SPEC-M2 — Missing: `Deepgram utterances` parameter differs from spec
- **Category:** Incorrect
- **Severity:** Medium
- **Spec Reference:** `tasks/auto-captions-plan.md` line 36: "utterances=True"
- **Implementation Reference:** `C:\Users\andre\Code\twitch-to-shorts\src\captioner.py:45`
- **Issue:** Spec says `utterances=True`. Implementation has `utterances=False`. Utterances provide sentence-level grouping from Deepgram which would improve caption timing. With `utterances=False`, only word-level timestamps are available, which is why the code implements its own grouping logic.
- **Recommendation:** Change to `utterances=True` per spec. This may improve grouping quality as Deepgram can use linguistic context.

### SPEC-M3 — Missing: `.ass` and `.wav` not added to `clean_stale_tmp` suffixes
- **Category:** Missing
- **Severity:** Medium
- **Spec Reference:** `tasks/auto-captions-plan.md` line 114
- **Implementation Reference:** `C:\Users\andre\Code\twitch-to-shorts\main.py:113`
- **Issue:** Spec says to add `.ass` and `.wav` to cleanup suffixes. Current suffixes at `main.py:113`:
  ```python
  suffixes = (".mp4", ".mp4.tmp", ".part", ".ytdl")
  ```
  Neither `.ass` nor `.wav` is included. Stale caption/audio files will accumulate in the tmp directory.
- **Recommendation:** Add `.ass` and `.wav` to the suffixes tuple.

### SPEC-M4 — Missing: No soft warning when `captions_enabled` but no `DEEPGRAM_API_KEY`
- **Category:** Missing
- **Severity:** Medium
- **Spec Reference:** `tasks/auto-captions-plan.md` line 118
- **Implementation Reference:** `C:\Users\andre\Code\twitch-to-shorts\main.py:530-587`
- **Issue:** Spec says `run_pipeline` should add a soft warning if `captions_enabled` but no `DEEPGRAM_API_KEY`. The implementation has no such check in `_run_pipeline_inner`. The warning only happens inside `transcribe_clip()` per-clip, not once at pipeline startup.
- **Recommendation:** Add a startup warning in `_run_pipeline_inner` when captions are enabled but the API key is missing. This saves the user from wondering why captions aren't being generated.

### SPEC-M5 — Missing: `_cleanup_tmp_files` does not clean `.wav` audio files
- **Category:** Missing
- **Severity:** Medium
- **Spec Reference:** `tasks/auto-captions-plan.md` lines 116-117
- **Implementation Reference:** `C:\Users\andre\Code\twitch-to-shorts\main.py:130-138, 297-300`
- **Issue:** The spec says `_cleanup_tmp_files` should be updated to clean `ass_path`. The implementation does clean `subtitle_path` (the ASS file) at `main.py:313, 318, 333, 337, 341, 346, 365`. However, the intermediate `.wav` audio file extracted during transcription is cleaned up inside `transcribe_clip()` in its `finally` block at `captioner.py:73-74`. This is actually fine — the WAV is cleaned up internally. But if `transcribe_clip` crashes hard (e.g., OOM kill), the WAV file could leak.
- **Recommendation:** Low risk since the `finally` block handles it. The `clean_stale_tmp` fix (SPEC-M3) would catch leaked files as a safety net.

### SPEC-M6 — Stale: `load_config` doesn't pop `captions` from streamer dict
- **Category:** Missing
- **Severity:** Medium
- **Spec Reference:** `tasks/auto-captions-plan.md` line 112: "Pop `captions` from streamer dict before constructing `StreamerConfig`"
- **Implementation Reference:** `C:\Users\andre\Code\twitch-to-shorts\main.py:69-74`
- **Issue:** Since `StreamerConfig` lacks a `captions` field (SPEC-H5), `load_config` doesn't pop it either. If a user adds `captions: true` to a streamer config, it would cause a `TypeError: __init__() got an unexpected keyword argument 'captions'`. The spec anticipated this integration.
- **Recommendation:** Fix SPEC-H5 first (add `captions` field to `StreamerConfig`), then `load_config` will accept it naturally, or pop it before construction.

### SPEC-M7 — Incorrect: `CaptionWord` replaces `TranscriptWord` from spec
- **Category:** Incorrect
- **Severity:** Medium
- **Spec Reference:** `tasks/auto-captions-plan.md` lines 73-79
- **Implementation Reference:** `C:\Users\andre\Code\twitch-to-shorts\src\models.py:21-25`
- **Issue:** Spec names the dataclass `TranscriptWord`. Implementation names it `CaptionWord`. The fields are identical. This is a naming deviation, not a functional one. However, it means any external references to the spec's `TranscriptWord` won't find it in the codebase.
- **Recommendation:** Minor naming deviation. Accept as-is or rename to match spec.

### SPEC-M8 — Incorrect: ASS subtitle filter uses `subtitles=` instead of `ass=`
- **Category:** Incorrect
- **Severity:** Medium
- **Spec Reference:** `tasks/auto-captions-plan.md` lines 95-96: "ass='escaped_path'"
- **Implementation Reference:** `C:\Users\andre\Code\twitch-to-shorts\src\video_processor.py:402, 406`
- **Issue:** Spec uses `ass=` filter syntax. Implementation uses `subtitles=` filter syntax. Both are valid ffmpeg filters, but they differ:
  - `ass=` is ASS-specific and faster (direct ASS rendering)
  - `subtitles=` is a generic filter that auto-detects format (adds overhead, supports more formats)

  For ASS files specifically, `ass=` is the better choice.
- **Recommendation:** Change `subtitles=` to `ass=` for better performance and spec compliance.

---

## Low/Stale Findings

### SPEC-L1 — Stale: README module table does not include `captioner.py`
- **Category:** Stale
- **Severity:** Low
- **Spec Reference:** `README.md` lines 1-20
- **Implementation Reference:** `C:\Users\andre\Code\twitch-to-shorts\README.md:8-19`
- **Issue:** README module table lists 10 files but does not include `src/captioner.py`. Also lists `src/media_utils.py` as 22 lines but it is now 34 lines (after `extract_audio` was added). Line counts for other modules are slightly off from current state.
- **Recommendation:** Add `captioner.py` to the module table. Update line counts.

### SPEC-L2 — Stale: README does not mention captions in pipeline data flow
- **Category:** Stale
- **Severity:** Low
- **Spec Reference:** `README.md` lines 22-38
- **Implementation Reference:** `C:\Users\andre\Code\twitch-to-shorts\README.md:22-38`
- **Issue:** The pipeline data flow section doesn't mention the caption generation step between download and process.
- **Recommendation:** Add step 5.5 or update step 6 to mention optional caption generation.

### SPEC-L3 — Stale: README config reference doesn't mention captions config
- **Category:** Stale
- **Severity:** Low
- **Spec Reference:** `README.md` lines 69-74
- **Implementation Reference:** `C:\Users\andre\Code\twitch-to-shorts\README.md:69-74`
- **Issue:** Config reference section doesn't document the `captions.enabled` config key.
- **Recommendation:** Add `captions` section to config reference.

### SPEC-L4 — Stale: README test count says 183 but captioner tests were added
- **Category:** Stale
- **Severity:** Low
- **Implementation Reference:** `C:\Users\andre\Code\twitch-to-shorts\README.md:112`
- **Issue:** README says "183 tests across 8 test files". With `test_captioner.py` added (14 test methods), the count should be updated.
- **Recommendation:** Update test count and file count.

### SPEC-L5 — Stale: README dependencies don't mention deepgram-sdk
- **Category:** Stale
- **Severity:** Low
- **Implementation Reference:** `C:\Users\andre\Code\twitch-to-shorts\README.md:126-129`
- **Issue:** Dependencies section lists runtime packages but does not mention `deepgram-sdk`, which was added to `requirements.txt`.
- **Recommendation:** Add `deepgram-sdk` to the runtime dependencies list.

---

## Previous Audit Fix Verification

I spot-checked the 9 Critical findings from the Feb 5 audit (`docs/audits/audit-summary.md`):

| # | Fix | Status | Evidence |
|---|-----|--------|----------|
| 1 | Twitch secret in POST body | **HELD** | `twitch_client.py:33` uses `data=` not `params=` |
| 2 | Config.yaml secret fallback removed | **HELD** | `main.py:99-102` only reads from `os.environ`, no config fallback |
| 3 | COALESCE in `record_known_clip` | **HELD** | `db.py:141` uses `COALESCE(clips.youtube_id, excluded.youtube_id)` |
| 4 | YouTube API timeout | **MODIFIED** | `youtube_uploader.py:117` uses `build("youtube", "v3", credentials=creds)` — the timeout was originally via `AuthorizedHttp(httplib2.Http(timeout=30))` but was removed due to the resumable upload 308 bug (see MEMORY.md). Currently NO explicit timeout on non-upload API calls. The fix was correct to remove `AuthorizedHttp`, but the timeout was collateral damage. |
| 5 | Analytics fallback wrapped | **HELD** | `youtube_analytics.py:47-52` has try/except on both primary and fallback |
| 6 | Channel dedup fatal vs transient | **HELD** | `youtube_uploader.py:390-394` raises on 401/403 (non-quota) |
| 7 | Atomic lockfile | **HELD** | `main.py:229-239` uses `os.replace()` for atomic PID write |
| 8 | `verify=True` on Twitch requests | **HELD** | `twitch_client.py:60` passes `verify=True` |
| 9 | COALESCE youtube_id | **HELD** | Same as #3 |

**Summary: 8 of 9 critical fixes are intact.** Fix #4 (YouTube API timeout) regressed — the timeout was removed along with the `AuthorizedHttp` wrapper when fixing the 308 redirect bug. Non-upload YouTube API calls currently have no explicit timeout.

---

## Roadmap Compliance

| Milestone | Claimed Status | Verified | Notes |
|-----------|---------------|----------|-------|
| 1.1 Channel restriction lifts | DONE | N/A | Operational, not verifiable from code |
| 1.2 First production upload | DONE | N/A | Operational |
| 1.3 Ramp to 5 uploads/day | IN PROGRESS | **Consistent with code** | `max_uploads_per_window: 1` + 4hr cron = 5-6/day |
| 1.4 2-week data checkpoint | PENDING | N/A | Target ~2026-02-16 |
| 2.1 Thumbnail generation | DONE | **Verified** | `video_processor.py:126-173` implements YDIF-based thumbnail extraction |
| 2.2 Title optimization | DONE | **Verified** | `youtube_uploader.py:175-180` implements md5-based A/B template selection |
| 2.3 Fail-count guard | DONE | **Verified** | `dedup.py:34` checks `fail_count >= 3` |
| 2.4 Basic YouTube data pull | DONE (analytics off) | **Verified** | `youtube_analytics.py` exists, `analytics_enabled: false` in config |
| 2.5 Scoring tuning | PENDING | **Correct** | Needs analytics data first |
| 2.6 Burned-in captions | DONE (default off) | **Partially verified** | Captioner exists and integrates into pipeline, but has significant spec deviations (see findings above) |

---

## Integration Flow Trace

Tracing the captioner integration from entry to output:

1. **`main.py:551`** — reads `captions_enabled` from `raw_config.get("captions", {}).get("enabled", False)`
2. **`main.py:573`** — passes `captions_enabled` to `_process_streamer()`
3. **`main.py:463`** — passes `captions_enabled` to `_process_single_clip()`
4. **`main.py:298-300`** — if `captions_enabled`, imports and calls `generate_captions(video_path, cfg.tmp_dir)`
5. **`captioner.py:171-189`** — `generate_captions()` orchestrates: transcribe -> generate ASS
6. **`captioner.py:9-74`** — `transcribe_clip()` extracts audio via `media_utils.extract_audio()`, calls Deepgram, returns `CaptionWord` list
7. **`captioner.py:123-168`** — `generate_ass_subtitles()` groups words, writes ASS file
8. **`main.py:304-308`** — passes `subtitle_path` to `crop_to_vertical()`
9. **`video_processor.py:198-265`** — `crop_to_vertical()` accepts `subtitle_path`, passes to `_run_ffmpeg()`
10. **`video_processor.py:397-406`** — `_run_ffmpeg()` injects subtitle filter into ffmpeg command

The integration flow works end-to-end. The subtitle file IS burned into the video output. The critical gap is the silence offset desync (SPEC-C1): the video gets trimmed but the captions don't know about it.

---

## Recommendations (Priority Order)

1. **Fix silence offset desync (SPEC-C1, C2, C3)** — This is the only functional bug. Captions will be out of sync on any clip with leading silence. Make `detect_leading_silence` public, call it in `main.py`, pass offset to captioner.

2. **Fix word grouping rules (SPEC-H1)** — The current grouping produces visually different captions than specified. Change max words to 3, gap threshold to 0.3s, add punctuation breaking.

3. **Uppercase caption text (SPEC-H2)** — One-line fix, high visual impact.

4. **Fix MarginV and Outline (SPEC-H3)** — MarginV=120 positions captions too low on the frame. Change to 400 per spec.

5. **Add per-streamer captions config (SPEC-H5, H6)** — Required for multi-streamer Phase 3.

6. **Add `.ass` and `.wav` to cleanup (SPEC-M3)** — Prevents file accumulation.

7. **Update README (SPEC-L1 through L5)** — Five stale sections need updating.

8. **Re-add YouTube API timeout (audit fix #4 regression)** — Non-upload API calls have no timeout since the `AuthorizedHttp` removal.

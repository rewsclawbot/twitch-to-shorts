# Performance Audit — 2026-02-09

Auditor: performance
Scope: All 11 modules + config.yaml + captioner.py (new)

**Previous audit reference:** `docs/audits/audit-summary.md` (2026-02-05)

---

## Summary

| Severity | Count |
|----------|-------|
| Critical | 2     |
| High     | 5     |
| Medium   | 7     |
| Low      | 4     |
| **Total** | **18** |

---

## Critical Findings

### P-C1: Captioner reads entire WAV file into memory before Deepgram upload
- **Severity:** Critical
- **Effort:** S
- **Location:** `src/captioner.py:37-40`
- **Issue:** `transcribe_clip()` extracts audio to an uncompressed 16kHz mono WAV file, then reads the entire file into memory with `f.read()` before sending to Deepgram. A 60-second clip at 16kHz/16-bit mono = ~1.9MB WAV. This is manageable for a single clip, but the real cost is the intermediate WAV file itself: extracting to uncompressed PCM when Deepgram accepts compressed formats (MP3, OGG, FLAC) wastes disk I/O and extraction time. A 60s WAV at 16kHz is ~3x larger than equivalent FLAC.
- **Fix:** Use FLAC or OGG output format in `extract_audio()` (change `-acodec pcm_s16le` to `-acodec flac` or pipe audio directly). Deepgram Nova-2 accepts FLAC natively. This cuts audio extraction time and file size by ~60-70%. Alternatively, stream the file to Deepgram using chunked upload instead of buffering the entire file.

### P-C2: `clip_overlaps()` calls `julianday()` on every row in the filtered set — no index usable
- **Severity:** Critical
- **Effort:** M
- **Location:** `src/db.py:72-91`
- **Issue:** The overlap query uses `julianday(created_at)` which prevents SQLite from using the index on `created_at` for the precise comparison. The coarse pre-filter (ISO string range) helps, but for every candidate row, SQLite must parse the ISO string through `julianday()` twice. This is called per-clip during dedup, so with 500 clips fetched x N existing clips in the window, this becomes O(500 * K) julianday evaluations. As the DB grows (hundreds of clips), this compounds.
- **Fix:** Add an integer column `created_at_epoch` (Unix timestamp) and index it. Replace `julianday()` arithmetic with simple integer subtraction: `ABS(created_at_epoch - ?) < ?`. This is the same recommendation from the previous audit (P-C2 in audit-summary.md) — **still unaddressed**.
- **ALREADY FLAGGED:** Previous audit item #24.

---

## High Findings

### P-H1: Captioner creates a new `DeepgramClient` instance per clip
- **Severity:** High
- **Effort:** S
- **Location:** `src/captioner.py:31-35`
- **Issue:** `transcribe_clip()` imports and instantiates `DeepgramClient(api_key)` inside the function body on every call. Client instantiation includes HTTP session setup, SDK initialization, and potentially DNS resolution. When processing multiple clips in a single pipeline run (up to 6 per streamer), this is 6 redundant client instantiations.
- **Fix:** Accept an optional `client` parameter or use a module-level singleton pattern. Initialize the client once in the pipeline loop and pass it through.

### P-H2: Audio extraction + ffmpeg encode are completely serial — no overlap with Deepgram API call
- **Severity:** High
- **Effort:** M
- **Location:** `main.py:298-301`, `src/captioner.py:22-49`
- **Issue:** The caption pipeline is: (1) extract audio to WAV, (2) read WAV, (3) send to Deepgram API (network I/O), (4) wait for response, (5) generate ASS file. Steps 1-4 are all synchronous and blocking. The Deepgram API call is network-bound (typically 2-5s for a 60s clip) while ffmpeg encoding is CPU-bound. These could overlap with the video processing step.
- **Fix:** Extract audio and send to Deepgram *before* `crop_to_vertical()`, then use the transcription result when building the subtitle filter. The video crop takes 10-30s — more than enough time for the Deepgram round-trip to complete. This would require restructuring `_process_single_clip()` to start caption work early.

### P-H3: `filter_new_clips()` calls `clip_overlaps()` per-clip sequentially — N+1 query pattern
- **Severity:** High
- **Effort:** M
- **Location:** `src/dedup.py:39-44`
- **Issue:** For each clip in the candidate list, `clip_overlaps()` runs a separate SQL query against the DB. With 500 fetched clips (after initial ID-based dedup), this could mean up to ~500 individual queries per streamer per run. Each query invokes `julianday()` as noted in P-C2, compounding the cost.
- **Fix:** Batch the overlap check: fetch all existing clips for the streamer in the lookback window in a single query, then do the overlap comparison in Python. This reduces ~500 queries to 1 query + in-memory comparison.

### P-H4: `_detect_leading_silence()` reads first 6 seconds of video even when there's no silence
- **Severity:** High
- **Effort:** S
- **Location:** `src/video_processor.py:176-195`
- **Issue:** This ffmpeg invocation decodes the first 6 seconds of video+audio for every clip regardless of whether the clip has leading silence. Most Twitch clips do not have leading silence — the clip starts mid-action. This adds ~1-2s of subprocess overhead per clip unnecessarily.
- **Fix:** Only decode audio (`-vn` is already present, good), but check the return early: if `silence_start` is not found at position 0, skip. The bigger optimization: make silence detection optional via config flag (default off) since most Twitch clips don't need it. Or combine with the loudnorm first pass — both read the audio stream.

### P-H5: Two full audio decoding passes (silence detection + loudnorm measurement) before encode
- **Severity:** High
- **Effort:** M
- **Location:** `src/video_processor.py:176-195` (silence), `src/video_processor.py:322-342` (loudnorm)
- **Issue:** The pipeline runs two separate ffmpeg audio-only passes before the final encode:
  1. `_detect_leading_silence()` — decodes first 6s of audio
  2. `_measure_loudness()` — decodes the entire audio track

  Both are audio-only analysis passes. The loudnorm pass already reads the full audio, which includes the first 6s that silence detection reads separately. This means the first 6s of audio are decoded twice.
- **Fix:** Combine silence detection and loudnorm measurement into a single ffmpeg invocation using a complex filter: `-af "silencedetect=noise=-30dB:d=0.5,loudnorm=I=-14:LRA=11:TP=-1.5:print_format=json"`. Parse both outputs from stderr. Saves one full subprocess spawn + 6s of redundant audio decoding.

---

## Medium Findings

### P-M1: `_batch_sample_ydif()` opens the same video file N times via multiple `-ss -i` inputs
- **Severity:** Medium
- **Effort:** M
- **Location:** `src/video_processor.py:84-123`
- **Issue:** For thumbnail extraction, `_batch_sample_ydif()` opens the same video file up to 8 times (one `-ss -i` per timestamp). Each input open involves file handle allocation, container parsing, and seek. For the thumbnail case (8 samples), this means 8 file opens.
- **Fix:** Use a single input with `select` filter to pick specific frames at timestamps, or use `trim` filters from a single input. Example: `-i video.mp4 -filter_complex "[0:v]split=8[s0]...[s7];[s0]trim=start=T0:end=T0+0.04,setpts=PTS-STARTPTS,signalstats..."`. The single-input approach avoids repeated container parsing.

### P-M2: `get_game_names()` makes API calls even when all game_ids are empty strings
- **Severity:** Medium
- **Effort:** S
- **Location:** `src/twitch_client.py:81-94`
- **Issue:** The function filters empty strings with `[gid for gid in set(game_ids) if gid]`, which is correct. However, in `_process_streamer()` (main.py:432-439), `game_ids` is extracted for ALL `new_clips` even though many Twitch clips share the same game_id. The dedup via `set()` inside `get_game_names()` handles this, but the list construction is wasteful for large clip sets.
- **Fix:** Minor — the current code is functionally correct and the `set()` dedup handles it. Low priority.

### P-M3: `verify_upload()` makes a separate API call after upload — costs 1 quota unit each time
- **Severity:** Medium
- **Effort:** S
- **Location:** `src/youtube_uploader.py:402-427`
- **Issue:** After a successful upload, `verify_upload()` calls `videos().list(part="status", id=video_id)` which costs 1 quota unit. The upload response itself already contains the video status in the response body. For the current 1-streamer/1-upload-per-window setup this is negligible, but at scale (5+ uploads/day), this wastes 5+ quota units daily.
- **Fix:** Check the upload response for status data before making a separate verify call. The `videos().insert()` response includes `status.uploadStatus` when `status` is in the `part` parameter (which it already is). Return the status from the upload response.

### P-M4: `check_channel_for_duplicate()` verifies each title match with a separate `videos().list()` call
- **Severity:** Medium
- **Effort:** S
- **Location:** `src/youtube_uploader.py:370-382`
- **Issue:** When a title match is found during channel dedup, the code makes an additional `videos().list(part="status", id=video_id)` call (1 quota unit) to verify the video isn't a ghost/deleted upload. In the common case (no duplicates), this doesn't fire. But when duplicates exist, it adds 1 quota unit per match. With the `max_results=50` scan, worst case is 50 extra quota units if every title matched (extremely unlikely but wasteful per-match).
- **Fix:** The `playlistItems().list()` response already includes `status` if you add it to the `part` parameter. Change `part="snippet"` to `part="snippet,status"` to get upload status inline, eliminating the separate verify call. This costs 0 extra quota units.

### P-M5: ffmpeg CPU preset is "fast" — suboptimal quality/size tradeoff for Shorts
- **Severity:** Medium
- **Effort:** S
- **Location:** `src/video_processor.py:412`
- **Issue:** The CPU encoder uses `-preset fast` with `-crf 20`. YouTube re-encodes all uploads, so spending more time encoding locally for better quality is wasted — YouTube will degrade it anyway. However, "fast" still produces larger files than "ultrafast"/"superfast", which means longer upload times. For CI (GitHub Actions), upload bandwidth is the bottleneck, not encoding speed.
- **Fix:** For CI, "ultrafast" or "superfast" produces adequate quality (YouTube re-encodes anyway) with smaller files and faster encoding. The current "fast" preset was already changed from the default — consider going even faster for CI specifically. Could be toggled via `FFMPEG_PRESET` env var.

### P-M6: `extract_audio()` has no timeout and no `-t` duration limit
- **Severity:** Medium
- **Effort:** S
- **Location:** `src/media_utils.py:25-33`
- **Issue:** `extract_audio()` has a 60s timeout on subprocess.run, which is fine. But it processes the entire video duration even though clips are max 60s. The bigger issue: if the video file is corrupt or has a very long reported duration (metadata error), ffmpeg will attempt to extract audio for the full reported duration before timing out.
- **Fix:** Add `-t 65` (65s to be safe) to limit extraction to clip duration. This ensures even corrupt files with wrong duration metadata finish quickly.

### P-M7: `update_streamer_stats()` issues a separate `conn.commit()` for each streamer
- **Severity:** Medium
- **Effort:** S
- **Location:** `src/db.py:155-173`, `main.py:509`
- **Issue:** Called once per streamer at the end of `_process_streamer()`. Each call does `SELECT` + `INSERT/UPDATE` + `commit()`. With N streamers, that's N separate transactions. Current state (1 streamer) is fine, but at 10 streamers this becomes 10 separate commits.
- **Fix:** Accept a batch of streamers and commit once. Low priority at current scale.

---

## Low Findings

### P-L1: `_format_ass_time()` called per caption group — micro-optimization opportunity
- **Severity:** Low
- **Effort:** S
- **Location:** `src/captioner.py:77-87`
- **Issue:** The time formatting function does division/modulo per call. For a typical 60s clip with ~30-50 caption groups, this is 60-100 calls. The cost is negligible (microseconds total), but the function could cache or use a more efficient strftime-based approach.
- **Fix:** No action needed. Listed for completeness.

### P-L2: `_dedupe_tags()` and `_limit_tag_length()` iterate tags list twice
- **Severity:** Low
- **Effort:** S
- **Location:** `src/youtube_uploader.py:183-209`
- **Issue:** Tags are first deduped then length-limited in two passes. Could be combined into a single pass. But with ~8-10 tags per upload, the cost is negligible.
- **Fix:** No action needed at current scale.

### P-L3: `load_blocklist()` reads file from disk on every `filter_new_clips()` call
- **Severity:** Low
- **Effort:** S
- **Location:** `src/dedup.py:13-18`
- **Issue:** The blocklist file is read from disk on every pipeline run. The file is typically small (a few lines) and only read once per run per streamer, so file I/O cost is negligible. However, if blocklist grows large or multiple streamers are processed, it's read N times.
- **Fix:** Cache the blocklist at the pipeline level and pass it to `filter_new_clips()`. Minor optimization.

### P-L4: `_probe_video_info()` returns both duration and dimensions but callers often only need one
- **Severity:** Low
- **Effort:** S
- **Location:** `src/video_processor.py:20-53`
- **Issue:** `_get_duration()` calls `_probe_video_info()` which probes both duration and dimensions, discarding the dimensions. This is already an improvement from the previous audit (consolidated from separate probes), and the overhead of parsing one extra JSON field is negligible.
- **Fix:** No action needed. The consolidation was the right call. Listed for completeness since this was the P-C1 fix from the previous audit — **ALREADY ADDRESSED**.

---

## Findings Addressed Since Previous Audit

The following findings from the 2026-02-05 audit have been resolved:

| Previous ID | Description | Status |
|-------------|-------------|--------|
| P-C1 | Consolidate ffmpeg probes (7-9 -> fewer per clip) | **Fixed** — `_probe_video_info()` consolidated, `_has_facecam()` uses single invocation with 3 seeks |
| P-H1 | Cache uploads_playlist_id in channel dedup | **Fixed** — `_uploads_playlist_cache` dict at module level |
| P-M5 | 5MB chunk size for YouTube uploads | **Fixed** — `chunksize=5 * 1024 * 1024` |

Still unaddressed from previous audit:
- P-C2: `created_at_epoch` column for faster overlap queries (now P-C2 in this audit)

---

## Captioner-Specific Performance Analysis

The new `src/captioner.py` module introduces a Deepgram API dependency. Performance characteristics:

1. **Deepgram API latency:** Nova-2 transcription for a 60s clip typically takes 2-5s. This is network-bound and cannot be optimized locally, but can be overlapped with other work (see P-H2).

2. **Audio extraction cost:** `extract_audio()` decodes the entire video to produce a WAV file. For a 60s clip at 16kHz mono, this takes ~1-2s of CPU time. Using a compressed format (P-C1) would reduce this.

3. **ASS generation:** `generate_ass_subtitles()` is pure Python string manipulation — negligible cost. `_group_words()` is O(n) where n is word count (~100-200 words for a 60s clip).

4. **Total captioner overhead per clip:** ~5-10s (1-2s audio extraction + 2-5s API + 1s overhead). This adds ~15-30% to the per-clip processing time (currently ~30-60s for download+process+upload).

---

## Scaling Analysis Update

### Current state (1 streamer, 1 upload per 2h window)
- Pipeline runtime: ~45-90s per run (fetch + filter + download + process + upload)
- ffmpeg processing: ~15-30s per clip (with silence + loudnorm + encode)
- API costs: ~1,600 quota units/day (5 uploads x ~320 units each)
- With captions: add ~5-10s per clip, plus Deepgram API cost ($0.0043/min for Nova-2 = ~$0.004 per 60s clip)

### At 5 uploads/day (current target)
- **Bottleneck:** YouTube API quota (10,000 units/day). 5 uploads = ~1,600-2,000 units. Comfortable.
- **CI runtime:** ~5-10 min per run. Well within GitHub Actions 6h limit.
- **Deepgram cost:** ~$0.02/day (5 clips x $0.004). Negligible.

### At 20 uploads/day (3-4 streamers)
- **Bottleneck:** YouTube API quota ~6,400 units. Still under 10,000 limit.
- **CI runtime:** ~20-30 min per run. Close to GitHub Actions free tier time limits.
- **Key optimization:** P-H2 (overlap captioner with video processing) and P-H5 (combine audio passes) save ~5-10s per clip = 100-200s total per run.

### At 50+ uploads/day
- **Impossible** without YouTube API quota increase (50 uploads = ~16,000 units/day).
- CI runtime: >1h. Would need self-hosted runner or parallel jobs.

---

## Priority Recommendations

### Immediate (before enabling captions)
1. **P-C1** — Switch audio extraction to FLAC format (effort: S, saves ~60% extraction time)
2. **P-H1** — Singleton Deepgram client (effort: S, avoids per-clip instantiation)
3. **P-M6** — Add `-t 65` to audio extraction (effort: S, safety guard)

### Next sprint
4. **P-H5** — Combine silence detection + loudnorm into single ffmpeg pass (effort: M, saves 1 subprocess + 6s audio decoding per clip)
5. **P-H4** — Make silence detection configurable/skippable (effort: S, saves 1-2s per clip when disabled)
6. **P-C2** — Add `created_at_epoch` column (effort: M, from previous audit — unblocks batch overlap optimization)
7. **P-H3** — Batch overlap queries (effort: M, depends on P-C2)

### If scaling beyond 5 uploads/day
8. **P-H2** — Overlap captioner with video processing (effort: M, saves 5-10s per clip)
9. **P-M4** — Include status in playlistItems part param (effort: S, saves 1 quota unit per dedup match)
10. **P-M3** — Use upload response for verification (effort: S, saves 1 quota unit per upload)

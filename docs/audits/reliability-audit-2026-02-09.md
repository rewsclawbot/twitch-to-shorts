# Reliability Audit — 2026-02-09

**Auditor:** reliability
**Scope:** All 11 source modules + captioner
**Previous audit:** `docs/audits/audit-summary.md` (2026-02-05, 60 findings total across 4 auditors)

---

## Summary

**Total findings: 22** (3 Critical, 5 High, 9 Medium, 5 Low)

Many of the original 2026-02-05 reliability findings have been fixed (atomic lockfile, COALESCE youtube_id, timeout on YouTube API builder, analytics fallback try/except, token backoff, PipelineConfig validation, consolidated ffprobe calls). This audit focuses on **new code** (captioner module, refactored pipeline functions) and **residual/newly-discovered** issues.

---

## Critical Findings

### R-C1: Deepgram API call has no timeout — captioner can hang indefinitely
- **Severity:** Critical | **Effort:** S
- **Location:** `src/captioner.py:49`
- **Issue:** `client.listen.rest.v("1").transcribe_file(payload, options)` has no timeout parameter. If the Deepgram API is slow or unresponsive, this call blocks the entire pipeline indefinitely. The clip's audio buffer is read entirely into memory first (line 38), so for a 60s clip at 16kHz mono WAV, that's ~1.9MB — manageable, but the HTTP call itself has zero timeout protection.
- **Fix:** Pass a `timeout` option to the Deepgram client constructor or the `transcribe_file` call. Deepgram's Python SDK supports `options={"timeout": 30}` on the client. Alternatively, wrap the call in a `threading.Timer` or `signal.alarm` as a safety net.

### R-C2: Entire audio file read into memory before Deepgram upload
- **Severity:** Critical | **Effort:** S
- **Location:** `src/captioner.py:37-40`
- **Issue:** `buffer_data = f.read()` reads the entire WAV file into memory. While ~2MB for a 60s clip is fine, the audio extraction at `media_utils.py:27` uses pcm_s16le at 16kHz mono — but there's no duration cap on the ffmpeg extraction. If a malformed duration probe returns a very large value or the clip is unexpectedly long (e.g., max_duration check bypassed), the WAV could be huge. More critically, a failed/corrupt extraction could produce an enormous file.
- **Fix:** Add `-t 65` (65 seconds) to the ffmpeg `extract_audio` command to cap audio length. This provides defense-in-depth since `crop_to_vertical` already checks duration.

### R-C3: `_uploads_playlist_cache` is a module-level global dict keyed by `id(service)` — stale after GC
- **Severity:** Critical | **Effort:** S
- **Location:** `src/youtube_uploader.py:333-353`
- **Issue:** The cache uses `id(service)` as key. In CPython, `id()` returns the memory address of the object. If a service object is garbage collected and a new one is allocated at the same address, the cache returns the old playlist ID — which could belong to a **different YouTube channel**. In this pipeline, each streamer gets a fresh `get_authenticated_service()` call. If a previous service is GC'd before the next one is created, the cache could serve a wrong channel's uploads playlist, causing dedup to check the wrong channel and miss real duplicates.
- **Fix:** Key the cache by `credentials_file` (the YouTube token path) instead of `id(service)`. This is stable and uniquely identifies the channel. Alternatively, clear the cache between streamers.

---

## High Findings

### R-H1: `extract_audio` in `media_utils.py` has no error message — bare `check=True` raises opaque CalledProcessError
- **Severity:** High | **Effort:** S
- **Location:** `src/media_utils.py:27-32`
- **Issue:** If ffmpeg fails to extract audio (corrupt video, missing audio track, disk full), `check=True` raises `CalledProcessError` with binary stderr. The caller in `captioner.py:24` catches this generically, but the error message will be unhelpful (just the return code). No cleanup of partial `output_path` happens inside `extract_audio` itself.
- **Fix:** Catch `CalledProcessError` inside `extract_audio`, log stderr, clean up partial output, and raise a more descriptive error or return None.

### R-H2: No retry on Deepgram transient failures (5xx, network timeout)
- **Severity:** High | **Effort:** M
- **Location:** `src/captioner.py:30-72`
- **Issue:** The Deepgram API call has zero retry logic. A transient 503 or network blip causes the entire captioning to fail for that clip. Every other external API in the pipeline (Twitch: 3 retries with backoff, YouTube upload: 4 retries with exponential backoff) has retry logic — captioner is the odd one out.
- **Fix:** Add a simple retry loop (2-3 attempts with exponential backoff) around the `transcribe_file` call, catching transient HTTP errors.

### R-H3: `crop_to_vertical` returns stale cached output without validating it
- **Severity:** High | **Effort:** S
- **Location:** `src/video_processor.py:210-212`
- **Issue:** If `output_path` exists and has size > 0, the function returns it immediately. But a previous run might have produced a **corrupt** file (partial write, ffmpeg crash after creating headers). The file would have size > 0 but be unplayable. This would then be uploaded to YouTube as a broken video.
- **Fix:** Run `is_valid_video(output_path)` on the cached file before returning it. This adds one ffprobe call but prevents uploading corrupt videos.

### R-H4: Subtitle path injection via clip title in ffmpeg filter
- **Severity:** High | **Effort:** S
- **Location:** `src/video_processor.py:397-406`
- **Issue:** The `_escape_subtitle_path` function (line 365-367) escapes `\` to `/` and `:` to `\:`, but ffmpeg's subtitles filter also interprets `'` (single quotes), `;` (filter separator), and `[` `]` (link labels) in the filename. While the subtitle path is constructed from `clip_id` + `_captions.ass` (reasonably safe), if the tmp_dir contains special characters, the filter could break or be exploited. More concerning: if a future change allows user-supplied subtitle paths, this becomes a command injection vector.
- **Fix:** Also escape `'`, `[`, `]`, and `;` in `_escape_subtitle_path`. Or better: use ffmpeg's `-i` input for the subtitle file and reference it by stream index in the filter, avoiding path escaping entirely.

### R-H5: DB connection not closed on `get_connection` failure path
- **Severity:** High | **Effort:** S
- **Location:** `src/db.py:8-14`
- **Issue:** If `init_schema()` raises (e.g., disk full, corrupt DB, migration failure), the `conn` object is left open and unreturned. The caller in `main.py:251` calls `conn.close()` in `finally`, but if `get_connection()` itself throws, `conn` is never assigned to the caller's variable and the SQLite connection leaks.
- **Fix:** Wrap `init_schema` in try/except inside `get_connection` and close `conn` on failure before re-raising.

---

## Medium Findings

### R-M1: `_process_single_clip` doesn't count download/process steps on "quota_exhausted" path
- **Severity:** Medium | **Effort:** S
- **Location:** `main.py:479-483`
- **Issue:** When `result == "quota_exhausted"`, the counters increment `downloaded += 1` and `processed += 1`, but the clip may not have actually been downloaded/processed — the quota error occurs during upload. Looking at `_process_single_clip`, the QuotaExhaustedError is raised during `upload_short()` (line 331), which happens AFTER download and crop succeed. So the counters are actually correct. However, `thumbnail_path` in the cleanup on line 333 is always None at that point (it's only set on line 356 after upload succeeds), so including it in cleanup is a no-op dead parameter. Not a bug, but misleading code.
- **Status:** Cosmetic — no operational impact.

### R-M2: `_sync_streamer_metrics` doesn't wrap individual `fetch_video_metrics` calls in try/except
- **Severity:** Medium | **Effort:** S
- **Location:** `main.py:157-168`
- **Issue:** If `fetch_video_metrics` raises an unexpected exception for one video (not caught by the try/except inside `fetch_video_metrics` itself — e.g., a `KeyError` in response parsing), the entire metrics sync loop aborts. Remaining videos for that streamer are skipped. The outer `try/except` in `_process_streamer` (line 524) catches this, but metrics for subsequent videos are lost.
- **Fix:** Wrap the per-video `fetch_video_metrics` + `update_youtube_metrics` block in its own try/except within the loop. **PREVIOUSLY FLAGGED** in 2026-02-05 audit as R-C1/A-M6 — the fallback query was fixed, but the per-video exception guard was not added.

### R-M3: WAL checkpoint runs even if pipeline raised an exception
- **Severity:** Medium | **Effort:** S
- **Location:** `main.py:252-258`
- **Issue:** The `finally` block in `run_pipeline` always attempts `PRAGMA wal_checkpoint(TRUNCATE)`. If the pipeline failed due to a DB corruption issue, forcing a WAL checkpoint could compound the problem. The checkpoint warning is caught, but on Windows, if another process holds the WAL, this could cause issues.
- **Fix:** Only run checkpoint on clean exit (move to the `try` block after `_run_pipeline_inner` returns, or gate on a success flag).

### R-M4: `clean_stale_tmp` runs on every pipeline execution regardless of outcome
- **Severity:** Medium | **Effort:** S
- **Location:** `main.py:259`
- **Issue:** If the pipeline crashes early (e.g., Twitch API down), `clean_stale_tmp` still runs in the finally block. With `max_age_hours=1`, this aggressively cleans tmp files. If a user is running a second manual pipeline instance (despite the lock), or debugging, their tmp files could be deleted. Minor risk since the lockfile prevents concurrent runs, but the 1-hour window is aggressive.
- **Fix:** Consider keeping the default 24-hour window or only cleaning on success.

### R-M5: No max retry limit on Twitch pagination — infinite loop possible
- **Severity:** Medium | **Effort:** S
- **Location:** `src/twitch_client.py:104-135`
- **Issue:** The `while True` loop in `fetch_clips` relies on the cursor becoming empty or `max_clips` being reached. If the Twitch API has a bug and returns the same cursor infinitely, this loop never terminates. The 500-clip cap mitigates this (since each page returns 100 clips, max 5 pages), but there's no iteration limit as defense-in-depth.
- **Fix:** Add a `max_pages` counter (e.g., 10) to break the loop after a reasonable number of iterations.

### R-M6: `_filter_batch_overlaps` appends clips with invalid `created_at` without overlap checking
- **Severity:** Medium | **Effort:** S
- **Location:** `src/dedup.py:53-57`
- **Issue:** If `datetime.fromisoformat(c.created_at)` fails, the clip is appended to results without overlap checking (line 57). This means a malformed timestamp bypasses batch dedup entirely. The clip could overlap with another clip and create a near-duplicate upload.
- **Fix:** Skip (continue) clips with invalid timestamps instead of appending them. Or log a warning and exclude them from the batch.

### R-M7: `verify_upload` is never called in the current pipeline
- **Severity:** Medium | **Effort:** S
- **Location:** `src/youtube_uploader.py:402-427`
- **Issue:** The `verify_upload` function exists and is imported in `main.py`, but it is never called in the `_process_single_clip` flow. The "record before verify" pattern was implemented (insert_clip before verify), but the verify step itself was apparently removed or never wired in. This means uploaded videos are never verified — if YouTube silently rejects a video, the pipeline doesn't know.
- **Fix:** Call `verify_upload(yt_service, youtube_id)` after `insert_clip` in `_process_single_clip`. Log the result but don't fail the pipeline on verify failure.

### R-M8: `generate_ass_subtitles` doesn't validate word timing data
- **Severity:** Medium | **Effort:** S
- **Location:** `src/captioner.py:123-168`
- **Issue:** The function trusts that `words[].start` and `words[].end` are valid floats. If Deepgram returns NaN, negative values, or end < start, the ASS file will have invalid timecodes. ffmpeg's subtitle filter may silently drop these or display them incorrectly.
- **Fix:** Validate each word's timing: `start >= 0`, `end > start`, both are finite. Skip or clamp invalid entries.

### R-M9: `_build_composite_filter` trusts facecam config values without bounds checking
- **Severity:** Medium | **Effort:** S
- **Location:** `src/video_processor.py:345-362`
- **Issue:** If `facecam.x`, `facecam.y`, `facecam.w`, or `facecam.h` are outside [0, 1] or their sum exceeds 1.0 (e.g., `x=0.9, w=0.5` = crop outside frame), ffmpeg will error out. The values come from `config.yaml` which has `__post_init__` validation on `PipelineConfig` but **not** on `FacecamConfig`.
- **Fix:** Add `__post_init__` validation to `FacecamConfig` ensuring `0 <= x, y, w, h <= 1` and `x + w <= 1`, `y + h <= 1`.

---

## Low Findings

### R-L1: `_choose_template` returns None when templates list is empty — callers handle this but inconsistently
- **Severity:** Low | **Effort:** S
- **Location:** `src/youtube_uploader.py:175-180`
- **Issue:** If `title_templates` is `[]` (empty list, not None), `_choose_template` returns None because `not templates` is True for empty lists. This is correct behavior, but callers should be aware. Currently handled correctly in `build_upload_title` (falls back to `title_template` or default). No bug, just a subtle edge case to document.
- **Status:** No action needed.

### R-L2: `_sanitize_text` doesn't strip zero-width joiners/non-joiners
- **Severity:** Low | **Effort:** S
- **Location:** `src/youtube_uploader.py:161`
- **Issue:** The regex strips bidi control characters and C0 controls, but doesn't catch zero-width spaces (U+200B), zero-width joiners (U+200C, U+200D), and other invisible Unicode. These can make YouTube titles appear different from what the pipeline logged.
- **Fix:** Extend the regex to include `\u200b-\u200d\ufeff`.

### R-L3: Module-level `log = logging.getLogger(__name__)` in main.py shadowed inside functions
- **Severity:** Low | **Effort:** S
- **Location:** `main.py:127` vs `main.py:250`, `main.py:598`
- **Issue:** The module-level `log` (line 127) is shadowed by `log = logging.getLogger("pipeline")` in `run_pipeline` (line 250) and `log = logging.getLogger("main")` in `main()` (line 598). This is intentional for different logger names but makes it easy to use the wrong logger in helper functions. `_cleanup_tmp_files` and `clean_stale_tmp` use the module-level logger, while pipeline functions use the "pipeline" logger.
- **Status:** Minor inconsistency, no operational impact.

### R-L4: `download_clip` timeout of 120s may be too short for slow Twitch CDN
- **Severity:** Low | **Effort:** S
- **Location:** `src/downloader.py:34`
- **Issue:** yt-dlp has a 120s subprocess timeout. For a 60s clip at typical Twitch quality (~5Mbps), that's ~37MB which needs 120s to download on a ~2.5Mbps connection. GitHub Actions runners typically have fast connections, but this could fail on slow networks. The timeout kills the entire subprocess, losing all progress.
- **Fix:** Increase to 180s or add a configurable timeout in PipelineConfig.

### R-L5: `clip_overlaps` uses julianday() which doesn't understand ISO timezone offsets
- **Severity:** Low | **Effort:** S
- **Location:** `src/db.py:85`
- **Issue:** SQLite's `julianday()` function can parse ISO dates but doesn't handle timezone offsets correctly (it treats the `+00:00` suffix as literal text in some SQLite versions). Twitch clips use `Z` suffix, which `julianday()` handles. But if `created_at` ever contains `+00:00` instead of `Z`, the comparison could be wrong.
- **Fix:** Normalize `created_at` to strip timezone info before DB storage, or use Python's datetime for comparison instead of SQLite's julianday.

---

## Previously Fixed Findings (from 2026-02-05 audit)

These were identified in the original audit and have been verified as fixed:

| Original ID | Issue | Status |
|-------------|-------|--------|
| R-C2 / S-H4 | TOCTOU race in lockfile | **Fixed** — atomic `os.replace()` pattern (main.py:229-237) |
| R-H3 | No timeout on YouTube API calls | **Fixed** — `build(credentials=creds)` pattern (youtube_uploader.py:117) |
| R-H4 | channel dedup swallows fatal errors | **Fixed** — distinguishes 401/403 from transient (youtube_uploader.py:390-396) |
| R-C1 / A-M6 | Analytics fallback propagates unhandled | **Partially fixed** — fallback wrapped in try/except (youtube_analytics.py:49-52), but per-video guard still missing (see R-M2) |
| R-M7 | record_known_clip overwrites youtube_id | **Fixed** — COALESCE pattern (db.py:141) |
| R-H5 | No backoff on Twitch token refresh | **Fixed** — 2s backoff (twitch_client.py:29-31) |
| R-L3 | No PipelineConfig validation | **Fixed** — `__post_init__` with type coercion (models.py:69-112) |
| P-C1 | 7-9 ffmpeg probes per clip | **Fixed** — consolidated to 2 probes (video_processor.py:20-53, 84-123) |

---

## Risk Summary by Module

| Module | Risk Level | Key Concerns |
|--------|-----------|--------------|
| `captioner.py` | **HIGH** | No API timeout, no retry, no input validation (R-C1, R-H2, R-M8) |
| `youtube_uploader.py` | **MEDIUM** | Playlist cache keyed by `id()` (R-C3), verify_upload unused (R-M7) |
| `video_processor.py` | **MEDIUM** | Stale cache risk (R-H3), subtitle path injection (R-H4) |
| `main.py` | **LOW** | WAL checkpoint on error (R-M3), metrics loop not guarded (R-M2) |
| `db.py` | **LOW** | Connection leak on init_schema failure (R-H5), julianday edge case (R-L5) |
| `twitch_client.py` | **LOW** | Pagination loop unbounded (R-M5) |
| `dedup.py` | **LOW** | Invalid timestamps bypass overlap check (R-M6) |
| `downloader.py` | **LOW** | Timeout may be tight (R-L4) |
| `media_utils.py` | **LOW** | extract_audio lacks error handling (R-H1) |
| `models.py` | **LOW** | FacecamConfig unvalidated (R-M9) |
| `clip_filter.py` | **CLEAN** | No reliability issues found |
| `youtube_analytics.py` | **LOW** | Dormant; previously fixed analytics fallback |

---

## Priority Matrix

### P0 — Quick Wins (S effort, Critical/High impact)
| # | Finding | Effort | Impact |
|---|---------|--------|--------|
| 1 | R-C1: Add Deepgram API timeout | S | Critical |
| 2 | R-C2: Cap audio extraction duration | S | Critical |
| 3 | R-C3: Fix playlist cache key (use creds file, not id()) | S | Critical |
| 4 | R-H1: Add error handling to extract_audio | S | High |
| 5 | R-H3: Validate cached vertical clip with is_valid_video | S | High |
| 6 | R-H5: Close DB connection on init_schema failure | S | High |

### P1 — Important (S-M effort, Medium impact)
| # | Finding | Effort | Impact |
|---|---------|--------|--------|
| 7 | R-H2: Add retry logic to Deepgram API call | M | High |
| 8 | R-H4: Escape additional special chars in subtitle path | S | High |
| 9 | R-M2: Per-video try/except in metrics sync loop | S | Medium |
| 10 | R-M5: Add max_pages guard to Twitch pagination | S | Medium |
| 11 | R-M6: Skip clips with invalid timestamps in batch dedup | S | Medium |
| 12 | R-M7: Wire verify_upload back into pipeline | S | Medium |
| 13 | R-M8: Validate word timing data in ASS generation | S | Medium |
| 14 | R-M9: Add FacecamConfig validation | S | Medium |

### P2 — Nice to Have
| # | Finding | Effort | Impact |
|---|---------|--------|--------|
| 15 | R-M3: Only WAL checkpoint on clean exit | S | Low |
| 16 | R-M4: Don't aggressively clean tmp on failure | S | Low |
| 17 | R-L2: Extend sanitize_text for zero-width chars | S | Low |
| 18 | R-L4: Increase download timeout to 180s | S | Low |
| 19 | R-L5: Normalize created_at timezone before storage | S | Low |

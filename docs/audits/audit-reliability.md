# Reliability Audit — 2026-02-05

## Critical

### R-C1: Analytics Fallback Query Propagates Unhandled HttpError
- **Location:** `src/youtube_analytics.py:47-49`
- **Description:** When the primary metrics query fails with an `HttpError`, the code catches it and attempts a fallback query. However, if the fallback query (`_query_metrics` with `_METRICS_FALLBACK`) also raises an `HttpError` or any other exception, it propagates unhandled out of `fetch_video_metrics`. The caller in `main.py:164` calls `fetch_video_metrics` in a bare loop with no per-video exception handling -- a single fallback failure aborts the entire analytics sync for that streamer.
- **Impact:** One bad video ID (deleted, private, or API-transient error) halts metrics sync for all remaining videos for that streamer in that run.
- **Recommendation:** Wrap the fallback call in its own try/except and return `None` on failure, or wrap the per-video call in `_sync_streamer_metrics` with try/except to isolate failures.

### R-C2: Lock File Race Between `os.remove` and `_try_create_lock`
- **Location:** `main.py:230-234`
- **Description:** In `acquire_lock()`, after detecting a stale PID, the code calls `os.remove(LOCK_FILE)` then `_try_create_lock()`. Between these two calls, another process (or a concurrent CI trigger) could also detect the stale PID, remove the lock, and create its own lock. The second process's `_try_create_lock` would then succeed, and so would the first process's, resulting in two processes holding the "lock" simultaneously. While the `concurrency` block in CI mitigates this for scheduled runs, local manual runs or `workflow_dispatch` with local runs have no such protection.
- **Impact:** Two pipeline instances could run simultaneously, causing duplicate uploads or DB corruption.
- **Recommendation:** Use `os.replace()` to atomically write the new PID to the lock file rather than remove-then-create. Or use `fcntl.flock`/`msvcrt.locking` for advisory file locking.

### R-C3: Resumable Upload Chunk Loop Can Silently Drop the Last Retry Error
- **Location:** `src/youtube_uploader.py:265-280`
- **Description:** The upload chunk retry loop does `for attempt in range(4)` with `break` on success. If all 4 attempts fail on a retryable 5xx error, the inner `raise` re-raises the last `HttpError`. However, if a `ConnectionError` or `TimeoutError` occurs on attempt 3 (the last retry), the condition `retryable and attempt < 3` is `True` (attempt=3, `3 < 3` is `False`), so it falls through to `raise`. This is correct. BUT: if `response` remains `None` after the inner loop completes without raising (because `break` was never reached and no exception was raised -- which cannot happen in the current logic but is fragile if the loop is ever modified), the outer `while` continues with `response is None`, potentially looping to `max_chunks` with repeated failures. The `max_chunks=1000` safety net prevents infinite looping but could result in 1000 failed API calls burning quota.
- **Impact:** Theoretical risk of burning up to 1000 API calls on a persistently failing upload if the retry logic is ever modified.
- **Recommendation:** Add a break/return after the inner `for` loop if no `break` was executed (i.e., all attempts failed). A simple `else` clause on the `for` loop that raises would make the contract explicit.

## High

### R-H1: `_sync_streamer_metrics` Has No Transaction Boundary
- **Location:** `main.py:142-170`
- **Description:** `_sync_streamer_metrics` calls `update_youtube_metrics` and `touch_youtube_metrics_sync` in a loop. Each of these functions does its own `conn.commit()`. If the process crashes mid-loop, some videos will have updated metrics and others won't, with `yt_last_sync` updated for some but not all. While this isn't data loss, it means the next run's `get_clips_for_metrics` query may skip videos whose metrics were partially updated (because `yt_last_sync` was touched) while leaving stale data.
- **Impact:** Partial metric updates that look complete because `yt_last_sync` was touched, masking stale data.
- **Recommendation:** Either batch all updates in a single transaction, or ensure `yt_last_sync` is set atomically with the metrics update (which `update_youtube_metrics` already does -- the separate `touch_youtube_metrics_sync` on the no-data path is fine).

### R-H2: `conn.commit()` Per-Row in Hot Path (insert_clip, increment_fail_count)
- **Location:** `src/db.py:121`, `src/db.py:173`
- **Description:** Each `insert_clip` and `increment_fail_count` call does an immediate `conn.commit()`. While this is correct for safety (the "record before verify" pattern requires immediate persistence), it means that in a theoretical multi-clip upload scenario, each commit forces a WAL sync to disk. On CI (ext4 + GitHub Actions ephemeral disk), this is ~0.5ms per commit. But if the DB is on a slow filesystem or NFS mount, this becomes a bottleneck.
- **Impact:** Low impact currently (1 upload per run), but could become a bottleneck if `max_uploads_per_window` is increased.
- **Recommendation:** Keep current behavior for upload safety. Document that `insert_clip` intentionally commits immediately. If batch operations are ever added, use explicit transaction wrapping.

### R-H3: No Timeout on YouTube API Calls (Non-Upload)
- **Location:** `src/youtube_uploader.py:307,318,347` and `src/youtube_analytics.py:24-30`
- **Description:** YouTube API calls (`channels().list`, `playlistItems().list`, `videos().list`, `reports().query`) use the default `googleapiclient` HTTP transport with no explicit timeout. If the YouTube API hangs (DNS resolution failure, network partition, API gateway timeout), these calls will block indefinitely.
- **Impact:** Pipeline hangs forever waiting for a YouTube API response. The CI `timeout-minutes: 60` provides an outer bound, but local runs have no such safety net.
- **Recommendation:** Set `http = httplib2.Http(timeout=30)` or use `google.auth.transport.requests.AuthorizedSession` with a timeout parameter when building the service.

### R-H4: `check_channel_for_duplicate` Swallows All Exceptions Silently
- **Location:** `src/youtube_uploader.py:336-341`
- **Description:** Both the `HttpError` and generic `Exception` handlers in `check_channel_for_duplicate` return `None`, meaning the pipeline proceeds with the upload. If the YouTube API is down or the credentials are invalid, every single clip will bypass the dedup check and potentially create duplicates. The function logs a warning but doesn't distinguish between "API temporarily unavailable" (safe to proceed) and "credentials revoked" (all subsequent calls will also fail).
- **Impact:** If YouTube API is partially down, all clips bypass dedup and could create duplicates -- exactly the scenario the 3-layer defense was designed to prevent. Layer 3 becomes a no-op.
- **Recommendation:** Consider raising on persistent failures (e.g., if the channel lookup itself fails with a non-transient error like 401/403), rather than silently falling through.

### R-H5: Twitch Token Refresh Has No Backoff
- **Location:** `src/twitch_client.py:24-37`
- **Description:** `_get_token()` calls `resp.raise_for_status()` directly. If Twitch's token endpoint returns a 5xx, the caller (`_request`) retries up to 3 times, but each retry calls `_get_token()` again (because `self._token` was set to `None` on 401). There's no exponential backoff on the token request itself. A Twitch outage triggers rapid-fire token requests.
- **Impact:** Rapid token request hammering during Twitch outages. Could trigger rate limiting or IP blocks.
- **Recommendation:** Add a short backoff (1-2s) between token acquisition failures, or cache the failure for a few seconds.

### R-H6: `_run_ffmpeg` Popen Stderr Pipe Can Deadlock on Large Output
- **Location:** `src/video_processor.py:356-358`
- **Description:** `subprocess.Popen` with `stdout=PIPE, stderr=PIPE` followed by `proc.communicate(timeout=300)` is correct and avoids the deadlock that would occur with manual `proc.stdout.read()`. However, if `communicate` itself is interrupted by an exception other than `TimeoutExpired` (e.g., `MemoryError` from a huge stderr buffer), the process is killed and waited on in the outer except block. This is handled correctly. No actual bug here, but the 300s timeout is quite generous -- an ffmpeg encode of a 60s clip should complete in under 60s on CPU.
- **Impact:** Low. The timeout is a safety net, not a functional concern.
- **Recommendation:** Consider reducing the timeout to 120s for faster failure detection.

## Medium

### R-M1: WAL Checkpoint in `run_pipeline` Finally Block Can Mask Original Exception
- **Location:** `main.py:250-253`
- **Description:** The `finally` block in `run_pipeline` catches exceptions from `PRAGMA wal_checkpoint(TRUNCATE)` and logs a warning, but if the original exception from `_run_pipeline_inner` is in flight, Python will correctly propagate the original. However, if the checkpoint itself raises and the original block completed normally, the warning is logged but the non-zero exit code from checkpoint failure is swallowed. This is minor since WAL checkpoint failure is non-fatal.
- **Impact:** WAL file may grow unbounded if checkpointing consistently fails, but this is unlikely.
- **Recommendation:** Current behavior is acceptable. Consider logging at ERROR level instead of WARNING if checkpoint fails after a successful pipeline run.

### R-M2: `clean_stale_tmp` Runs During `finally` Even on Failure Paths
- **Location:** `main.py:254`
- **Description:** `clean_stale_tmp` runs in the `finally` block of `run_pipeline`, which means it runs even if the pipeline crashed. If the crash left files in `tmp_dir` that are less than `max_age_hours` old, they won't be cleaned. If the crash left files older than `max_age_hours`, they will be cleaned even if they contain useful debugging artifacts. The 1-hour cutoff (passed in the call) means only files older than 1 hour are cleaned, which is appropriate. No actual bug.
- **Impact:** Minimal. Stale files from the current run (< 1 hour old) accumulate until the next run.
- **Recommendation:** Current behavior is acceptable.

### R-M3: `filter_new_clips` Uses String Format SQL with Unbounded IN Clause
- **Location:** `src/dedup.py:29-35`
- **Description:** The `IN ({placeholders})` clause is constructed from `clip_ids`, which could be up to 500 items (from `max_clips=500` in `fetch_clips`). SQLite's default `SQLITE_MAX_VARIABLE_NUMBER` is 999 (or 32766 in newer versions), so 500 is safe. However, if `max_clips` is ever increased significantly, this could hit the limit.
- **Impact:** Would cause a `sqlite3.OperationalError` if clip count exceeds SQLite's variable limit.
- **Recommendation:** Consider batching the query if clip counts could exceed 500 in the future. Currently safe.

### R-M4: `_has_facecam` Division by Zero if Duration is 0
- **Location:** `src/video_processor.py:233`
- **Description:** `seek_time = str(max(1, int(duration * pct))) if duration else "1"`. If `duration` is `0.0` (truthy in Python), `int(0.0 * 0.25)` = 0, then `max(1, 0)` = 1. So it's actually safe. But if `duration` is negative (which `_get_duration` could theoretically return if ffprobe output is malformed), `int(negative * pct)` would produce a negative seek time, causing ffmpeg to seek to an invalid position.
- **Impact:** FFmpeg would likely fail gracefully (output no frames), causing `_has_facecam` to return `False`. Low risk.
- **Recommendation:** Add `duration = max(duration or 0, 0.1)` guard.

### R-M5: No Test Coverage for Main Pipeline Orchestration
- **Location:** `main.py:244-484`
- **Description:** The `run_pipeline` and `_run_pipeline_inner` functions have zero test coverage. These are the most complex orchestration functions in the codebase -- they coordinate download, processing, upload, dedup, cleanup, and analytics sync. Any regression in the interaction between these steps (e.g., the upload starvation bug from lessons.md) can only be caught by manual testing or production failures.
- **Impact:** Regressions in pipeline orchestration logic are invisible until they cause production failures.
- **Recommendation:** Add integration tests with mocked external services (Twitch API, YouTube API, ffmpeg) that exercise the key pipeline paths: happy path, download failure fallback, upload quota exhaustion, duplicate detection, spacing enforcement.

### R-M6: `_detect_leading_silence` Returns 0.0 on ffmpeg Failure
- **Location:** `src/video_processor.py:140-142`
- **Description:** If ffmpeg fails entirely (e.g., corrupt audio stream), `_detect_leading_silence` returns `0.0`, meaning the clip is processed without silence trimming. This is the correct fallback behavior. However, the function also returns `0.0` if the regex parsing fails (no `silence_start` match), which could happen if ffmpeg changes its output format. The fallback is safe but could silently disable silence trimming.
- **Impact:** Silence trimming silently disabled if ffmpeg output format changes. Low risk.
- **Recommendation:** Log a debug message when no silence is detected vs. when detection fails entirely.

### R-M7: `record_known_clip` ON CONFLICT Overwrites `youtube_id` Unconditionally
- **Location:** `src/db.py:124-136`
- **Description:** If `record_known_clip` is called for a clip that already has a different `youtube_id` (e.g., the clip was uploaded legitimately and then a channel dedup check finds a different video with the same title), the existing `youtube_id` is overwritten. The test `test_record_known_clip_does_not_overwrite_posted_at` confirms `posted_at` is preserved, but `youtube_id` is explicitly updated. If the existing `youtube_id` was from a real upload and the new one is from a false-positive title match, the real upload record is lost.
- **Impact:** False-positive duplicate detection could overwrite a legitimate YouTube ID with an incorrect one. Edge case but data-corrupting.
- **Recommendation:** Only update `youtube_id` if the existing value is NULL: `youtube_id = COALESCE(clips.youtube_id, excluded.youtube_id)`.

## Low

### R-L1: `_is_valid_video` Catches All Exceptions
- **Location:** `src/downloader.py:85-87`
- **Description:** The blanket `except Exception` in `_is_valid_video` catches everything including `KeyboardInterrupt` (which is `BaseException`, so actually not caught). It catches `MemoryError`, `SystemExit` subclasses that inherit from Exception, etc. In practice, the only expected exceptions are `subprocess.TimeoutExpired` and `OSError`.
- **Impact:** Could mask unexpected errors during development/debugging. Low risk in production.
- **Recommendation:** Narrow to `except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError)`.

### R-L2: Twitch `_request` Assert at Line 67
- **Location:** `src/twitch_client.py:67`
- **Description:** `assert resp is not None` is used after the retry loop. If Python is run with `-O` (optimize), asserts are stripped. In that case, `resp.raise_for_status()` on the next line would raise `AttributeError: 'NoneType' object has no attribute 'raise_for_status'`. This is a minor readability issue -- the assert is logically correct since the loop always executes at least once.
- **Impact:** Confusing error message if running with `-O`. Extremely unlikely in practice.
- **Recommendation:** Replace with `if resp is None: raise RuntimeError("No response after retries")`.

### R-L3: `load_config` Does Not Validate YAML Structure
- **Location:** `main.py:62-76`
- **Description:** `load_config` trusts that `raw.get("pipeline", {})` produces a dict whose keys match `PipelineConfig` fields. If `config.yaml` contains a typo (e.g., `max_clips_per_stremer` instead of `max_clips_per_streamer`), it's silently ignored and the default value is used. Similarly, unknown keys in the YAML are silently accepted.
- **Impact:** Configuration typos are invisible. A user could think they changed a setting when it's actually using the default.
- **Recommendation:** Add a warning for unknown keys in the pipeline config section.

### R-L4: No Retry on Twitch `fetch_clips` Pagination
- **Location:** `src/twitch_client.py:94-126`
- **Description:** The pagination loop calls `self._request("GET", CLIPS_URL, params=params)` which has its own 3-retry logic. However, if the first page succeeds and the second page fails after all retries, the function raises and all clips from the first page are lost. The caller in `main.py:298` catches the exception and `continue`s to the next streamer.
- **Impact:** Partial clip data is discarded on pagination failure. For most streamers with < 100 clips, pagination never triggers.
- **Recommendation:** Consider returning partial results on pagination failure rather than raising.

### R-L5: `_filter_batch_overlaps` Appends Clip with Invalid `created_at` Without Timestamp Tracking
- **Location:** `src/dedup.py:54-57`
- **Description:** If `datetime.fromisoformat(c.created_at)` fails, the clip is appended to `result` without adding its timestamp to `accepted_by_streamer`. This means subsequent clips cannot check overlap against this clip within the batch. The clip with invalid `created_at` is essentially invisible to batch dedup.
- **Impact:** Could allow a near-duplicate clip through if the first clip has a malformed timestamp. Very unlikely since Twitch API returns ISO timestamps.
- **Recommendation:** Either skip the clip entirely or parse the timestamp earlier in the pipeline.

### R-L6: Test Coverage Gap: No Tests for `download_clip`, `video_processor`, or `twitch_client`
- **Location:** `tests/` directory
- **Description:** The test suite covers `db.py`, `dedup.py`, `clip_filter.py`, and parts of `youtube_uploader.py`. There are no tests for `download_clip` (yt-dlp interaction), `video_processor` (ffmpeg orchestration), or `twitch_client` (API pagination, token refresh, rate limiting). These are the most I/O-heavy and failure-prone modules.
- **Impact:** Regressions in download, video processing, or Twitch API handling are invisible until production failure.
- **Recommendation:** Add unit tests with mocked subprocess calls for `download_clip` and `crop_to_vertical`, and mocked HTTP responses for `TwitchClient`.

---

## Previously Known Issues (from tasks/lessons.md)

The following issues were identified in previous audits and are referenced here for completeness. They are NOT new findings:

- **PID lock on Windows** (`os.kill` unreliable) -- Fixed with ctypes `OpenProcess + GetExitCodeProcess` approach
- **Loop slicing starvation** (Bug #8) -- Fixed by iterating all candidates
- **Self-matching overlap** (Bug #2) -- Fixed with `exclude_clip_id` parameter
- **Duplicate recording poisons spacing** (Bug #18) -- Fixed with `record_known_clip`
- **DB cache `if: always()`** causing duplicate uploads -- Fixed with `if: success()`
- **`gh secret set --body -`** corruption -- Fixed by omitting `--body`
- **Token save guard** -- Implemented with `if [ -s file ]`
- **Artifact fallback** -- Implemented in pipeline.yml
- **Channel dedup** -- Implemented with `playlistItems.list`
- **`HttpError.error_details` can be string** -- Fixed with `isinstance` check in `_extract_error_reason`
- **ctypes `restype` on 64-bit Windows** -- Fixed with explicit `restype`/`argtypes`

# Performance Audit — 2026-02-05

## Critical

### P-C1: Facecam detection spawns 9 separate ffmpeg processes per clip
- **Location:** `src/video_processor.py:214-260` (`_has_facecam`) and `src/video_processor.py:145-211` (`crop_to_vertical`)
- **Description:** For each clip with `facecam_mode: "auto"`, the pipeline runs:
  1. `_get_duration` (1 ffprobe process) — line 160
  2. `_detect_leading_silence` (1 ffmpeg process) — line 166
  3. `_has_facecam` at 25%, 50%, 75% timestamps (3 ffmpeg processes) — lines 232-241
  4. `_get_dimensions` (1 ffprobe process) — line 183
  5. `_measure_loudness` (1 ffmpeg process, full audio decode) — line 200
  6. `_run_ffmpeg` GPU attempt (1 ffmpeg process) — line 206
  7. `_run_ffmpeg` CPU fallback (1 ffmpeg process) — line 208

  That is 7-9 subprocess invocations per clip before the final encode. Each ffmpeg/ffprobe subprocess has process startup overhead (~100-300ms on Linux, ~500ms on Windows), and several of them decode the full audio or video stream.
- **Impact:** For 6 clips, that is 42-54 subprocess spawns. On a GitHub Actions runner, ffmpeg subprocess startup is cheap but the full audio decode in `_measure_loudness` (line 271, 60s timeout) and the three `_has_facecam` probes are the dominant cost. Estimated: ~15-30s per clip in subprocess overhead alone (excluding the actual encode). Total: ~2-3 minutes of pure overhead per 6-clip run.
- **Recommendation:** Consolidate probes. A single `ffprobe -show_format -show_streams` call (1 process) can return duration, dimensions, and codec info simultaneously. The three facecam YDIF samples could be combined into a single ffmpeg call that seeks to all three timestamps in one invocation using a complex filtergraph. This would reduce 6 subprocess calls to 2 per clip.

### P-C2: `clip_overlaps` is called per-clip with no index on the computed expression
- **Location:** `src/db.py:71-80` (`clip_overlaps`), called from `src/dedup.py:42`
- **Description:** For every candidate clip, `filter_new_clips` calls `clip_overlaps` which runs:
  ```sql
  SELECT 1 FROM clips WHERE streamer = ?
    AND ABS(julianday(created_at) - julianday(?)) * 86400 < ?
  ```
  The `julianday()` computation on `created_at` prevents SQLite from using the `idx_clips_streamer` index efficiently. SQLite must compute `julianday(created_at)` for every row with the matching streamer, making this an O(N) scan per call where N is the number of clips for that streamer.
- **Impact:** With 1 streamer and 500 fetched clips (max), after dedup there may be ~6 candidates. Each scans all rows for that streamer. Currently the DB is small (<100 rows), so this is fast. But at 10 streamers uploading 1/day for a year, that's ~3,650 rows per streamer. 6 candidates * 3,650 rows = 21,900 `julianday()` computations per streamer per run. Still under 100ms on SQLite, but the algorithmic complexity is O(candidates * total_clips_per_streamer).
- **Recommendation:** Two options: (1) Add a `created_at_epoch INTEGER` column and use a simple range query with an index, or (2) filter by a coarse time range first (`WHERE created_at >= ? AND created_at <= ?` using string comparison, which works for ISO 8601) to narrow the scan before the expensive `julianday()` computation.

## High

### P-H1: `check_channel_for_duplicate` makes 2+ API calls per clip (up to 4 quota units each)
- **Location:** `src/youtube_uploader.py:300-341`, called from `main.py:397`
- **Description:** For every new clip that passes dedup, the pipeline calls `check_channel_for_duplicate` which:
  1. `channels().list(part="contentDetails", mine=True)` — 1 quota unit
  2. `playlistItems().list(part="snippet", ...)` — 1 quota unit per page (up to `max_results/50` pages)

  With `max_results=50` (default), this is 2 quota units per clip. For 6 clips per streamer, that is 12 quota units. With the daily YouTube API quota of 10,000 units, and each video upload costing 1,600 units, this overhead is small (~0.12%). However, the `channels().list` call returns the same uploads playlist ID every time — it is invariant for the authenticated user within a run.
- **Impact:** 6 redundant `channels().list` API calls per streamer per run. Each call adds ~200-500ms of network latency. At scale (10 streamers), that is 60 redundant API calls, wasting ~12-30s of wall clock time.
- **Recommendation:** Cache the `uploads_playlist` ID. Either (a) pass it as a parameter after the first lookup, (b) cache it on the service object/wrapper, or (c) look it up once in `_run_pipeline_inner` and pass it to `check_channel_for_duplicate`.

### P-H2: `_measure_loudness` does a full audio decode — entire file decoded just for stats
- **Location:** `src/video_processor.py:263-283`
- **Description:** The 2-pass loudnorm first pass decodes the entire audio stream to compute loudness statistics. For a 60-second clip at 48kHz stereo, this means decoding ~5.5MB of raw audio data. The command uses `-vn` (no video) which is correct, but the full audio decode is unavoidable for accurate loudnorm stats.
- **Impact:** ~2-5 seconds per clip for the first pass. This is already hoisted out of the GPU/CPU retry loop (good, per lessons.md), but it is still the second most expensive operation after the final encode. For 6 clips, ~12-30s total.
- **Recommendation:** This is an inherent cost of 2-pass EBU R128 normalization and cannot be eliminated without switching to single-pass loudnorm (which produces inferior results — pumping artifacts on speech). The current approach is correct. One minor optimization: add `-nostdin` and `-hide_banner` flags to reduce startup overhead, and consider adding `-threads 1` since the audio decode is not CPU-bound enough to benefit from multithreading.

### P-H3: Sequential streamer processing — no parallelism between streamers
- **Location:** `main.py:291` (`for streamer in streamers:`)
- **Description:** The pipeline processes streamers sequentially: fetch clips, filter, download, process, upload for streamer A, then repeat for streamer B. At 1 streamer this is fine. At N streamers, total wall-clock time scales linearly.
- **Impact:** Each streamer takes ~3-8 minutes (fetch + 6 clips * (download ~5s + process ~30s + upload ~20s)). At 10 streamers: ~30-80 minutes. The GitHub Actions timeout is 60 minutes (pipeline.yml:14).
  - Download is I/O-bound (network)
  - Processing is CPU-bound (ffmpeg encode)
  - Upload is I/O-bound (network + YouTube API)

  These could overlap via a producer-consumer pattern, but the single-process architecture and shared SQLite connection make this non-trivial.
- **Recommendation:** For near-term scaling (2-5 streamers), the sequential approach is fine within the 60-minute timeout. For 10+ streamers, consider: (a) parallel Twitch API fetching with `concurrent.futures.ThreadPoolExecutor`, (b) parallel ffmpeg processing (each ffmpeg is already a separate process), or (c) splitting into multiple GitHub Actions jobs per streamer. Option (c) is the simplest but requires per-streamer DB partitioning or a shared artifact.

### P-H4: `apt-get update && install` runs every CI run (~15-25s)
- **Location:** `.github/workflows/pipeline.yml:28-31`
- **Description:** Every pipeline run installs `ffmpeg` and `sqlite3` via `apt-get update && apt-get install -y`. The `ubuntu-latest` image already includes `sqlite3` (it is a dependency of Python 3.12). The `ffmpeg` package download is ~2-3MB and `apt-get update` refreshes the entire package index (~10-15s).
- **Impact:** 15-25 seconds of wasted time per run. At 6 runs/day, that is ~2-3 minutes/day of CI time.
- **Recommendation:** (1) Check if `ffmpeg` is already installed on `ubuntu-latest` (it is, as of 2025). Remove the install step or gate it with `which ffmpeg || sudo apt-get install -y ffmpeg`. (2) Remove `sqlite3` from the install list — it is already present and the pipeline uses Python's built-in `sqlite3` module, not the CLI tool (except for the WAL checkpoint step on line 83, which can be replaced with a Python one-liner).

## Medium

### P-M1: `_sample_ydif` in `extract_thumbnail` spawns N+1 ffmpeg processes for N samples
- **Location:** `src/video_processor.py:53-72` called from `src/video_processor.py:97-101`
- **Description:** `extract_thumbnail` calls `_sample_ydif` once per sample timestamp (default 8). Each call spawns a separate ffmpeg process that seeks to a timestamp, decodes 1 frame, and runs `signalstats`. Then a final ffmpeg process extracts the best frame.
- **Impact:** 9 ffmpeg subprocess spawns per thumbnail extraction. With `thumbnail_enabled: false` in the current config, this code path is dormant. When enabled, it would add ~5-10s per clip.
- **Recommendation:** All 8 samples could be extracted in a single ffmpeg call using segment-based seeking or a complex filtergraph that outputs stats for all timestamps. Low priority since thumbnails are disabled.

### P-M2: Redundant `_get_duration` calls
- **Location:** `src/video_processor.py:160` and `src/video_processor.py:226-227`
- **Description:** `crop_to_vertical` calls `_get_duration` at line 160. Then `_has_facecam` accepts an optional `duration` parameter and uses the already-computed value (line 180 passes `duration=duration`). This is already optimized. However, if `extract_thumbnail` is called later (line 449 in main.py), it calls `_get_duration` again (line 86) on the already-processed vertical clip, which has a different path. This is a separate file so the re-probe is necessary.
- **Impact:** Minimal — one extra ffprobe call (~200ms) per clip when thumbnails are enabled. Currently dormant.
- **Recommendation:** No action needed given current config. If thumbnails are enabled, consider passing duration from the caller.

### P-M3: `filter_new_clips` makes N+1 database queries (batch + per-clip overlap)
- **Location:** `src/dedup.py:20-44`
- **Description:** `filter_new_clips` does one batch query for existing clip IDs (line 29-35), then iterates each remaining clip calling `clip_overlaps` individually (line 42). This is an N+1 query pattern: 1 batch query + M individual queries where M is the number of clips not in the existing set.
- **Impact:** With 500 fetched clips, ~300 pass the view count filter, ~294 pass the batch existing check, each triggers a `clip_overlaps` query. That is 295 SQLite queries. Each query is sub-millisecond on a small DB, so total is <300ms. Not a bottleneck at current scale.
- **Recommendation:** Could batch the overlap check by fetching all clips for the streamer within the relevant time window in one query, then doing the overlap logic in Python. Low priority — SQLite handles this volume easily.

### P-M4: `conn.commit()` called after every single DB write
- **Location:** `src/db.py:121,136,162,173,222,230` — every write function ends with `conn.commit()`
- **Description:** Each `insert_clip`, `record_known_clip`, `increment_fail_count`, `update_youtube_metrics`, and `touch_youtube_metrics_sync` call issues a separate `conn.commit()`. In WAL mode, each commit is an `fsync` on the WAL file.
- **Impact:** For a run that processes 6 clips: ~6 `increment_fail_count` or `insert_clip` calls + 1 `update_streamer_stats` = ~7 commits. Each WAL commit is ~1-5ms (SSD). Total: <35ms. Not a bottleneck.

  However, in the analytics sync path (`_sync_streamer_metrics` in main.py:158-170), `update_youtube_metrics` is called per video with individual commits. At `analytics_max_videos_per_run: 20`, that is 20 commits. Still fast, but could be a single transaction.
- **Recommendation:** For correctness, the `insert_clip` after upload MUST commit immediately (per the "record before verify" lesson). Other writes (fail counts, stats updates, metrics) could be batched in a single transaction. Low priority.

### P-M5: YouTube upload uses default chunk size (implicit in `MediaFileUpload`)
- **Location:** `src/youtube_uploader.py:257`
- **Description:** `MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)` uses the default chunk size of 256KB (as of google-api-python-client). For a 60-second 1080x1920 video at CRF 20, typical file sizes are 15-40MB. With 256KB chunks, that is 60-160 upload chunks, each requiring a round-trip HTTP request.
- **Impact:** Each chunk upload has ~50-200ms of HTTP overhead (TLS handshake is reused, but each chunk is a separate PUT). For a 30MB file: ~120 chunks * ~100ms overhead = ~12s of network overhead on top of the raw transfer time. With a 1MB chunk size: ~30 chunks * ~100ms = ~3s overhead.
- **Recommendation:** Set `chunksize=5*1024*1024` (5MB) in `MediaFileUpload`. This reduces the number of round-trips by 20x for typical file sizes. The tradeoff is that a failed chunk means re-uploading up to 5MB, but the retry logic already handles this. The Google API client docs recommend 5-10MB chunks for files over 10MB.

### P-M6: `blocklist.txt` is re-read from disk on every `filter_new_clips` call
- **Location:** `src/dedup.py:12-17` (`load_blocklist`), called from `src/dedup.py:26`
- **Description:** `load_blocklist()` opens and reads `data/blocklist.txt` from disk on every pipeline invocation. This file is read once per streamer per run.
- **Impact:** Negligible — file I/O for a small text file is sub-millisecond. At 10 streamers, 10 reads of the same file.
- **Recommendation:** No action needed. If the blocklist grows large (1000+ entries), consider caching it at the pipeline level and passing it as a parameter.

## Low

### P-L1: `DISABLE_GPU_ENCODE` check is inside the per-clip loop
- **Location:** `src/video_processor.py:203` (inside `crop_to_vertical`, called per clip)
- **Description:** `os.environ.get("DISABLE_GPU_ENCODE")` is evaluated for every clip. The environment variable does not change during execution.
- **Impact:** Negligible — `os.environ.get` is a dict lookup (~100ns).
- **Recommendation:** Could hoist to module level or `crop_to_vertical` caller for cleanliness, but not worth the code churn.

### P-L2: `hashlib.md5` used for A/B template selection
- **Location:** `src/youtube_uploader.py:148-151` (`_choose_template`)
- **Description:** MD5 is computed on the clip ID for deterministic template selection. MD5 is cryptographically broken but is fine for non-security hash distribution.
- **Impact:** Negligible — MD5 of a short string takes <1 microsecond.
- **Recommendation:** No performance concern. If md5 is ever flagged by security scanning tools, `hashlib.sha256` or a simple `hash()` would work, but this is purely cosmetic.

### P-L3: String-based ISO timestamp comparisons in SQL
- **Location:** `src/db.py:90` (`recent_upload_count`), `src/db.py:145` (`update_streamer_stats`), `src/db.py:184-186` (`get_clips_for_metrics`)
- **Description:** Several queries compare `posted_at >= ?` or `created_at >= ?` using string comparison on ISO 8601 timestamps. This works correctly because ISO 8601 sorts lexicographically when timezone offsets are consistent (all UTC), but it prevents SQLite from using the `idx_clips_posted` index as efficiently as integer epoch comparisons would.
- **Impact:** SQLite can still use the B-tree index on `posted_at` for string range comparisons, so this is not a correctness issue. The performance difference vs integer epoch is unmeasurable at current scale (<1000 rows).
- **Recommendation:** No action needed at current scale. If the DB grows to 100K+ rows, consider adding an `posted_at_epoch INTEGER` column with an index.

### P-L4: GitHub Actions cache delete + save is 2 API calls that could race
- **Location:** `.github/workflows/pipeline.yml:85-97`
- **Description:** The workflow deletes the old cache (line 90) then saves a new one (line 94). Both are gated on `if: success()` and run sequentially, which is correct. However, `continue-on-error: true` on the delete step means a delete failure (e.g., cache already evicted) does not block the save. The save step will fail if the key already exists (immutable cache keys).
- **Impact:** If the delete fails silently and the cache key already exists, the save silently fails too (cache keys are immutable). The artifact fallback (line 99-106) provides durability, so this is not a data loss risk. But it means the next run might restore a stale cache.
- **Recommendation:** This is already well-understood from lessons.md. The current mitigation (artifact fallback) is sound. No additional action needed.

### P-L5: `_run_pipeline_inner` re-evaluates config values from `raw_config` dict on every call
- **Location:** `main.py:260-282`
- **Description:** Template strings, tag lists, and thumbnail settings are extracted from `raw_config` dict at the top of `_run_pipeline_inner`. This happens once per pipeline run, not per streamer.
- **Impact:** Zero — single dict lookups, once per run.
- **Recommendation:** No action needed.

## Scaling Analysis

### At 10 streamers:
- **Twitch API:** 10 `fetch_clips` calls (paginated, ~1-3 requests each) + 10 `get_game_names` calls = ~20-40 API calls. Twitch rate limit is 800 requests/minute. No concern.
- **YouTube API:** 10 * (2 quota for channel dedup + 1,600 for upload) * max 6 clips = ~96,120 quota units. Daily limit is 10,000. **This is the hard ceiling**: at 10 streamers uploading 1 clip/run * 6 runs/day = 60 uploads = 96,000 quota units. This exceeds the 10,000 daily limit by ~10x. Even at 1 upload/streamer/day, 10 uploads = 16,000 quota units, still over the limit.
- **GitHub Actions:** 60-minute timeout. At ~8 min/streamer, 10 streamers = ~80 minutes. Exceeds timeout.
- **DB:** ~3,650 rows/streamer/year = 36,500 rows. All queries are indexed or small. No concern.
- **Disk:** 6 clips * 10 streamers * ~30MB each = ~1.8GB in tmp. GitHub Actions runners have ~14GB free. No concern, especially with cleanup.

### At 50 streamers:
- **YouTube API quota is the binding constraint.** 50 streamers * 1 upload/day = 80,000 quota units. Impossible without a quota increase (Google Cloud project must apply).
- **GitHub Actions:** Would need parallel jobs or a self-hosted runner.
- **DB:** ~182,500 rows. SQLite handles this easily with proper indexes.

### Bottom line:
The pipeline is well-optimized for 1-3 streamers. The first bottleneck at scale is YouTube API quota (hard 10,000 units/day), not code performance. The second bottleneck is GitHub Actions runtime limits. Code-level optimizations would yield diminishing returns compared to addressing these external constraints.

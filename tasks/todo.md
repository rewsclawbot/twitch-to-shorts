# Twitch-to-Shorts: Iteration & Improvement Plan

> Synthesized from parallel audit by @code-quality-pragmatist, @Jenny, @claude-md-compliance-checker

---

## Wave 1: Bug Fixes (Correctness)
*These affect whether the pipeline produces correct output.*

- [x] **Fix score/filter mismatch in clip_filter.py** — `clip_filter.py:41` thresholds on raw `view_count` but the whole point of the scoring system is velocity-weighted ranking. The threshold should use `score`, not `view_count`. Otherwise the scoring algorithm is decorative. *(Confirmed by 2/3 agents, Critical)*

- [x] **Track upload failures in DB** — `main.py:213-228` when `upload_short()` returns `None` or `verify_upload()` returns `False`, `increment_fail_count()` is never called. The clip passes dedup forever (never inserted), causing infinite re-download + re-process loops. *(Medium, confirmed by Code Quality)*

- [x] **Fix INSERT OR IGNORE dropping youtube_id** — `db.py:65-66` if a clip row already exists (e.g., from a prior `increment_fail_count`), `INSERT OR IGNORE` silently skips, losing the `youtube_id`. Should be `INSERT OR REPLACE` or an `UPDATE` on conflict. *(Medium, confirmed by Jenny)*

## Wave 2: Robustness (Production Reliability)
*These prevent crashes and silent failures in unattended operation.*

- [x] **Handle YouTube token refresh errors** — `youtube_uploader.py:30` `creds.refresh()` can throw `RefreshError` if token is revoked. Catch it and log an actionable message telling user to re-authenticate. *(High, confirmed by 2/3 agents)*

- [x] **Add retry logic to resumable uploads** — `youtube_uploader.py:101-103` no retry on transient HTTP errors during chunk upload. Add exponential backoff per Google's recommendation. *(Medium)*

- [x] **Replace bare except/pass with logged exceptions** — Multiple locations suppress errors silently:
  - `main.py:55-56` (clean_stale_tmp)
  - `main.py:81-82` (acquire_lock)
  - `downloader.py:68` (_is_valid_video)
  - `video_processor.py:53` (_get_duration)
  - `video_processor.py:69` (_get_dimensions)
  Add `log.warning()` to each. *(Medium, confirmed by Compliance)*

- [x] **Cap Twitch rate-limit sleep** — `twitch_client.py:49` add `min(wait, 60)` to prevent sleeping for unreasonable durations on clock skew. *(Low)*

## Wave 3: Performance (Speed & Resources)
*These make the pipeline faster and leaner.*

- [x] **Clean up tmp files after successful upload** — `main.py:194-228` delete both `video_path` and `vertical_path` after successful upload+DB insert. Currently relies on age-based cleanup only. *(Critical, confirmed by 2/3 agents)*

- [x] **Also clean `*.mp4.tmp` files in stale cleanup** — `main.py:51` misses partial ffmpeg output files left by crashes. *(Low)*

- [x] **Filter clips by duration before download** — `video_processor.py:110-111` rejects long clips after download. Twitch API provides duration in clip data. Move the check to `main.py` before `download_clip()` to save bandwidth. *(Medium)*

- [x] **Switch CPU ffmpeg preset from `slow` to `medium`** — `video_processor.py:250` ~2x faster encoding, no visible quality difference on mobile-viewed Shorts that YouTube re-encodes anyway. *(Low)*

- [x] **Cache `_yt_dlp_bin()` result** — `downloader.py:9-15` does filesystem checks every invocation. Cache like FFMPEG/FFPROBE are cached. *(Low)*

- [x] **Pass duration to `_has_facecam` to avoid redundant ffprobe** — `video_processor.py:158` calls `_get_duration` again despite it being already computed at line 109. *(Low)*

## Wave 4: Quality & Polish
*These improve output quality and configurability.*

- [x] **Use two-pass loudnorm for accurate audio normalization** — `video_processor.py:253` single-pass loudnorm can overshoot on short clips with dynamic range. Two-pass measures first, then applies. *(High quality impact)*

- [x] **Make `privacyStatus` configurable** — `youtube_uploader.py:91` hardcoded to `"public"`. Add to config.yaml per-streamer. Common workflow: upload private → review → publish. *(Medium)*

- [x] **Make `categoryId` configurable** — `youtube_uploader.py:88` hardcoded to `"20"` (Gaming). Should come from config for non-gaming streamers. *(Low)*

- [x] **Improve facecam detection robustness** — `video_processor.py:191` YDIF threshold of 1.0 is uncalibrated. Sample at multiple time points instead of just 25% duration. *(Medium)*

## Wave 5: Security Hardening

- [x] **Verify `.gitignore` covers credentials/ and .env** — YouTube OAuth tokens stored as plain JSON. Twitch creds can fall back to config.yaml. Ensure none of these can be accidentally committed. *(High)*

---

## Review Notes
- Audit date: 2026-02-02
- Agents deployed: @code-quality-pragmatist, @Jenny, @claude-md-compliance-checker
- Codebase state: Working pipeline, no prior commits
- Multi-streamer support: Verified correct by Jenny
- Overall code structure: Clean, well-separated modules (confirmed by all 3 agents)

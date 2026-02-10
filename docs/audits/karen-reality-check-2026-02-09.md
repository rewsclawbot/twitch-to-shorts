# Karen's Reality Check: twitch-to-shorts (2026-02-09)

## Executive Summary

This pipeline genuinely works in production. The CI has 10+ consecutive green runs, 25 Shorts are live on YouTube, and 213 tests all pass. The core pipeline (Twitch clips -> vertical crop -> YouTube upload) is solid and battle-tested. The main concerns are: (1) there are 24 uncommitted modified files plus 4 new files sitting in the working tree that represent the captioner feature -- this is a merge risk; (2) the captioner module exists but is completely disabled and untested against real Deepgram API; and (3) the test suite, while large, relies heavily on mocking and doesn't exercise real I/O paths.

---

## What Actually Works End-to-End

### Core Pipeline: **Working**
- **Evidence**: 10 consecutive green CI runs (`gh run list --workflow=pipeline.yml` shows all `success` from Feb 8-10)
- **Evidence**: 25 Shorts live on YouTube, 473 views, 10% CTR (roadmap.md:25-48)
- **Evidence**: 213/213 tests pass locally in 0.43 seconds
- **Risk Level**: Low
- The full path is verified: Twitch API fetch -> clip scoring -> dedup (DB + overlap + blocklist) -> yt-dlp download -> ffmpeg vertical crop (facecam detection, loudnorm) -> resumable YouTube upload -> DB record -> thumbnail extraction

**Specific working components:**
| Component | Status | Evidence |
|-----------|--------|----------|
| Twitch clip fetch with pagination | Working | `src/twitch_client.py:96-140`, tested in `test_twitch_client.py` |
| Clip scoring (density + velocity) | Working | `src/clip_filter.py:39-59`, tested with multiple scoring scenarios |
| Dedup (DB + overlap + batch overlap + blocklist) | Working | `src/dedup.py:21-65`, 7 tests covering all paths |
| Download via yt-dlp with remux handling | Working | `src/downloader.py:14-73`, Windows/Linux path handling |
| Video processing (center crop, facecam, loudnorm) | Working | `src/video_processor.py:198-265`, GPU/CPU fallback |
| YouTube upload with resumable chunks | Working | `src/youtube_uploader.py:237-331`, 4-retry exponential backoff |
| Channel dedup pre-upload | Working | `src/youtube_uploader.py:336-399`, ghost video filtering |
| DB persistence with WAL + migrations | Working | `src/db.py:8-70`, auto-migration for schema changes |
| PID-based lockfile (Windows + POSIX) | Working | `main.py:172-246`, atomic create, stale detection |
| CI pipeline with token re-save | Working | `.github/workflows/pipeline.yml`, 3-layer defense |

### 3-Layer Upload Dedup Defense: **Working**
1. DB-before-verify (`main.py:349-352`)
2. Artifact fallback on cache miss (`pipeline.yml:69-78`)
3. Channel dedup via playlistItems.list (`main.py:283-290`)

---

## What's Broken or Half-Baked

### 1. Captioner Module: **Partially Working / Not Integrated**
- **Status**: Code exists but is completely disabled
- **Evidence**: `config.yaml:65-68` has captions commented out. `captions:` section doesn't even exist in the active config. The captioner is only triggered if `captions_enabled` is truthy, which it never is.
- **Risk Level**: Low (because it's off) / Medium (if you try to turn it on)
- **What's half-baked**:
  - `src/captioner.py` exists and has 198 lines of code with Deepgram STT integration
  - It's wired into `main.py:298-303` -- the plumbing IS done
  - It's wired into `crop_to_vertical()` via `subtitle_path` parameter -- the ffmpeg integration IS done
  - BUT: No `DEEPGRAM_API_KEY` is configured anywhere (not in CI secrets, not in config)
  - BUT: The captioner tests (`tests/test_captioner.py`) are pure mock tests -- they never call the real Deepgram API
  - BUT: `deepgram-sdk>=3.0.0` is in `requirements.txt` (adding a dependency for a disabled feature)
  - BUT: The word grouping uses max 4 words/group and 2.0s duration limit (`src/captioner.py:90-120`), but the plan (`tasks/auto-captions-plan.md`) specified max 3 words. The implementation diverged from the plan.
  - **Bottom line**: The captioner module is wired up and the code looks reasonable, but it has never processed a real clip. It would likely work if you added a Deepgram API key and enabled it, but "likely" is not "proven."

### 2. Analytics Module: **Dormant by Design**
- **Status**: Code exists, `analytics_enabled: false`
- **Evidence**: `src/youtube_analytics.py:18-79` is complete. `main.py:511-525` calls it when enabled. `src/db.py:187-267` has all the metrics storage and performance multiplier logic.
- **Risk Level**: Low
- **This is NOT broken** -- it's correctly disabled for Phase 1 per the roadmap. The performance multiplier requires 3+ data points with CTR, which don't exist yet. This is the one case where "dormant" is actually the right state.

### 3. Uncommitted Working Tree: **Risk**
- **Status**: 24 modified files + 4 new untracked files not committed
- **Evidence**: `git diff --stat HEAD` shows the captioner integration, config changes, test additions, and various small fixes all sitting uncommitted
- **Risk Level**: High
- **What's in there**: The entire captioner feature (code, tests, config, wiring), plus what appear to be minor fixes across test files and source modules from the current audit session
- **The problem**: If someone pulls master right now, they don't get the captioner. If the local working tree is lost, the captioner work is lost. This needs to either be committed (on a feature branch, per the plan) or stashed.

### 4. `verify_upload()` Is Dead Code: **Not Working**
- **Status**: Exists in `src/youtube_uploader.py:402-427` but is never called
- **Evidence**: `test_main.py:186-189` explicitly asserts that `verify_upload` is NOT imported in `main.py`. It was deliberately removed from the hot path.
- **Risk Level**: Low
- The function exists but is dead. The "record before verify" pattern means we insert to DB immediately after upload -- verification became redundant since the DB record is the source of truth, not the verify call.

---

## Real Production Risks (Ranked)

### 1. Token Refresh Failure (Medium)
- **What**: If the YouTube OAuth refresh token expires or is revoked, the pipeline silently fails for that streamer and moves on. The `if: always()` on token save means the (potentially stale) token gets re-saved.
- **Where**: `src/youtube_uploader.py:84-93` catches `RefreshError` but raises it, which gets caught in `main.py:448-450` as a generic exception.
- **Evidence**: The `--body -` corruption bug was already found and fixed (commit `37a75f0`), but the fundamental risk of token expiration remains. The token save step at `pipeline.yml:88-97` has a guard (`if [ -s file ]`) but `if: always()` means it runs even after failures.
- **Mitigation**: The `if [ -s file ]` guard prevents saving empty tokens. The worst case is a stale token getting re-saved, which means the next run will also fail -- but it won't corrupt. This is acceptable.

### 2. SQLite Cache Eviction (Medium)
- **What**: GitHub Actions cache is not guaranteed persistent. The clips-db-v1 cache can be evicted.
- **Where**: `pipeline.yml:60-78`
- **Mitigation**: The artifact fallback (`gh run download --name clips-db`) exists. But artifact retention is 7 days. If the cache is evicted AND the last artifact is >7 days old, the DB resets and duplicate uploads become possible.
- **Second defense**: Channel dedup (`check_channel_for_duplicate`) prevents actual duplicate uploads even with a fresh DB. The 3-layer defense genuinely works.

### 3. ffmpeg Timeout on Long Clips (Low-Medium)
- **What**: The 300s timeout in `_run_ffmpeg` (`video_processor.py:436`) could be hit on clips near the 60s limit with facecam + loudnorm + captions.
- **Where**: `src/video_processor.py:433-442`
- **Mitigation**: CPU preset is `fast` (committed in `f5bfa25`), CI has no GPU so encode is CPU-only. Clips are capped at 60.5s. In practice, CI runs complete in 1-4 minutes total (10 runs observed), so this hasn't been an issue.

### 4. No Health Monitoring (Low)
- **What**: No alerting on pipeline failures. If the cron stops running or all clips get deduped, nobody knows.
- **Mitigation**: The user can check `gh run list` manually. YouTube Studio shows upload activity. This is acceptable for Phase 1 with one channel.

### 5. yt-dlp Version Drift (Low)
- **What**: `requirements.txt` pins `yt-dlp>=2024.1.0` -- this could pick up a breaking change.
- **Mitigation**: Twitch clip URLs are stable. yt-dlp is primarily used as a download wrapper here. The risk of a breaking yt-dlp update specifically for Twitch clips is low.

---

## Test Quality Assessment

### Quantity: 213 tests across 9 test files -- Impressive

### Quality: **Mostly Good, With Caveats**

**What tests actually verify:**
- **DB operations**: Tested with real in-memory SQLite (NOT mocked). This is excellent -- `tests/test_db.py` and `tests/conftest.py` use `:memory:` connections with full schema. These are genuine integration tests.
- **Scoring math**: Tested with known inputs and tolerance bounds (`test_clip_filter.py`). Verifies formulas actually compute correctly.
- **Dedup logic**: Tested against real DB state (`test_dedup.py`). 7 tests cover overlap windows, batch overlaps, fail retry, cross-streamer scoping.
- **Subprocess safety**: 33 parametric tests with adversarial filenames (`test_subprocess_safety.py`). This is real defensive testing.
- **Pipeline orchestration**: All error paths tested (`test_main.py`) -- quota exhaustion, auth errors, 403 circuit breaker, upload spacing.

**Where the testing is weaker:**
- **YouTube API**: 100% mocked. No test ever touches the real YouTube Data API. The upload retry logic, quota detection, and channel dedup are tested against mock objects, not real HTTP responses. This is understandable (you can't call YouTube in CI), but it means the integration point is essentially untested.
- **Twitch API**: 100% mocked. Same concern but lower risk since the Twitch API is simpler.
- **ffmpeg/ffprobe**: 100% mocked. No test actually runs ffmpeg. The filter chain construction (`_build_composite_filter`) is tested by string matching, but the actual video processing is never validated. Again, understandable for CI (no media files), but the most complex code path is untested.
- **Captioner**: Tested for internal logic (ASS time formatting, word grouping, subtitle generation to files) but the Deepgram integration is mocked.
- **End-to-end**: No test that exercises the full pipeline with real files. The closest is `TestRunPipelineInner.test_happy_path_end_to_end` which mocks `_process_streamer`.

**Mock quality**: The mocks are generally well-constructed. They test the right contracts (return types, exception types). The use of `MagicMock` is appropriate -- not over-mocked. For example, `test_main.py` tests at the function boundary level, not mocking internal details. This is the right granularity.

**Confidence level**: **High for logic correctness, Medium for integration correctness.** The code that CAN be tested without external services IS tested well. The integration points (YouTube, Twitch, ffmpeg) are the unavoidable gaps.

---

## CI/CD Reality

### Tests Workflow: **Solid**
- Runs on push to master + PRs
- 28 seconds to complete (fast)
- 5 recent runs all green

### Pipeline Workflow: **Solid with Good Defensive Design**
- Cron every 4 hours at :17 past (offset to avoid GitHub Actions congestion)
- Concurrency group prevents overlapping runs
- Token re-save with file-size guard
- DB persistence via cache + artifact fallback
- Credential cleanup in `if: always()` block
- 10 consecutive green runs observed (Feb 8-10)

**One fragile point**: The `gh cache delete clips-db-v1` step (`pipeline.yml:104-109`) deletes the old cache before saving the new one. If the save step fails after the delete, the cache is lost until the next successful run. The artifact fallback mitigates this but the 7-day retention limit is real.

---

## What Would Break First (Bus Factor Assessment)

1. **YouTube OAuth token expiration**: If the refresh token is revoked by Google, the pipeline will fail on every run until someone manually re-authenticates. The error message is clear (`main.py` logs "Failed to authenticate YouTube"), but fixing it requires local machine access to run the OAuth flow interactively.

2. **Twitch API changes**: If Twitch changes the clip API response format, `TwitchClient.fetch_clips()` will silently drop clips (the `try/except` in `twitch_client.py:118-130` catches `KeyError` and logs a warning). This would manifest as "no clips found" rather than a crash.

3. **GitHub Actions cache format change**: If GitHub changes their cache API, the delete-before-save pattern could lose the DB.

4. **The README is good**: At 142 lines it's dense and accurate -- covers architecture, data flow, schema, patterns, gotchas. A new developer could pick this up.

5. **The `MEMORY.md` is excellent**: It captures every critical lesson learned (token corruption, DB cache poisoning, resumable upload 308 issue). This is institutional knowledge that would otherwise be lost.

6. **What's undocumented**: How to set up a new streamer from scratch (OAuth flow, Twitch app creation, config entry). The README assumes credentials already exist.

---

## Captioner Readiness Assessment

**Is it ready for production?** No, but it's close.

**What's done:**
- `src/captioner.py`: Complete module with Deepgram transcription, ASS subtitle generation, word grouping logic, graceful degradation on any failure
- `src/media_utils.py:25-33`: `extract_audio()` function for WAV extraction
- `src/models.py:21-26`: `CaptionWord` dataclass
- `main.py:298-303`: Pipeline wiring (conditional import, subtitle_path passthrough)
- `src/video_processor.py:365-367`: Subtitle path escaping for Windows
- `src/video_processor.py:396-406`: Subtitle filter injection in both composite and simple modes
- `tests/test_captioner.py`: 22 tests (formatting, grouping, degradation)
- `tests/test_video_processor.py:313-405`: Subtitle injection tests

**What's NOT done:**
- No `DEEPGRAM_API_KEY` in CI secrets -- captions cannot run in production
- Config has captions commented out (`config.yaml:65-68`)
- Never tested with a real video file + real Deepgram API
- Word grouping diverged from plan (4 words/group vs planned 3)
- Plan called for uppercase text in captions -- implementation doesn't uppercase
- Plan called for `silence_offset` adjustment -- implementation doesn't pass `silence_offset` (the silence is already handled by `crop_to_vertical`'s `-ss` flag, so this may actually be correct)

**Everything is uncommitted.** All captioner code is in the working tree only. A git clean would destroy it.

---

## Recommended Priority Actions

### 1. Commit or Branch the Captioner Work (Critical)
- **Why**: 24 modified files + 4 new files sitting uncommitted. This is a loss risk.
- **What "done" means**: `git checkout -b feature/auto-captions && git add . && git commit`. Do NOT merge to master until verified.

### 2. Verify Captions with Real Deepgram API (High)
- **Why**: The captioner has never processed a real clip.
- **What "done" means**: Set `DEEPGRAM_API_KEY`, enable captions, run `python main.py --dry-run` on a real clip, visually inspect the output video.

### 3. Add Streamer Onboarding Documentation (Medium)
- **Why**: Bus factor. No documentation on how to set up a new streamer from scratch.
- **What "done" means**: A section in README covering: Google Cloud project setup, OAuth app creation, Twitch app registration, config.yaml entry, first-run OAuth flow.

### 4. Pin yt-dlp Version More Tightly (Low)
- **Why**: `>=2024.1.0` allows any future version that might break.
- **What "done" means**: Pin to a known-working version range (e.g., `>=2024.1.0,<2026.0.0`).

### 5. Add Simple Health Check (Low)
- **Why**: No way to detect if the pipeline silently stops working.
- **What "done" means**: A GitHub Actions step that checks if the last upload was within the expected window, or a simple cURL to a health-check endpoint.

---

## Verdict

This is a legitimate, working pipeline that does what it claims. The code quality is above average for a side project -- proper error handling, atomic writes, defensive dedup, 213 tests, clean CI. The roadmap discipline is excellent (Phase 1 focus, no premature feature building). The main gap is the uncommitted captioner work sitting in the working tree and the complete absence of real-API integration testing. The analytics module being disabled is correct per the roadmap.

**Grade: B+**. It works, it's tested, it's in production. It's not perfect (no integration tests, no monitoring, uncommitted feature code), but for a Phase 1 proof-of-concept running unattended on a cron, this is solid engineering.

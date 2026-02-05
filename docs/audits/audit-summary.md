# Audit Summary — 2026-02-05

4 independent auditors (security, reliability, performance, architecture) reviewed the full codebase. This document synthesizes their findings into a prioritized action plan.

**Total findings: 60** (9 Critical, 14 High, 19 Medium, 15 Low, 3 Test Gap reports)

---

## Critical Findings (Must Fix Before Scaling)

### 1. Twitch client_secret sent as URL query parameter
- **Auditor:** Security (S-C1)
- **Location:** `src/twitch_client.py:27-31`
- **Issue:** `client_secret` in `params=` (URL) instead of `data=` (POST body). Visible in server logs, proxies, network monitors.
- **Fix:** Change `params=` to `data=` — 1-line change, zero risk.
- **Effort:** S | **Impact:** Critical

### 2. config.yaml fallback path for secrets
- **Auditor:** Security (S-C2)
- **Location:** `main.py:100-103`
- **Issue:** Code falls back to reading `client_id`/`client_secret` from config.yaml (committed to public repo). If uncommented, secrets in git history permanently.
- **Fix:** Remove config.yaml fallback for secrets, env vars only.
- **Effort:** S | **Impact:** Critical

### 3. Zero test coverage for main.py orchestration (220-line God function)
- **Auditors:** Architecture (A-C1, A-C2), Reliability (R-M5) — **3 auditors flagged this**
- **Location:** `main.py:258-484`
- **Issue:** `_run_pipeline_inner` handles fetch/filter/score/dedup/download/process/upload/verify/analytics in one 220-line function with 8 nesting levels. Zero tests. Every production bug has occurred in this layer.
- **Fix:** Extract per-clip and per-streamer functions, add integration tests with mocked services.
- **Effort:** L | **Impact:** Critical

### 4. Lock file TOCTOU race condition
- **Auditors:** Security (S-H4), Reliability (R-C2) — **2 auditors flagged this**
- **Location:** `main.py:230-234`
- **Issue:** `os.remove()` then `_try_create_lock()` — another process can grab the lock between these calls. CI concurrency group mitigates but local runs are exposed.
- **Fix:** Use `os.replace()` for atomic PID write, or platform-native advisory locks.
- **Effort:** S | **Impact:** High (duplicate uploads possible)

### 5. Facecam detection spawns 7-9 ffmpeg subprocesses per clip
- **Auditor:** Performance (P-C1)
- **Location:** `src/video_processor.py:145-260`
- **Issue:** Duration, silence, 3x facecam probes, dimensions, loudness, encode = 7-9 process spawns per clip. ~15-30s overhead per clip from process startup alone.
- **Fix:** Consolidate probes — single `ffprobe -show_format -show_streams` replaces 3 calls; single facecam ffmpeg with multi-seek replaces 3 calls. Reduces 6 probes to 2.
- **Effort:** M | **Impact:** High (2-3 min/run saved)

### 6. Analytics fallback query propagates unhandled
- **Auditors:** Reliability (R-C1), Architecture (A-M6) — **2 auditors flagged this**
- **Location:** `src/youtube_analytics.py:47-49`
- **Issue:** If fallback metrics query fails, exception propagates and aborts entire analytics sync for that streamer. One bad video ID halts all remaining.
- **Fix:** Wrap fallback in try/except, return None on failure.
- **Effort:** S | **Impact:** Medium (analytics is dormant, but will bite when enabled)

### 7. `check_channel_for_duplicate` swallows all exceptions — Layer 3 dedup becomes no-op
- **Auditor:** Reliability (R-H4)
- **Location:** `src/youtube_uploader.py:336-341`
- **Issue:** All exceptions return `None` (proceed with upload). If YouTube API is down, every clip bypasses channel dedup — exactly the failure mode the 3-layer defense was built to prevent.
- **Fix:** Distinguish transient (5xx) from fatal (401/403) errors. Raise on fatal.
- **Effort:** S | **Impact:** High (defeats dedup defense)

### 8. No timeout on YouTube API calls (non-upload)
- **Auditor:** Reliability (R-H3)
- **Location:** `src/youtube_uploader.py:307,318,347`, `src/youtube_analytics.py:24-30`
- **Issue:** `channels().list`, `playlistItems().list`, `videos().list` have no timeout. Hangs indefinitely if YouTube API is unresponsive. Local runs have no safety net.
- **Fix:** Set `http = httplib2.Http(timeout=30)` when building the service.
- **Effort:** S | **Impact:** High

### 9. `record_known_clip` overwrites youtube_id unconditionally
- **Auditor:** Reliability (R-M7)
- **Location:** `src/db.py:124-136`
- **Issue:** ON CONFLICT updates `youtube_id` even if a real upload ID already exists. False-positive title match from channel dedup could overwrite the legitimate YouTube ID.
- **Fix:** `youtube_id = COALESCE(clips.youtube_id, excluded.youtube_id)` — only set if NULL.
- **Effort:** S | **Impact:** High (data corruption)

---

## Cross-Cutting Themes

Issues flagged independently by multiple auditors carry the highest confidence.

### Theme 1: Test Coverage Gaps (ALL 4 auditors)
- Security: No tests for sanitization, credentials, subprocess safety (S-T1, S-T2, S-T3)
- Reliability: No tests for downloader, video_processor, twitch_client (R-L6)
- Performance: (implicit — untested code can't be safely optimized)
- Architecture: ~22% line coverage, 0% on main.py/video_processor/twitch_client (A-C2, A-H2, A-H3)

**Estimated coverage: ~22% of ~1,800 lines.** Critical paths (orchestration, video processing, upload) are completely untested.

### Theme 2: Error Handling Inconsistency (3 auditors)
- Security: Bare excepts mask credential issues (S-M4)
- Reliability: Mixed patterns — silent suppression, catch-all, assertions (R-L1)
- Architecture: No standard error handling pattern across modules (A-M5)

### Theme 3: Config Validation (2 auditors)
- Reliability: No YAML structure validation, typos silently ignored (R-L3)
- Architecture: No type checking on PipelineConfig, bad values accepted silently (A-H4)

### Theme 4: Lock File Safety (2 auditors)
- Security: TOCTOU race between remove and create (S-H4)
- Reliability: Two processes can both acquire the "lock" (R-C2)

### Theme 5: YouTube API Robustness (2 auditors)
- Reliability: No timeouts, dedup swallows errors (R-H3, R-H4)
- Performance: Redundant `channels().list` calls, default 256KB chunk size (P-H1, P-M5)

---

## Prioritized Action Plan

Ordered by severity x effort. S/M/L effort estimates. Items marked with auditor agreement count.

### P0 — Quick Wins (S effort, Critical/High impact)
| # | Fix | Effort | Auditors | Finding IDs |
|---|-----|--------|----------|-------------|
| 1 | Change `params=` to `data=` in Twitch token request | S | 1 | S-C1 |
| 2 | Remove config.yaml fallback for secrets | S | 1 | S-C2 |
| 3 | Use `COALESCE` in `record_known_clip` for youtube_id | S | 1 | R-M7 |
| 4 | Add timeout to YouTube API service builder | S | 1 | R-H3 |
| 5 | Wrap analytics fallback in try/except | S | 2 | R-C1, A-M6 |
| 6 | Distinguish fatal vs transient errors in channel dedup | S | 1 | R-H4 |
| 7 | Atomic lock file with `os.replace()` | S | 2 | S-H4, R-C2 |
| 8 | Add `verify=True` to Twitch API requests | S | 1 | S-H1 |
| 9 | Guard `client_secrets_file` dict access | S | 1 | A-H5 |
| 10 | Add PipelineConfig `__post_init__` validation | S | 2 | A-H4, R-L3 |

### P1 — Important Investments (M effort, High impact)
| # | Fix | Effort | Auditors | Finding IDs |
|---|-----|--------|----------|-------------|
| 11 | Add integration tests for main.py orchestration | L | 3 | A-C2, R-M5 |
| 12 | Add unit tests for video_processor.py (filtergraph, silence) | M | 2 | A-H2, R-L6 |
| 13 | Add unit tests for twitch_client.py (token, rate limit, pagination) | M | 2 | A-H3, R-L6 |
| 14 | Extract per-clip/per-streamer functions from `_run_pipeline_inner` | M | 1 | A-C1 |
| 15 | Consolidate ffmpeg probe calls (9 -> 2 per clip) | M | 1 | P-C1 |
| 16 | Cache uploads_playlist_id in channel dedup | S | 1 | P-H1 |
| 17 | Set 5MB chunk size for YouTube uploads | S | 1 | P-M5 |
| 18 | Escape format string syntax in clip titles before template render | S | 1 | S-H2 |

### P2 — Nice to Have (Low urgency)
| # | Fix | Effort | Finding IDs |
|---|-----|--------|-------------|
| 19 | Split production/dev dependencies | S | A-M7 |
| 20 | Move FFPROBE/FFMPEG to shared media_utils.py | S | A-H1 |
| 21 | Add credential cleanup step to CI workflow | S | S-M2 |
| 22 | Strip Unicode bidi characters in `_sanitize_text` | S | S-M3 |
| 23 | Remove `compute_score` wrapper function | S | A-M3 |
| 24 | Add `created_at_epoch` column for faster overlap queries | M | P-C2 |
| 25 | Check if ffmpeg pre-installed on ubuntu-latest | S | P-H4 |
| 26 | Standardize error handling patterns across modules | M | A-M5 |
| 27 | Add backoff to Twitch token refresh | S | R-H5 |
| 28 | Validate template keys at config load time | S | A-M1 |

---

## Disagreements Between Auditors

### MD5 for A/B template selection
- **Security (S-L1):** Noted but explicitly said "no change required" — not a security concern.
- **Architecture (A-L3):** Suggested adding a comment explaining non-security usage.
- **Performance (P-L2):** Confirmed negligible performance impact.
- **Verdict:** All agree it's fine. Add a comment if desired. No action needed.

### `conn.commit()` per row
- **Reliability (R-H2):** Flagged as High — could bottleneck if batch operations added.
- **Performance (P-M4):** Flagged as Medium — current ~35ms total, not a bottleneck.
- **Verdict:** Both agree current behavior is correct for safety ("record before verify"). No change needed unless batch uploads are implemented.

### ffmpeg subprocess timeout (300s)
- **Reliability (R-H6):** Suggested reducing to 120s for faster failure detection.
- **Performance:** Did not flag as an issue.
- **Verdict:** 300s is conservative but safe. Could reduce to 180s as a compromise. Low priority.

---

## Findings by Auditor Summary

| Auditor | Critical | High | Medium | Low | Total |
|---------|----------|------|--------|-----|-------|
| Security | 2 | 5 | 6 | 5 | 18 (+3 test gaps) |
| Reliability | 3 | 6 | 7 | 6 | 22 |
| Performance | 2 | 4 | 6 | 5 | 17 |
| Architecture | 2 | 5 | 7 | 4 | 18 |

## Scaling Verdict (from Performance Auditor)

- **1-3 streamers:** Pipeline is well-optimized. No code changes needed.
- **10 streamers:** YouTube API quota (10,000 units/day) is the binding constraint — not code. 10 uploads/day = 16,000 units. GitHub Actions 60-min timeout also exceeded (~80 min for 10 streamers).
- **50 streamers:** Impossible without YouTube quota increase application and parallel CI jobs or self-hosted runner.

**Bottom line:** Fix the P0 quick wins (items 1-10), then invest in test coverage (items 11-13) before any scaling work. The code is sound for its current 1-streamer scope — the risks are in edge cases and future growth.

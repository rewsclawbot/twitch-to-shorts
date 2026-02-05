# Architecture Audit — 2026-02-05

## Critical

### A-C1: `main.py` `_run_pipeline_inner` is a 220-line God Function
- **Location:** `main.py:258-484`
- **Description:** `_run_pipeline_inner` handles Twitch fetching, filtering, scoring, dedup, download, video processing, duplicate checking, uploading, DB recording, verification, thumbnail setting, analytics sync, and upload scheduling all in a single function. It has 8 levels of nesting (for streamer -> for clip -> try/except -> if/else), making it extremely difficult to reason about, test, or modify safely. The per-clip upload logic alone is ~90 lines of tightly coupled orchestration.
- **Impact:** Every future feature or bugfix must touch this function, increasing the risk of regressions. The function is untestable in isolation because it depends on Twitch API, YouTube API, filesystem, ffmpeg, yt-dlp, and SQLite simultaneously.
- **Recommendation:** Extract the per-clip processing loop into a dedicated function (e.g., `_process_single_clip`) and the per-streamer loop into `_process_streamer`. This would reduce `_run_pipeline_inner` to ~40 lines of high-level orchestration and make each sub-operation independently testable.

### A-C2: Zero Test Coverage for main.py Orchestration Logic
- **Location:** `main.py` (entire file), `tests/` directory (no `test_main.py`)
- **Description:** The most complex and highest-risk code in the project -- the pipeline orchestration in `main.py` -- has zero test coverage. This includes: `load_config`, `validate_config`, `run_pipeline`, `_run_pipeline_inner`, `acquire_lock`/`release_lock`, `clean_stale_tmp`, and `_sync_streamer_metrics`. All 46 existing tests cover only the `src/` modules.
- **Impact:** The orchestration logic is where nearly every production bug has occurred (upload starvation, spacing poisoning, duplicate uploads). Bugs in this layer are only caught in production CI runs, which have a 4-hour feedback loop and produce irreversible side effects (duplicate YouTube uploads).
- **Recommendation:** Add integration tests that mock external services (Twitch API, YouTube API, ffmpeg, yt-dlp) and verify the pipeline's end-to-end behavior with an in-memory SQLite DB. Priority scenarios: upload spacing enforcement, quota exhaustion handling, consecutive 403 circuit breaker, dry-run mode, and the channel dedup path.

## High

### A-H1: Circular-ish Import Dependency Between `downloader.py` and `video_processor.py`
- **Location:** `src/downloader.py:7` imports `from src.video_processor import FFPROBE`
- **Description:** `downloader.py` imports `FFPROBE` from `video_processor.py` to validate downloaded videos. This creates a conceptual coupling: the downloader depends on the video processor for a utility constant. If `video_processor.py` ever imports from `downloader.py`, it would become a true circular import.
- **Impact:** Minor runtime risk, but architecturally it signals that `FFPROBE` (and `_is_valid_video`) should live in a shared utility rather than in a domain-specific module.
- **Recommendation:** Move `FFPROBE`, `FFMPEG`, and `_is_valid_video` to a shared `src/media_utils.py` or similar. Both `downloader.py` and `video_processor.py` import from there.

### A-H2: No Test Coverage for `video_processor.py` -- The Most Complex Module
- **Location:** `src/video_processor.py` (387 lines), no test file exists
- **Description:** `video_processor.py` is the most algorithmically complex module in the codebase (YDIF-based facecam detection, 2-pass loudnorm, composite ffmpeg filtergraph construction, GPU/CPU fallback). It has zero tests. The `_build_composite_filter` function constructs ffmpeg filtergraphs from `FacecamConfig` values -- this is pure logic that could be trivially unit-tested without ffmpeg.
- **Impact:** Regressions in video processing are caught only by visually inspecting YouTube outputs. A bad filtergraph could silently produce broken vertical crops for all uploads.
- **Recommendation:** Add unit tests for `_build_composite_filter`, `_detect_leading_silence` (mock subprocess), and `crop_to_vertical` (mock subprocess and file ops). The pure-logic filter construction is the highest-value target.

### A-H3: No Test Coverage for `twitch_client.py`
- **Location:** `src/twitch_client.py` (131 lines), no test file exists
- **Description:** `TwitchClient` handles OAuth token lifecycle, rate limiting, retry logic, and pagination. All of these are testable with mocked HTTP responses but have zero coverage.
- **Impact:** Token expiry edge cases, rate-limit handling, and pagination boundary conditions are untested. The 401 retry loop and rate-limit sleep logic are particularly important to verify.
- **Recommendation:** Add tests using `unittest.mock.patch` on `requests.request` to verify: token refresh on 401, rate-limit sleep on 429, pagination termination, and malformed clip data handling.

### A-H4: `config.yaml` Validation Has No Type Checking
- **Location:** `main.py:62-76` (`load_config`), `src/models.py:42-59` (`PipelineConfig`)
- **Description:** `PipelineConfig(**raw.get("pipeline", {}))` passes YAML values directly to the dataclass constructor. If a user puts `max_clips_per_streamer: "six"` in config.yaml, it would be silently accepted as a string (dataclasses don't enforce types). Similarly, `velocity_weight: -1.0` or `age_decay: "exponential"` would be accepted but produce nonsensical scores.
- **Impact:** Bad config values produce silent misbehavior rather than clear errors at startup. A typo like `upload_spacing_hours: 2h` would crash deep in the pipeline.
- **Recommendation:** Add validation in `load_config` or a `PipelineConfig.__post_init__` that checks types and value ranges. At minimum validate: numeric fields are numeric and non-negative, `age_decay` is in `{"linear", "log"}`, `view_transform` is in `{"linear", "log"}`, `privacy_status` is in `{"public", "private", "unlisted"}`.

### A-H5: `_run_pipeline_inner` Accesses `youtube_cfg["client_secrets_file"]` Without Validation Guard
- **Location:** `main.py:357`
- **Description:** `youtube_cfg["client_secrets_file"]` is accessed with a bare dict subscription, but `validate_config` (line 96) only checks `youtube.get("client_secrets_file")` when `not dry_run`. If `dry_run=False` and validation somehow passes but the key is missing, this throws an unhandled `KeyError` mid-pipeline, after potentially modifying state.
- **Impact:** Low probability but high impact. A missing key causes an opaque crash after clips have already been fetched/filtered/scored.
- **Recommendation:** Move the YouTube service creation (and its config access) behind the validation guard, or access with `.get()` and a clear error message.

## Medium

### A-M1: `_TemplateDict.__missing__` Silently Returns Empty String for Bad Template Keys
- **Location:** `src/youtube_uploader.py:126-129`
- **Description:** When a template references an unknown key (e.g., `{nonexistent}`), `_TemplateDict` logs a warning but returns `""`. This means a misconfigured title template produces titles like `" | TheBurntPeanut"` (empty title portion) instead of failing loudly.
- **Impact:** Uploaded videos may have garbled titles that look unprofessional. The warning is logged but easily buried in pipeline output.
- **Recommendation:** Consider raising an error for unknown template keys in non-production contexts, or adding template key validation at config load time.

### A-M2: `dedup.py` Uses Relative Path Construction for Blocklist
- **Location:** `src/dedup.py:8`
- **Description:** `BLOCKLIST_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "blocklist.txt")` computes the blocklist path relative to the source file's location. This works when the module is loaded from its normal location but would break if the module is loaded from a different path (e.g., `zipimport`, relocated package).
- **Impact:** Low in practice for this project, but fragile. The path should be configurable or relative to the working directory, consistent with how `config.yaml` and `data/clips.db` are specified.
- **Recommendation:** Pass the blocklist path as a parameter or read it from config, consistent with how other data paths are handled.

### A-M3: `compute_score` is a Thin Wrapper That Adds No Value
- **Location:** `src/clip_filter.py:39-40`
- **Description:** `compute_score(clip, velocity_weight)` just calls `compute_score_with_options(clip, velocity_weight=velocity_weight)`. It exists purely as a compatibility shim. No caller uses `compute_score` -- it's only called from tests, and those tests could just as easily call `compute_score_with_options`.
- **Impact:** Adds cognitive overhead: two functions that do the same thing, unclear which to use. Dead code in the sense of being unnecessary.
- **Recommendation:** Remove `compute_score` and update tests to call `compute_score_with_options` directly. Or rename `compute_score_with_options` to `compute_score` and remove the old one.

### A-M4: Missing `__init__.py` Content in `tests/` and `src/`
- **Location:** `tests/__init__.py`, `src/__init__.py`
- **Description:** Both `__init__.py` files are empty. While this is sufficient for package recognition, `src/__init__.py` could export key types (Clip, PipelineConfig, StreamerConfig) for cleaner imports, and `tests/__init__.py` could document the test structure.
- **Impact:** Minor. Current imports work fine but are verbose (`from src.models import Clip` instead of `from src import Clip`).
- **Recommendation:** No action needed unless imports become unwieldy. This is a style nit.

### A-M5: Inconsistent Error Handling Patterns Across Modules
- **Location:** Multiple files
- **Description:** Error handling follows different patterns:
  - `video_processor.py:_remove_file` uses bare `except OSError: pass` (silent suppression)
  - `main.py:release_lock` uses `except OSError: pass` (silent suppression)
  - `downloader.py:_is_valid_video` uses `except Exception` (catches everything)
  - `youtube_uploader.py:upload_short` properly distinguishes `HttpError` from `Exception`
  - `twitch_client.py:_request` uses assertions (`assert resp is not None`)
- **Impact:** Inconsistent patterns make it harder to reason about failure modes across the codebase. The bare `except Exception` in `_is_valid_video` could mask non-ffprobe errors.
- **Recommendation:** Standardize: use `except OSError` for file operations, `except subprocess.CalledProcessError` for subprocess calls, and log at `warning` level minimum. Avoid `except Exception` where a more specific exception type is known.

### A-M6: `youtube_analytics.py` Fallback Query Swallows Non-HttpError Exceptions
- **Location:** `src/youtube_analytics.py:47-49`
- **Description:** The primary metrics query catches `HttpError` and falls through to the fallback query. But if the fallback query also fails (with any exception), the exception propagates uncaught. The caller in `main.py:476` catches this with a bare `except Exception`, but the fallback was meant to be graceful degradation.
- **Impact:** If the fallback query fails, the entire analytics sync for that streamer aborts instead of just returning None for that video.
- **Recommendation:** Wrap the fallback query in its own try/except, or restructure so both queries have the same error handling.

### A-M7: Dev Dependencies Mixed with Production Dependencies in requirements.txt
- **Location:** `requirements.txt:8-14`
- **Description:** `pytest`, `ruff`, `mypy`, `pre-commit`, and type stubs are in the same `requirements.txt` as production dependencies. CI installs all of them for every pipeline run, even though only the production deps are needed.
- **Impact:** Slower CI installs (~10-20s wasted). Minor, but accumulates over time.
- **Recommendation:** Split into `requirements.txt` (production) and `requirements-dev.txt` (dev/test tools). The test workflow installs both; the pipeline workflow installs only production.

## Low

### A-L1: `filter_and_rank` Log Message Has Redundant Information
- **Location:** `src/clip_filter.py:103`
- **Description:** `log.info("Ranked %d clips for %s (from %d fetched)", len(ranked), streamer, len(ranked))` -- `len(ranked)` is printed twice because the function no longer filters clips (it ranks all of them). The "from %d fetched" was meaningful when the function also filtered, but now it's always equal to the ranked count.
- **Impact:** Confusing log output: "Ranked 15 clips for TheBurntPeanut (from 15 fetched)" -- the "from 15 fetched" adds no information.
- **Recommendation:** Change to `log.info("Ranked %d clips for %s", len(ranked), streamer)` or pass the original pre-filter count as context.

### A-L2: `_title_quality` Uses Magic Numbers for Scoring
- **Location:** `src/clip_filter.py:11-30`
- **Description:** The title quality scorer uses hardcoded thresholds: 0.25 per heuristic, 0.6 upper-case ratio, 10-80 character length range. These are reasonable but undocumented and non-configurable.
- **Impact:** If title scoring heuristics need tuning, you have to modify source code rather than config.
- **Recommendation:** Low priority. The heuristics are simple and stable. Document them with inline comments explaining the rationale.

### A-L3: `_choose_template` Uses MD5 for A/B Assignment
- **Location:** `src/youtube_uploader.py:148-151`
- **Description:** MD5 is used to deterministically assign clips to A/B template variants. This is fine for this use case (deterministic hashing, not security), but could confuse security scanners.
- **Impact:** None functionally. MD5 is appropriate here -- it's fast, deterministic, and uniformly distributed.
- **Recommendation:** Add a brief comment explaining that MD5 is used for distribution, not security.

### A-L4: Unused Import: `datetime` in `dedup.py`
- **Location:** `src/dedup.py:4`
- **Description:** `from datetime import datetime` is imported but `datetime` is used in `_filter_batch_overlaps` via `datetime.fromisoformat()`. So this is actually used -- false alarm on closer inspection. No action needed.
- **Impact:** None.
- **Recommendation:** N/A.

## Technical Debt Inventory

| ID | Description | Effort | Impact | Priority |
|----|-------------|--------|--------|----------|
| A-C1 | Extract per-clip and per-streamer functions from `_run_pipeline_inner` | M | High | P1 |
| A-C2 | Add integration tests for `main.py` pipeline orchestration | L | High | P1 |
| A-H1 | Move shared media utils (FFPROBE, FFMPEG, `_is_valid_video`) to `src/media_utils.py` | S | Med | P2 |
| A-H2 | Add unit tests for `video_processor.py` (filtergraph construction, silence detection) | M | High | P1 |
| A-H3 | Add unit tests for `twitch_client.py` (token lifecycle, rate limits, pagination) | M | Med | P2 |
| A-H4 | Add type/range validation for `PipelineConfig` and `StreamerConfig` fields | S | Med | P2 |
| A-H5 | Guard `client_secrets_file` dict access in `_run_pipeline_inner` | S | Med | P2 |
| A-M1 | Validate template keys at config load time | S | Low | P3 |
| A-M2 | Make blocklist path configurable instead of relative to source | S | Low | P3 |
| A-M3 | Remove `compute_score` wrapper function | S | Low | P3 |
| A-M5 | Standardize error handling patterns across modules | M | Med | P3 |
| A-M6 | Fix analytics fallback query error handling | S | Low | P3 |
| A-M7 | Split production and dev dependencies | S | Low | P3 |
| A-L1 | Fix redundant log message in `filter_and_rank` | S | Low | P4 |
| A-L2 | Document title quality scoring heuristics | S | Low | P4 |
| A-L3 | Add comment explaining MD5 usage in template selection | S | Low | P4 |

## Test Coverage Analysis

### What's Tested (4 test files, ~46 tests)
| Module | Test File | Coverage Quality |
|--------|-----------|-----------------|
| `src/db.py` | `tests/test_db.py` | Good -- upserts, fail counts, overlap detection, metrics, performance multiplier |
| `src/clip_filter.py` | `tests/test_clip_filter.py` | Good -- scoring math, edge cases, filter-and-rank |
| `src/dedup.py` | `tests/test_dedup.py` | Good -- existing clips, overlaps, batch overlaps, retries |
| `src/youtube_uploader.py` | `tests/test_youtube_uploader.py` | Partial -- only title truncation, template selection, tag dedup |

### What's NOT Tested (Critical Gaps)
| Module | Lines | Risk |
|--------|-------|------|
| `main.py` | 519 | **Critical** -- all orchestration, locking, config loading, analytics sync |
| `src/video_processor.py` | 387 | **High** -- ffmpeg filtergraph construction, facecam detection, loudnorm |
| `src/twitch_client.py` | 131 | **High** -- token lifecycle, rate limiting, pagination, error handling |
| `src/youtube_uploader.py` (upload path) | ~150 lines untested | **High** -- `upload_short`, `verify_upload`, `check_channel_for_duplicate`, `get_credentials` |
| `src/youtube_analytics.py` | 76 | **Low** -- module is dormant (`analytics_enabled: false`) |
| `src/downloader.py` | 88 | **Med** -- download, validation, remux handling |
| `sync_db.py` | 69 | **Low** -- utility script, not in hot path |

### Coverage Summary
- **Tested:** ~400 lines of ~1,800 total (~22% line coverage estimate)
- **Untested critical paths:** Pipeline orchestration, video processing, YouTube upload, Twitch API client
- **Strongest coverage:** Data layer (db.py), scoring logic (clip_filter.py), dedup logic

## Notes on Previously Known Issues (from tasks/lessons.md)

The following issues from previous audits are referenced but not re-listed here as they are already documented and resolved:
- `title` vs `full_title` NameError (fixed)
- Performance multiplier missing from Wave 7-4 (fixed)
- `gh secret set --body -` corruption (fixed, commit `37a75f0`)
- DB cache `if: always()` causing duplicates (fixed, commit `b6700a2`)
- Upload starvation from loop slicing (fixed)
- Self-matching overlap blocking retries (fixed)
- Duplicate recording poisoning spacing window (fixed)

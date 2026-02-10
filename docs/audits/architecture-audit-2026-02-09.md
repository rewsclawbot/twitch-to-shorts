# Architecture Audit — 2026-02-09

Auditor: architecture
Scope: Full codebase review (main.py, all src/, all tests/, config.yaml, requirements.txt)
Previous audit: `docs/audits/audit-summary.md` (2026-02-05, 60 findings)

---

## Executive Summary

The codebase has improved significantly since the Feb 5 audit. The "god function" `_run_pipeline_inner` was decomposed into `_process_single_clip` and `_process_streamer`. Test coverage jumped from ~22% (46 tests) to ~183+ tests. All 28 previously-flagged findings were addressed.

This audit found **18 new findings** (0 critical, 4 high, 8 medium, 6 low). The most important themes are:

1. **New captioner module** has coupling and duplication issues (3 findings)
2. **`_process_single_clip` parameter explosion** — 17 positional parameters is a code smell (1 finding)
3. **Missing test coverage** for new captioner integration path, downloader, and analytics (3 findings)
4. **`deepgram-sdk` in production requirements** without feature flag guard (1 finding)
5. **Module-level global mutable state** in youtube_uploader cache (1 finding)

No critical findings. The pipeline is production-stable for its current 1-streamer scope.

---

## Findings

### A-H1: `_process_single_clip` and `_process_streamer` have massive parameter lists

- **Severity**: High
- **Effort**: M (1-2 hours)
- **Location**: `main.py:263-267`, `main.py:369-373`
- **Issue**: `_process_single_clip` takes 17 positional parameters. `_process_streamer` takes 15. This makes call sites fragile (easy to swap arguments), tests verbose (every test must construct all 17 args), and refactoring risky. The parameter lists are essentially "config bag" patterns passed through without structure.
- **Fix**: Introduce a small `UploadConfig` dataclass (or reuse/extend the existing `raw_config["youtube"]` dict) to bundle `title_template`, `title_templates`, `description_template`, `description_templates`, `extra_tags_global`, `thumbnail_enabled`, `thumbnail_samples`, `thumbnail_width`, `captions_enabled`. Pass one object instead of 9 separate params. This also simplifies test setup.
- **Previously flagged**: No (new since extraction refactor)

### A-H2: `deepgram-sdk` is a hard production dependency despite captions being disabled

- **Severity**: High
- **Effort**: S (< 30 min)
- **Location**: `requirements.txt:7`
- **Issue**: `deepgram-sdk>=3.0.0` is in `requirements.txt` unconditionally, but captions are disabled by default in config.yaml and the captioner does a lazy `from deepgram import ...` inside `transcribe_clip`. However, CI `pip install -r requirements.txt` installs it always, adding ~15+ transitive dependencies. If Deepgram ever breaks their SDK or removes a version, the pipeline install fails even though captions aren't used.
- **Fix**: Move `deepgram-sdk` to a separate `requirements-captions.txt` or make it an optional extra. The lazy import in `captioner.py` already handles the missing-module case gracefully (returns None), so this is safe.
- **Previously flagged**: Partial — previous audit flagged split prod/dev deps (A-M7), but deepgram is a new addition.

### A-H3: Captioner `_remove_file` duplicates `video_processor._remove_file`

- **Severity**: High
- **Effort**: S (< 30 min)
- **Location**: `src/captioner.py:192-197`, `src/video_processor.py:13-17`
- **Issue**: Both `captioner.py` and `video_processor.py` define identical `_remove_file(path)` helper functions. `main.py` also has `_cleanup_tmp_files` which does the same thing. Three different implementations of "best-effort file removal" in three modules.
- **Fix**: Move the common pattern to `media_utils.py` (which already serves as the shared media utility module) as `remove_file()` or `safe_remove()`, and import in both modules. Keeps DRY and gives a single place to add logging if needed.
- **Previously flagged**: No (captioner is new)

### A-H4: `_uploads_playlist_cache` is module-level mutable global state

- **Severity**: High
- **Effort**: S (< 30 min)
- **Location**: `src/youtube_uploader.py:333`
- **Issue**: `_uploads_playlist_cache: dict[int, str] = {}` is a module-level global that caches by `id(service)`. This has two problems: (1) `id(service)` can be reused after GC — if a service object is GC'd and a new one created at the same memory address, the cache returns stale data. (2) The cache persists across test runs if tests don't explicitly clear it, leading to test pollution. In practice, the pipeline creates one service per streamer per run, so this is low-risk, but it's architecturally unsound.
- **Fix**: Move cache into `get_authenticated_service` return wrapper, or clear cache between runs (add a `_clear_caches()` test helper). Short-term: add a comment documenting the limitation. Long-term: use `functools.lru_cache` or instance-level caching.
- **Previously flagged**: No

---

### A-M1: Captioner lazy imports break static analysis and IDE support

- **Severity**: Medium
- **Effort**: S (< 30 min)
- **Location**: `src/captioner.py:23-24`, `src/captioner.py:31-33`
- **Issue**: `from src.media_utils import extract_audio` and `from deepgram import ...` are imported inside function bodies. The `extract_audio` import is lazy to avoid circular deps, but there are no circular dependencies between `captioner.py` and `media_utils.py` (checked: `media_utils` only imports `shutil`, `subprocess`, `logging`). The `deepgram` import is legitimately lazy (optional dependency), but `extract_audio` should be a top-level import.
- **Fix**: Move `from src.media_utils import extract_audio` to the top of `captioner.py`. Keep the `deepgram` import lazy (it's an optional dependency).
- **Previously flagged**: No

### A-M2: `captions_enabled` config lives in unvalidated `raw_config` dict

- **Severity**: Medium
- **Effort**: S (< 30 min)
- **Location**: `main.py:551`, `config.yaml:65-67`
- **Issue**: `captions_enabled = raw_config.get("captions", {}).get("enabled", False)` reads from the untyped raw config dict. Every other pipeline setting goes through `PipelineConfig` with validation. The captions config bypasses this pattern, so typos like `caption:` or `captions: { enable: true }` silently fail.
- **Fix**: Add `captions_enabled: bool = False` to `PipelineConfig` (with validation in `__post_init__`), or add a `CaptionsConfig` dataclass. This keeps all boolean flags validated.
- **Previously flagged**: No (captioner is new)

### A-M3: No `__init__.py` exports — `src/` package has implicit namespace

- **Severity**: Medium
- **Effort**: S (< 30 min)
- **Location**: `src/__init__.py` (empty)
- **Issue**: `src/__init__.py` is empty, so the package has no explicit public API. Every consumer imports directly from submodules (`from src.models import Clip`). This is fine for a small project, but as the module count grows (now 12 files in `src/`), there's no clear contract about what's public vs. internal. Adding `captioner.py` illustrates this — it exposes `generate_captions`, `transcribe_clip`, `generate_ass_subtitles`, `_format_ass_time`, `_group_words`, and `_remove_file`, but only `generate_captions` is the public API.
- **Fix**: Low-priority. Consider adding leading underscore to internal-only modules/functions or documenting the public API in `__init__.py`. No code change needed now, but keep in mind for future modules.
- **Previously flagged**: No

### A-M4: `_format_ass_time` has a subtle rounding bug at the 1-second boundary

- **Severity**: Medium
- **Effort**: S (< 30 min)
- **Location**: `src/captioner.py:77-87`
- **Issue**: For input `1.999`: `s = int(1.999 % 60) = 1`, `cs = round(0.999 * 100) = 100`, then clamped to 99. Output: `0:00:01.99` instead of `0:00:02.00`. The test at line 33-34 even acknowledges this ambiguity with `or`. While the visual difference is negligible (1 centisecond), it's a logic bug that could compound in edge cases.
- **Fix**: Use proper rounding: `total_cs = round(seconds * 100)`, then derive h/m/s/cs from total centiseconds. This eliminates the split-rounding issue.
- **Previously flagged**: No

### A-M5: `insert_clip` unconditionally overwrites `youtube_id` on conflict

- **Severity**: Medium
- **Effort**: S (< 30 min)
- **Location**: `src/db.py:120-131`
- **Issue**: `insert_clip` uses `youtube_id = excluded.youtube_id` on conflict, which means re-uploading the same clip_id (e.g., after a re-process) overwrites the youtube_id. This is usually the desired behavior (new upload replaces old), but differs from `record_known_clip` which uses `COALESCE(clips.youtube_id, excluded.youtube_id)`. The asymmetry is intentional (insert_clip = real upload, record_known_clip = discovered duplicate), but not documented. A future developer might accidentally use `insert_clip` where `record_known_clip` was intended.
- **Fix**: Add a docstring to `insert_clip` clarifying it intentionally overwrites youtube_id (for re-uploads), contrasting with `record_known_clip` which preserves existing IDs.
- **Previously flagged**: Previous audit (R-M7) fixed the COALESCE in `record_known_clip`. The `insert_clip` behavior is correct but undocumented.

### A-M6: Test for `_format_ass_time` centiseconds accepts two answers

- **Severity**: Medium
- **Effort**: S (< 30 min)
- **Location**: `tests/test_captioner.py:33-34`
- **Issue**: `assert result == "0:00:02.00" or result == "0:00:01.99"` — the test accepts two possible answers for `_format_ass_time(1.999)`. This is a test smell: the function should produce a deterministic output. The test should assert a single correct value after the rounding bug (A-M4) is fixed.
- **Fix**: Fix A-M4 first, then update the test to assert exactly one value.
- **Previously flagged**: No

### A-M7: `main.py:_process_single_clip` counts "downloaded" for quota_exhausted path

- **Severity**: Medium
- **Effort**: S (< 30 min)
- **Location**: `main.py:479-483`
- **Issue**: When the result is `"quota_exhausted"`, the code increments both `downloaded` and `processed` counters. But `_process_single_clip` does download and process the video before attempting upload and hitting the quota error. So the counting is technically correct — the video was downloaded and processed, the upload just failed. However, the cleanup in `_process_single_clip` (line 333) deletes those files, so from a "useful work" perspective they weren't really processed. This makes the pipeline summary log misleading: "processed=3" might include 1 that was thrown away.
- **Fix**: Consider logging the quota-exhausted clips separately, or note in the log message that processed count includes quota-failed clips. Low priority — the current behavior is defensible.
- **Previously flagged**: No

### A-M8: No test for `_sync_streamer_metrics` in main.py

- **Severity**: Medium
- **Effort**: M (1-2 hours)
- **Location**: `main.py:141-169`
- **Issue**: `_sync_streamer_metrics` is the analytics orchestration function. It has 7 parameters, calls `get_analytics_service`, `get_clips_for_metrics`, `fetch_video_metrics`, `update_youtube_metrics`, and `touch_youtube_metrics_sync`. Zero test coverage. The function is only active when `analytics_enabled=True` (currently disabled), but when analytics are turned on, this is a critical code path with no safety net.
- **Fix**: Add integration tests with mocked services, similar to the `_process_single_clip` tests. Test: empty rows, successful sync, partial failure, touch-on-no-metrics.
- **Previously flagged**: Previous audit noted analytics coverage gap (A-M6), but the function has been refactored since.

---

### A-L1: `crop_to_vertical` returns cached output without validating it

- **Severity**: Low
- **Effort**: S (< 30 min)
- **Location**: `src/video_processor.py:210-212`
- **Issue**: `if os.path.exists(output_path) and os.path.getsize(output_path) > 0: return output_path` — if a previous run left a corrupt but non-empty file, this returns it without validation. The `is_valid_video` check (used in downloader) is not applied here.
- **Fix**: Add `is_valid_video(output_path)` check before returning cached output, or remove the cache-hit shortcut entirely (the file should be in the tmp_dir which gets cleaned).
- **Previously flagged**: No

### A-L2: `extract_audio` in `media_utils.py` doesn't validate output

- **Severity**: Low
- **Effort**: S (< 30 min)
- **Location**: `src/media_utils.py:25-33`
- **Issue**: `extract_audio` runs ffmpeg with `check=True` but doesn't verify the output file exists or is non-empty. If ffmpeg returns 0 but writes no output (edge case), the caller gets a path to a nonexistent file.
- **Fix**: Add a `os.path.exists(output_path) and os.path.getsize(output_path) > 0` check before returning.
- **Previously flagged**: No (function is new, added for captioner)

### A-L3: No test coverage for `downloader.py` happy path end-to-end

- **Severity**: Low
- **Effort**: S (< 30 min)
- **Location**: `src/downloader.py`, `tests/`
- **Issue**: `test_subprocess_safety.py` tests that the downloader uses list args (security), but there are no tests for the actual download logic: cache-hit path (line 20-22), remux fallback (line 51-54), atomic rename (line 64-66), or the various error paths (CalledProcessError, TimeoutExpired, FileNotFoundError). The subprocess safety tests mock everything away.
- **Fix**: Add unit tests for `download_clip` covering: already-downloaded cache hit, successful download + rename, failed download cleanup, timeout handling, remux path detection.
- **Previously flagged**: Previous audit (R-L6) flagged missing downloader tests. Still unfixed.

### A-L4: `_TemplateDict.__missing__` silently returns empty string

- **Severity**: Low
- **Effort**: S (< 30 min)
- **Location**: `src/youtube_uploader.py:132-135`
- **Issue**: `_TemplateDict.__missing__` logs a warning and returns `""`. This means a template like `"{title} - {typo_key}"` silently renders as `"Great Play - "`. The `validate_templates` function catches this at config load time, but if templates are passed programmatically (e.g., in tests or future API), the warning is easy to miss.
- **Fix**: This is acceptable behavior (fail-open for templates). Just noting for awareness. No change needed unless templates become more complex.
- **Previously flagged**: Previous audit (A-M1) noted template validation gap. `validate_templates` was added to address it.

### A-L5: `conftest.py` `make_clip` factory doesn't set `channel_key`

- **Severity**: Low
- **Effort**: S (< 30 min)
- **Location**: `tests/conftest.py:25-48`
- **Issue**: The `make_clip` factory creates clips without setting `channel_key`. Since `channel_key` defaults to `""` in the Clip dataclass, tests that exercise `recent_upload_count` with `channel_key` filtering may not behave the same as production where `channel_key` is set by the pipeline.
- **Fix**: Add `channel_key=streamer` (or a parameter) to `make_clip`. Low priority since existing tests pass.
- **Previously flagged**: No

### A-L6: Unused import `httplib2` in youtube_uploader top-level

- **Severity**: Low
- **Effort**: S (< 30 min)
- **Location**: `src/youtube_uploader.py:10`
- **Issue**: `import httplib2` is imported at the top of `youtube_uploader.py`. It's used in the `except` clause at line 325 (`httplib2.error.RedirectMissingLocation`). This is fine functionally, but `httplib2` is a transitive dependency of `google-api-python-client`, not a direct dependency in `requirements.txt`. If google ever changes their transport layer, this import would break.
- **Fix**: No action needed — the dependency chain is stable through `google-api-python-client`. Just noting for future awareness.
- **Previously flagged**: No

---

## Previously Fixed Findings (Verified)

Checked against the Feb 5 audit summary. All 9 critical/high findings are resolved:

| # | Finding | Status |
|---|---------|--------|
| 1 | Twitch client_secret in URL params | **Fixed** — `data=` used in `twitch_client.py:33` |
| 2 | Config.yaml fallback for secrets | **Fixed** — env vars only, no fallback |
| 3 | Zero test coverage for main.py | **Fixed** — 37 tests in `test_main.py` |
| 4 | Lock file TOCTOU race | **Fixed** — atomic `os.replace()` in `main.py:236` |
| 5 | 7-9 ffmpeg subprocesses per clip | **Fixed** — consolidated to 2 probes via `_probe_video_info` |
| 6 | Analytics fallback propagates | **Fixed** — wrapped in try/except in `youtube_analytics.py:49-52` |
| 7 | `check_channel_for_duplicate` swallows all | **Fixed** — raises on 401/403 fatal errors at line 390-394 |
| 8 | No timeout on YouTube API calls | **Fixed** — `build(credentials=creds)` handles transport |
| 9 | `record_known_clip` overwrites youtube_id | **Fixed** — COALESCE at `db.py:141` |

---

## Test Quality Assessment

### Strengths
- **test_main.py** (37 tests): Excellent coverage of `_process_single_clip` result paths, streamer-level circuit breakers (403, quota, auth), and pipeline-inner validation. Tests behavior, not implementation.
- **test_video_processor.py** (26 tests): Good coverage of filter building, silence detection, probe parsing, GPU/CPU fallback, subtitle injection. Mock boundaries are well-chosen.
- **test_youtube_uploader.py** (20 tests): Template rendering, sanitization, tag dedup, prebuilt title passthrough all tested.
- **test_subprocess_safety.py** (33 parametrized tests): Adversarial filename injection tested across downloader, silence detection, probe, and ffmpeg encode. Good security hygiene.

### Weaknesses
- **test_captioner.py** (16 tests): Tests the captioner in isolation but doesn't test the integration path through `_process_single_clip` (where `captions_enabled=True` triggers the captioner). The `_process_single_clip` tests always pass `captions_enabled=False` (default).
- **No downloader functional tests**: Only subprocess safety is tested. The download flow (cache hit, remux, atomic rename, error cleanup) has zero tests.
- **No analytics sync tests**: `_sync_streamer_metrics` in main.py is untested.
- **`make_clip` missing `channel_key`**: Could cause false-positive test results for channel-scoped queries.

### Coverage Estimate
- **main.py**: ~70% (good for orchestration, gap in analytics sync)
- **src/clip_filter.py**: ~90% (well tested)
- **src/db.py**: ~85% (solid, all core functions covered)
- **src/dedup.py**: ~85% (good edge cases)
- **src/youtube_uploader.py**: ~60% (templates good, upload flow partially tested, credential flow untested)
- **src/video_processor.py**: ~65% (individual functions good, integration through crop_to_vertical partially tested)
- **src/twitch_client.py**: ~80% (token, rate limit, pagination covered)
- **src/captioner.py**: ~50% (unit functions tested, integration path untested)
- **src/downloader.py**: ~15% (only subprocess safety)
- **src/youtube_analytics.py**: ~0% (no direct tests)
- **src/media_utils.py**: ~30% (is_valid_video tested indirectly, extract_audio untested)

Overall: **~60% estimated line coverage** (up from ~22% in Feb 5 audit).

---

## Module Coupling Analysis

### Import Graph (simplified)
```
main.py
  -> src/models.py
  -> src/twitch_client.py -> src/models.py
  -> src/clip_filter.py -> src/db.py, src/models.py
  -> src/dedup.py -> src/db.py, src/models.py
  -> src/downloader.py -> src/media_utils.py, src/models.py
  -> src/video_processor.py -> src/media_utils.py, src/models.py
  -> src/youtube_uploader.py -> src/models.py
  -> src/youtube_analytics.py -> src/youtube_uploader.py (get_credentials)
  -> src/db.py -> src/models.py
  -> src/captioner.py -> src/media_utils.py (lazy), src/models.py (lazy)
```

### Observations
- **No circular dependencies** — clean DAG.
- **`youtube_analytics.py` depends on `youtube_uploader.py`** for `get_credentials`. This is a coupling concern: analytics module imports from upload module. Better to extract credential management to its own module.
- **`captioner.py` is well-isolated** — only depends on `media_utils` and `models`, both light.
- **`main.py` imports everything** — expected for the orchestration layer. The extraction of `_process_single_clip` and `_process_streamer` was a good structural improvement.

---

## Config Design Review

### Strengths
- `PipelineConfig.__post_init__` validates types and ranges
- `validate_config` checks required fields and env vars
- `validate_templates` checks template key validity at startup
- Secrets are env-var-only (no config.yaml fallback)

### Gaps
- **Captions config** is outside `PipelineConfig` (raw_config access)
- **No validation of `facecam_mode` values** at config load time (validated at runtime in `crop_to_vertical`)
- **No validation of `privacy_status`** or `category_id` values
- **`StreamerConfig` accepts any string for `facecam_mode`** without validation

---

## Summary Table

| ID | Severity | Effort | Location | Issue |
|----|----------|--------|----------|-------|
| A-H1 | High | M | main.py:263-267 | 17-parameter function signature |
| A-H2 | High | S | requirements.txt:7 | deepgram-sdk unconditional dependency |
| A-H3 | High | S | captioner.py:192, video_processor.py:13 | Duplicated `_remove_file` across 3 modules |
| A-H4 | High | S | youtube_uploader.py:333 | Module-level mutable cache with `id()` key |
| A-M1 | Medium | S | captioner.py:23-24 | Unnecessary lazy import of `extract_audio` |
| A-M2 | Medium | S | main.py:551 | `captions_enabled` bypasses PipelineConfig validation |
| A-M3 | Medium | S | src/__init__.py | No explicit public API contract |
| A-M4 | Medium | S | captioner.py:77-87 | Centiseconds rounding bug at boundary |
| A-M5 | Medium | S | db.py:120-131 | `insert_clip` overwrites youtube_id without documentation |
| A-M6 | Medium | S | test_captioner.py:33-34 | Test accepts two answers for deterministic function |
| A-M7 | Medium | S | main.py:479-483 | Misleading "processed" counter for quota-failed clips |
| A-M8 | Medium | M | main.py:141-169 | Zero tests for `_sync_streamer_metrics` |
| A-L1 | Low | S | video_processor.py:210-212 | Cached output not validated |
| A-L2 | Low | S | media_utils.py:25-33 | `extract_audio` doesn't validate output |
| A-L3 | Low | S | downloader.py | No functional tests for download logic |
| A-L4 | Low | S | youtube_uploader.py:132-135 | `_TemplateDict.__missing__` silently returns empty |
| A-L5 | Low | S | tests/conftest.py:25-48 | `make_clip` missing `channel_key` |
| A-L6 | Low | S | youtube_uploader.py:10 | `httplib2` is transitive dependency |

**Totals: 0 Critical, 4 High, 8 Medium, 6 Low = 18 findings**

---

## Recommendations (Priority Order)

1. **A-H1**: Bundle upload config params into a dataclass — biggest quality-of-life improvement
2. **A-H2**: Move `deepgram-sdk` to optional requirements
3. **A-H3**: Consolidate `_remove_file` into `media_utils.py`
4. **A-M2**: Add `captions_enabled` to `PipelineConfig`
5. **A-M4 + A-M6**: Fix centisecond rounding, then fix the test
6. **A-M8 + A-L3**: Add tests for analytics sync and downloader
7. **A-H4**: Document or refactor the uploads playlist cache

# Code Quality Pragmatism Audit — 2026-02-09

**Auditor**: pragmatist
**Scope**: All production and test code (main.py, src/*, tests/*, config.yaml, requirements.txt)

---

## Overall Assessment

**Complexity: Low-Medium. This is a pragmatic, well-structured codebase.**

For a ~2,400-line automation pipeline, this project is remarkably restrained. Functions do what they say, modules have clear boundaries, and there is very little gratuitous abstraction. The dataclass models are lightweight without being anemic. Error handling is proportional to actual risk (YouTube uploads need retries; local file moves do not). The few complexity issues that exist are concentrated in `main.py`'s parameter-passing patterns and in the analytics subsystem which was built before it was needed. The test suite is strong (183 tests) and mostly tests real behavior rather than mocks. Overall verdict: this codebase would pass a "would a senior developer enjoy maintaining this?" test.

---

## High Priority — Actively Harming Maintainability

### CQ-H1 | Over-Engineered | `main.py:263-267, 369-373` — Massive parameter lists on extracted functions

**Issue**: `_process_single_clip` takes 18 parameters. `_process_streamer` takes 19 parameters. These functions were extracted for testability, but the parameter lists have grown to the point where calling them is error-prone and reading them requires scrolling. This is the single biggest maintainability issue in the codebase.

```python
def _process_single_clip(clip, yt_service, conn, cfg, streamer, log, dry_run,
                         title_template, title_templates, description_template,
                         description_templates, extra_tags_global,
                         thumbnail_enabled, thumbnail_samples, thumbnail_width,
                         captions_enabled=False):
```

**Fix**: Most of the template/thumbnail/tag parameters come from `raw_config["youtube"]`. Create a simple `YouTubeConfig` dataclass (like `PipelineConfig` already does for pipeline settings) to bundle them. This would cut both signatures to ~8 parameters without adding abstraction layers.

```python
@dataclass
class YouTubeConfig:
    client_secrets_file: str = ""
    title_template: str | None = None
    title_templates: list[str] | None = None
    description_template: str | None = None
    description_templates: list[str] | None = None
    extra_tags: list[str] | None = None
    thumbnail_enabled: bool = False
    thumbnail_samples: int = 8
    thumbnail_width: int = 1280
    captions_enabled: bool = False
```

### CQ-H2 | Unnecessary Complexity | `main.py:466-508` — Result-string-matching state machine

**Issue**: `_process_single_clip` returns a string result code (`"downloaded_fail"`, `"processed_fail"`, etc.) which `_process_streamer` then matches in a long if/elif chain to update counters. This is brittle — a typo in a result string would silently fall through to the else-less chain with no counter updates. It also makes it hard to see at a glance what state transitions are valid.

**Fix**: This works fine today but is fragile. An enum would make typos impossible and give IDE autocomplete. Alternatively, return a named tuple `(downloaded: bool, processed: bool, uploaded: bool, error_type: str | None)` so the caller doesn't need a 10-branch if/elif.

### CQ-H3 | Anti-Pattern | `requirements.txt:7` — deepgram-sdk unconditionally required

**Issue**: `deepgram-sdk>=3.0.0` is in the main requirements file, but captions are disabled by default (`captions.enabled: false` in config) and the import is guarded behind a runtime check (`from src.captioner import generate_captions` only runs if `captions_enabled`). Every CI run and every fresh install downloads a large SDK that 99% of users will never use.

**Fix**: Move `deepgram-sdk` to a separate `requirements-captions.txt` or use an extras group. The lazy import in `main.py:299` already handles the case where it's not installed.

---

## Medium Priority — Unnecessary Complexity to Simplify

### CQ-M1 | Unnecessary Complexity | `src/youtube_analytics.py` + `src/db.py:244-267` — Dormant analytics subsystem

**Issue**: The entire analytics subsystem (79 lines in `youtube_analytics.py`, ~50 lines in `db.py` for metrics queries, ~30 lines in `main.py` for sync orchestration) is dormant (`analytics_enabled: false`) and has been since the project's creation. It adds 7 database columns, 3 config parameters, and test coverage burden for code that has never run in production.

**Fix**: Keep it, but acknowledge the cost. If analytics isn't planned for the next 2-3 months, consider extracting it to a branch. The code is well-written and easy to re-integrate; the issue is carrying dead weight in every code review, test run, and refactor.

### CQ-M2 | Over-Engineered | `src/models.py:69-112` — PipelineConfig validation overkill

**Issue**: `PipelineConfig.__post_init__` has 40+ lines of validation that coerces types and checks ranges for 11 fields. Some of these checks are reasonable (negative values), but YAML already gives you typed values, and `int("log")` will already raise `ValueError` without custom validation. The validation is essentially duplicating what Python's type system and YAML parser already provide.

**Fix**: Keep the enum checks for `age_decay` and `view_transform` (those catch real mistakes). The numeric coercion (`int(value)` fallback) is borderline — it handles edge cases from YAML but adds maintenance cost. Consider simplifying to just the enum validation and letting Python's normal errors surface naturally.

### CQ-M3 | Unnecessary Complexity | `src/youtube_uploader.py:333-353` — Uploads playlist cache keyed by `id(service)`

**Issue**: `_uploads_playlist_cache` is a module-level dict keyed by `id(service)` (the memory address of the service object). This is a valid optimization (saves 1 API call per streamer per run), but `id()` can be reused after garbage collection, leading to stale cache hits in theory. In practice the service objects live for the entire pipeline run, so this works, but it's fragile and clever in a way that will confuse the next reader.

**Fix**: Since the pipeline currently processes one streamer at a time and creates a new service per streamer, this cache is only effective within a single streamer's processing loop. Consider caching the playlist ID by `credentials_file` path instead — more predictable, less clever.

### CQ-M4 | Unnecessary Complexity | `src/video_processor.py:84-123` — `_batch_sample_ydif` duplicates `_sample_ydif` logic

**Issue**: `_batch_sample_ydif` exists alongside `_sample_ydif`. The batch version was added for performance (1 ffmpeg call instead of N), but `_sample_ydif` is now dead code — it's defined but never called. Extract_thumbnail uses `_batch_sample_ydif` exclusively.

**Fix**: Delete `_sample_ydif` (lines 62-81). It's unused dead code.

### CQ-M5 | Over-Engineered | `src/youtube_uploader.py:198-209` — Tag length limiter

**Issue**: `_limit_tag_length` carefully counts comma-separated tag lengths against a 500-char limit. The YouTube API itself enforces tag limits and returns clear errors. The pipeline uploads ~5-10 tags per video — nowhere near 500 characters.

**Fix**: Keep it (it's defensive and short), but note it's solving a theoretical problem. Not worth removing, but also not worth expanding.

### CQ-M6 | Anti-Pattern | `src/captioner.py:22-28` — Lazy imports inside function body

**Issue**: `transcribe_clip` lazily imports `from src.media_utils import extract_audio` and `from deepgram import DeepgramClient, FileSource, PrerecordedOptions` inside the function body. Lazy imports for optional heavy dependencies (Deepgram) are fine. But `extract_audio` from `media_utils` is a project-internal module — there's no reason to lazy-import it.

**Fix**: Move `from src.media_utils import extract_audio` to the top of `captioner.py`. Keep the Deepgram import lazy (it's a large optional dependency).

### CQ-M7 | Unnecessary Complexity | `src/captioner.py:192-197` + `src/video_processor.py:13-17` — Duplicated `_remove_file` helper

**Issue**: Both `captioner.py` and `video_processor.py` define identical `_remove_file` functions. There's also `_cleanup_tmp_files` in `main.py` that does the same thing for multiple paths. Three places to do "best-effort delete a file."

**Fix**: Move `_remove_file` to `media_utils.py` (which already serves as the shared utility module) and import it in both places. Or just inline `os.remove` with a try/except where needed — it's 3 lines.

---

## Low Priority — Minor Cleanup Opportunities

### CQ-L1 | Dead Code | `src/video_processor.py:62-81` — `_sample_ydif` is never called

**Issue**: As noted in CQ-M4, `_sample_ydif` is completely unused. It was superseded by `_batch_sample_ydif`.

**Fix**: Delete it.

### CQ-L2 | Unnecessary Complexity | `config.yaml:48` — `max_clips_per_streamer: 6` but `max_uploads_per_window: 1`

**Issue**: The pipeline fetches up to 500 clips, filters and ranks them, then caps at `max_clips_per_streamer=6`, but can only upload `max_uploads_per_window=1` per 2-hour window. This means 5 out of 6 clips go through game name resolution, channel dedup checking, and the download/process loop only to be stopped by the upload limit. The `max_clips_per_streamer` cap happens before the upload limit check.

**Fix**: Not a code change but a config insight. If the pipeline typically uploads 1 clip per run, `max_clips_per_streamer: 3` would be sufficient and reduce wasted API calls. The code correctly handles this — but the config defaults are more generous than the upload limits allow.

### CQ-L3 | Unnecessary Complexity | `main.py:553-558` — Triple isinstance checks for config list types

**Issue**: Three sequential `isinstance(x, str)` checks convert single strings to lists. This handles an edge case in YAML parsing.

```python
if isinstance(title_templates, str):
    title_templates = [title_templates]
if isinstance(description_templates, str):
    description_templates = [description_templates]
if isinstance(extra_tags_global, str):
    extra_tags_global = [extra_tags_global]
```

**Fix**: This is fine but could be a one-liner utility: `ensure_list = lambda v: [v] if isinstance(v, str) else (v or [])`. Not worth changing unless you're refactoring the config loading anyway.

### CQ-L4 | Over-Engineered | `src/db.py:48-69` — Column migration checks on every connection

**Issue**: Every call to `get_connection` runs `PRAGMA table_info(clips)` and checks for 7 missing columns. This migration pattern was needed when the schema evolved, but once all production databases have the columns (which they should by now), these checks are pure overhead.

**Fix**: Leave them for safety — they're cheap (`PRAGMA table_info` is fast on SQLite) and protect against stale cached DBs from CI. Just noting that they're technical debt that could be simplified once the schema stabilizes.

### CQ-L5 | Unnecessary Complexity | `tests/test_main.py:82-89` — Helper `_call` method with 9 hardcoded params

**Issue**: `TestProcessSingleClip._call` is a test helper that passes 9 hardcoded parameters to `_process_single_clip`. Every test that uses it has the same boilerplate. This is a symptom of CQ-H1 (the massive parameter list).

**Fix**: If CQ-H1 is addressed (bundling YouTube params into a dataclass), this helper becomes trivial. Leave it for now.

### CQ-L6 | Anti-Pattern | `src/youtube_uploader.py:10` — Unused `httplib2` import still present

**Issue**: `import httplib2` is at the top of `youtube_uploader.py`, but `httplib2` is only used in the `except` clause catching `httplib2.error.RedirectMissingLocation`. The import itself is fine, but `httplib2` is a transitive dependency of `google-api-python-client` — it's not in `requirements.txt` directly. If the Google client library ever drops httplib2, this import breaks with no direct requirements pin to protect it.

**Fix**: Low risk since Google's client library depends on httplib2, but worth noting. No action needed.

---

## Good Patterns — Things Done Well Worth Preserving

### CQ-G1 | Good Pattern | Atomic writes throughout

Every file write in the pipeline uses atomic patterns: `.part` -> `os.replace()` for downloads, `.tmp` -> `os.replace()` for ffmpeg output, `O_CREAT|O_EXCL` for lock files, `O_CREAT|O_TRUNC` with `0o600` for credentials. This is production-grade reliability thinking.

### CQ-G2 | Good Pattern | Dataclass models over dicts

`Clip`, `FacecamConfig`, `StreamerConfig`, `PipelineConfig` are clean dataclasses that carry exactly the fields needed. No inheritance hierarchies, no abstract base classes, no builder patterns. Exactly right for this project scale.

### CQ-G3 | Good Pattern | `_TemplateDict` with `__missing__`

The `_TemplateDict` class in `youtube_uploader.py` elegantly handles unknown template keys by returning empty strings and logging a warning. This prevents `KeyError` crashes from user-defined templates without silently swallowing the issue. Pragmatic and safe.

### CQ-G4 | Good Pattern | DB-before-verify upload order

The decision to `insert_clip()` immediately after `upload_short()` succeeds — before `verify_upload()` or `set_thumbnail()` — is exactly right. A phantom DB entry is trivially cleanable; a duplicate YouTube upload is not. This shows real operational thinking.

### CQ-G5 | Good Pattern | GPU -> CPU fallback with shared loudness measurement

`crop_to_vertical` measures loudness once, then passes it to both the GPU and CPU attempts. This avoids redoing expensive I/O work on fallback. Simple optimization, correctly implemented.

### CQ-G6 | Good Pattern | Subprocess safety

All subprocess calls use list arguments (never `shell=True`), and there's a dedicated test file (`test_subprocess_safety.py`) that verifies adversarial filenames can't cause injection. This level of security awareness is above average for a personal automation project.

### CQ-G7 | Good Pattern | Test suite quality

183 tests that focus on behavior rather than implementation details. Tests for edge cases (zero duration, future timestamps, adversarial filenames), integration tests for the pipeline loop, and proper fixture sharing via `conftest.py`. The mock usage is restrained — most tests verify actual behavior rather than "did we call this mock."

### CQ-G8 | Good Pattern | Graceful degradation in captioner

The entire captions feature is wrapped in graceful fallbacks: no API key -> skip, audio extraction fails -> skip, Deepgram fails -> skip, ASS generation fails -> skip. The pipeline continues without captions rather than crashing. This is the right approach for an optional feature.

### CQ-G9 | Good Pattern | Channel dedup using `playlistItems.list`

Using `playlistItems.list` (2 quota units) instead of `search.list` (100 quota units) for duplicate checking shows awareness of API quota costs. The `_uploads_playlist_cache` (despite CQ-M3's concerns about the cache key) is a practical optimization.

### CQ-G10 | Good Pattern | Clean separation of concerns

Each module has a single clear responsibility: `twitch_client.py` talks to Twitch, `youtube_uploader.py` talks to YouTube, `video_processor.py` handles ffmpeg, `db.py` handles SQLite, `clip_filter.py` handles scoring. There's no circular dependencies, no god modules, and `main.py` orchestrates without duplicating module logic.

---

## Priority Actions — Top 3 Changes for Maximum Impact

1. **Bundle YouTube config parameters into a dataclass** (CQ-H1) — This would simplify both `_process_single_clip` and `_process_streamer` from 18-19 params to ~8, making the code dramatically easier to read, call, and test. Estimated effort: 30 minutes.

2. **Delete dead `_sample_ydif` function** (CQ-L1/CQ-M4) — Free cleanup with zero risk. Remove lines 62-81 from `video_processor.py`.

3. **Move deepgram-sdk to optional requirements** (CQ-H3) — Reduces install footprint for all users who don't use captions (which is currently everyone, since captions are disabled by default).

---

## Summary Table

| ID | Severity | Type | Location | Brief |
|----|----------|------|----------|-------|
| CQ-H1 | High | Over-Engineered | main.py:263,369 | 18-19 parameter functions |
| CQ-H2 | High | Unnecessary Complexity | main.py:466-508 | String-based result matching |
| CQ-H3 | High | Anti-Pattern | requirements.txt:7 | Unconditional deepgram-sdk dependency |
| CQ-M1 | Medium | Unnecessary Complexity | youtube_analytics.py (all) | Dormant analytics subsystem |
| CQ-M2 | Medium | Over-Engineered | models.py:69-112 | PipelineConfig validation overkill |
| CQ-M3 | Medium | Unnecessary Complexity | youtube_uploader.py:333-353 | Cache keyed by id(service) |
| CQ-M4 | Medium | Dead Code | video_processor.py:62-81 | Unused _sample_ydif |
| CQ-M5 | Medium | Over-Engineered | youtube_uploader.py:198-209 | Tag length limiter |
| CQ-M6 | Medium | Anti-Pattern | captioner.py:22-28 | Lazy import of internal module |
| CQ-M7 | Medium | Unnecessary Complexity | captioner.py:192 + video_processor.py:13 | Duplicated _remove_file |
| CQ-L1 | Low | Dead Code | video_processor.py:62-81 | Same as CQ-M4 |
| CQ-L2 | Low | Config Insight | config.yaml:48 | max_clips_per_streamer > max_uploads_per_window |
| CQ-L3 | Low | Unnecessary Complexity | main.py:553-558 | Triple isinstance checks |
| CQ-L4 | Low | Over-Engineered | db.py:48-69 | Column migration on every connect |
| CQ-L5 | Low | Anti-Pattern | tests/test_main.py:82-89 | Test helper with 9 params |
| CQ-L6 | Low | Anti-Pattern | youtube_uploader.py:10 | httplib2 transitive dep import |

**Good Patterns: 10 identified** (CQ-G1 through CQ-G10) — atomic writes, dataclass models, template safety, DB-before-verify, GPU fallback, subprocess safety, test quality, graceful degradation, quota-aware API usage, clean module separation.

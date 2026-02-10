# Fix Team Prompt

Paste this into Claude Code to spawn the fix team.

---

## Design Rationale

- **4 teammates** — matches audit structure, avoids coordination overhead
- **Strict file ownership** — prevents merge conflicts (docs say "two teammates editing the same file leads to overwrites")
- **Task dependencies** — test-writer starts CI fixes immediately, blocks on code changes for test writing
- **~6-8 tasks per teammate** — within the "5-6 tasks keeps everyone productive" sweet spot
- **Sonnet for all** — cost-effective for focused implementation tasks
- **Delegate mode** — lead only coordinates, doesn't implement
- **Verification gate** — all tests must pass before team cleanup

---

## The Prompt

```
Create an agent team to fix all 28 prioritized findings from our codebase audit.
Use Sonnet for all teammates. Use delegate mode — you coordinate only, do not write code.

Context files each teammate MUST read before starting:
- @audit-summary.md, audit-history.md
- README.md (architecture reference)

Spawn 4 teammates:

---

### 1. core-fixes (owns: main.py, src/db.py, src/dedup.py, src/clip_filter.py, src/models.py)

Fix the pipeline core — config, locking, orchestration, database, dedup.

Tasks in order:
1. **[S-C2] Remove config.yaml secret fallback** — main.py:100-103. Remove the code path that reads twitch.client_id and twitch.client_secret from config.yaml. Secrets must come from env vars only. Add a comment in config.yaml warning not to put secrets there.
2. **[S-H4/R-C2] Atomic lock file** — main.py:230-234. Replace the os.remove() + _try_create_lock() TOCTOU race with os.replace() to atomically write the new PID. The stale-PID detection logic stays, just make the replacement atomic.
3. **[A-H4/R-L3] PipelineConfig validation** — src/models.py. Add __post_init__ to PipelineConfig that validates: numeric fields are numeric and non-negative, age_decay is in {"linear","log"}, view_transform is in {"linear","log"}, privacy_status is in {"public","private","unlisted"}. Raise ValueError with a clear message for bad values. Also warn (log.warning) for unknown keys passed to the constructor.
4. **[A-H5] Guard client_secrets_file access** — main.py:357. Change bare dict subscription youtube_cfg["client_secrets_file"] to .get() with a clear error message if missing.
5. **[R-M7] COALESCE youtube_id in record_known_clip** — src/db.py:124-136. Change the ON CONFLICT UPDATE to: youtube_id = COALESCE(clips.youtube_id, excluded.youtube_id). This prevents false-positive channel dedup matches from overwriting legitimate YouTube IDs.
6. **[P-C2] Optimize clip_overlaps query** — src/db.py:71-80. Add a coarse time range pre-filter using ISO string comparison (WHERE created_at >= ? AND created_at <= ?) before the expensive julianday() computation. Calculate the time window from overlap_seconds and pass as additional parameters.
7. **[A-C1] Extract functions from _run_pipeline_inner** — main.py:258-484. Extract: (a) _process_single_clip(clip, ...) for the per-clip download/process/upload/verify logic, and (b) _process_streamer(streamer, ...) for the per-streamer loop. Keep _run_pipeline_inner as a ~40-line high-level orchestrator. Preserve all existing behavior exactly — this is a pure refactor.
8. **[A-M3] Remove compute_score wrapper** — src/clip_filter.py:39-40. Remove compute_score(), rename compute_score_with_options to compute_score. Update all callers in tests/test_clip_filter.py.
9. **[A-M2] Configurable blocklist path** — src/dedup.py:8. Change BLOCKLIST_PATH from relative-to-__file__ to accept a path parameter in filter_new_clips, defaulting to "data/blocklist.txt" relative to CWD. Update the caller in main.py.

After completing all tasks, run: python -m pytest tests/ -v
Fix any test failures before marking done.

---

### 2. youtube-fixes (owns: src/youtube_uploader.py, src/youtube_analytics.py)

Fix YouTube API robustness, dedup safety, upload performance, and input sanitization.

Tasks in order:
1. **[R-H3] Add YouTube API timeout** — When building the YouTube service in get_credentials() or where the service is constructed, set http timeout to 30 seconds. Use httplib2.Http(timeout=30) or equivalent. This prevents indefinite hangs on API calls.
2. **[R-H4] Fix channel dedup error swallowing** — src/youtube_uploader.py:336-341. In check_channel_for_duplicate, distinguish fatal errors (401 Unauthorized, 403 Forbidden with reason != "quotaExceeded") from transient errors (5xx, network, quotaExceeded). Raise on fatal errors so the pipeline stops. Return None only on transient errors (safe to proceed). Log the distinction.
3. **[R-C1/A-M6] Fix analytics fallback** — src/youtube_analytics.py:47-49. Wrap the fallback _query_metrics call in its own try/except. On failure, log a warning and return None instead of propagating the exception.
4. **[S-H2] Escape format strings in clip titles** — src/youtube_uploader.py, in _render_template() or before format_map() is called. Replace { with {{ and } with }} in all user-supplied values (clip.title, clip.streamer, clip.game_name) before they enter the template. This prevents a Twitch clip titled "{streamer}" from being double-interpolated.
5. **[P-H1] Cache uploads_playlist_id** — src/youtube_uploader.py:300-341. The channels().list() call returns the same uploads playlist ID every time within a run. Cache it as an instance attribute or accept it as a parameter. Look up once, reuse for all subsequent check_channel_for_duplicate calls.
6. **[P-M5] Increase upload chunk size** — src/youtube_uploader.py:257. Set chunksize=5*1024*1024 (5MB) in MediaFileUpload(). This reduces HTTP round-trips from ~120 to ~6 for a typical 30MB video.
7. **[S-M3] Strip Unicode bidi characters** — src/youtube_uploader.py, in _sanitize_text(). Additionally strip Unicode bidirectional control characters: U+200E-U+200F, U+202A-U+202E, U+2066-U+2069. Use a regex character class.
8. **[A-M1] Validate template keys** — In the YouTube uploader or at config load time, validate that title_templates and description_template only reference known keys {title}, {streamer}, {game}, {game_name}. Log a warning for unknown keys.

After completing all tasks, run: python -m pytest tests/ -v
Fix any test failures before marking done.

---

### 3. media-fixes (owns: src/twitch_client.py, src/video_processor.py, src/downloader.py, NEW src/media_utils.py)

Fix Twitch API security, consolidate media utilities, optimize ffmpeg probes.

Tasks in order:
1. **[S-C1] Fix Twitch secret in URL** — src/twitch_client.py:27-31. Change params= to data= in the requests.post() call for the token endpoint. This sends client_secret in the POST body instead of the URL. 1-line change.
2. **[S-H1] Enforce TLS verification** — src/twitch_client.py:50. Add verify=True explicitly to the requests.request() call in _request(). This prevents env var tampering from disabling certificate verification.
3. **[R-H5] Add token refresh backoff** — src/twitch_client.py:24-37. Add a 2-second sleep between token acquisition failures. If _get_token() raises, wait 2s before the next retry. This prevents hammering Twitch during outages.
4. **[A-H1] Create src/media_utils.py** — Extract FFPROBE and FFMPEG constants and the _is_valid_video() function from video_processor.py/downloader.py into a new src/media_utils.py. Update imports in both downloader.py and video_processor.py to import from media_utils instead.
5. **[R-L1] Narrow downloader exception** — src/downloader.py:85-87. Change except Exception to except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) in _is_valid_video.
6. **[P-C1] Consolidate ffmpeg probes** — src/video_processor.py:145-260. This is the biggest task:
   a. Replace separate _get_duration() + _get_dimensions() calls with a single ffprobe -show_format -show_streams -print_format json call that returns both duration and dimensions.
   b. Combine the 3 separate _has_facecam ffmpeg calls (at 25%, 50%, 75%) into a single ffmpeg invocation that seeks to all 3 timestamps using a complex filtergraph or sequential segment processing.
   c. Target: reduce 6 subprocess spawns to 2 per clip. Preserve all existing behavior (facecam detection logic, dimension extraction, duration extraction).

After completing all tasks, run: python -m pytest tests/ -v
Fix any test failures before marking done.

---

### 4. test-and-ci (owns: tests/ directory, .github/workflows/, requirements.txt, requirements-dev.txt)

CI hardening, dependency cleanup, and comprehensive test coverage for untested modules.

**DEPENDENCY: Tasks 1-3 can start immediately. Tasks 4-8 are BLOCKED until core-fixes, youtube-fixes, and media-fixes complete their code changes.** Start with CI tasks while waiting.

Tasks in order:
1. **[A-M7] Split dependencies** — Split requirements.txt into requirements.txt (production only: google-api-python-client, google-auth-httplib2, google-auth-oauthlib, requests, pyyaml, yt-dlp) and requirements-dev.txt (dev: pytest, ruff, mypy, pre-commit, type stubs). Update .github/workflows/pipeline.yml to install only requirements.txt. Update .github/workflows/tests.yml to install both.
2. **[P-H4] Skip redundant apt-get** — .github/workflows/pipeline.yml:28-31. Gate ffmpeg install with: which ffmpeg || sudo apt-get install -y ffmpeg. Remove sqlite3 from install (already in ubuntu-latest). Remove apt-get update if ffmpeg is already present.
3. **[S-M2] Add credential cleanup step** — .github/workflows/pipeline.yml. Add a final step with if: always() that runs: rm -rf credentials/. This removes OAuth tokens from the runner after the pipeline completes.

--- WAIT for other teammates to finish before proceeding ---

4. **[S-T1] Sanitization tests** — tests/test_youtube_uploader.py. Add tests for _sanitize_text with: control characters (\x00-\x1f), <script> tags, {format_string} syntax, Unicode bidi overrides (U+202E), null bytes, strings exceeding 100 chars. Also test the new format string escaping in _render_template.
5. **[S-T3] Subprocess safety tests** — New test file or extend existing. Add parameterized tests with adversarial filenames containing shell metacharacters (spaces, quotes, semicolons, pipes, backticks) to verify no command injection in downloader.py and video_processor.py subprocess calls.
6. **[A-H3] Twitch client tests** — New tests/test_twitch_client.py. Mock requests.request to test: token refresh on 401, rate-limit sleep on 429, pagination termination, _get_token backoff on failure, verify=True enforcement.
7. **[A-H2] Video processor tests** — New tests/test_video_processor.py. Mock subprocess to test: _build_composite_filter output for various FacecamConfig values, _detect_leading_silence regex parsing, the new consolidated ffprobe call (if media-fixes changed the API), GPU/CPU fallback logic.
8. **[A-C2] Main pipeline integration tests** — New tests/test_main.py. Mock all external services (TwitchClient, YouTubeUploader, ffmpeg, yt-dlp, filesystem). Test: happy path end-to-end, upload spacing enforcement, quota exhaustion handling, consecutive 403 circuit breaker, dry-run mode, channel dedup path, the new extracted functions from A-C1.

After completing all tasks, run: python -m pytest tests/ -v
ALL tests must pass. Fix any failures.

---

## Coordination rules:
1. Each teammate edits ONLY their owned files. No exceptions.
2. test-and-ci starts CI work (tasks 1-3) immediately, then waits for code teammates to finish before writing tests.
3. After ALL 4 teammates complete, run the full test suite as final verification.
4. Do NOT skip the pytest verification step — every teammate must prove their changes don't break anything.
5. If a teammate encounters a conflict with another's file, STOP and message the file owner instead of editing it.
```

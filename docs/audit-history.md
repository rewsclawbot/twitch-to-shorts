# Audit History

Archived from `tasks/todo.md` on 2026-02-05 after all 28 findings were fixed.
See `docs/audits/` for the original detailed audit reports.

---

## Audit Fix Sprint (2026-02-05)

4-agent team fixed all 28 prioritized findings from the codebase audit.
166 tests passing (up from 46). 120 new tests added.

### Findings Fixed

**Security (6 critical/high):**
- [S-C1] Twitch secret moved from URL params to POST body
- [S-C2] Config.yaml secret fallback removed (env vars only)
- [S-H1] TLS verification enforced on Twitch API
- [S-H2] Format string injection prevented in clip titles
- [S-M2] Credential cleanup step added to CI
- [S-M3] Unicode bidi characters stripped from titles

**Reliability (5 critical/high):**
- [R-C2] Atomic lockfile (os.replace instead of remove+create)
- [R-H3] YouTube API timeout (30s)
- [R-H4] Channel dedup distinguishes fatal vs transient errors
- [R-H5] Token refresh backoff (2s sleep between failures)
- [R-C1/A-M6] Analytics fallback wrapped in try/except
- [R-M7] COALESCE youtube_id prevents overwriting legitimate IDs
- [R-L1] Narrowed downloader exception types

**Performance (4):**
- [P-C1] Consolidated ffmpeg probes (6 -> 2 subprocess spawns per clip)
- [P-C2] Optimized clip_overlaps query with time range pre-filter
- [P-H1] Cached uploads_playlist_id across dedup checks
- [P-H4] Skipped redundant apt-get in CI
- [P-M5] Upload chunk size increased to 5MB

**Architecture (7):**
- [A-C1] Extracted _process_single_clip and _process_streamer from God function
- [A-H1] Created src/media_utils.py (shared FFMPEG/FFPROBE constants + is_valid_video)
- [A-H4] PipelineConfig validation (__post_init__)
- [A-H5] Guarded client_secrets_file access
- [A-M1] Template key validation
- [A-M2] Configurable blocklist path
- [A-M3] Removed compute_score wrapper
- [A-M7] Split requirements.txt into prod/dev

**New test coverage:**
- tests/test_main.py (20 tests - pipeline integration)
- tests/test_twitch_client.py (14 tests - API client)
- tests/test_video_processor.py (19 tests - ffmpeg processing)
- tests/test_subprocess_safety.py (33 tests - adversarial filenames)
- tests/test_youtube_uploader.py (expanded - sanitization, templates)

---

## Previous Audits

### 2026-02-04 Full Agent Audit
- 6 agents in parallel, 28 findings (6 critical, 10 high, 12 moderate)
- Detailed reports in `docs/audits/audit-*.md`

### 2026-02-04 Codex Changes Audit
- 3-phase sequential (validator -> pragmatist -> Jenny)
- Fixed: `title` vs `full_title` NameError, missing performance multiplier
- Set analytics_enabled: false

### 2026-02-02 Competition Audit
- 16 items from Waves 1-4 implemented (commits `95974bd` through `f1448db`)
- Typed models, test suite, pre-commit, CI cache fix, loudness hoist, etc.

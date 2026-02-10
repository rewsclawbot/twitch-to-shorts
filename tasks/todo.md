# Active Tasks

## Current Sprint

- [x] **ffmpeg CPU preset → fast** — Resolved. Changed to `-preset fast` unconditionally for CPU encode. Captions add ~10-15% more encode work; `fast` compensates and prevents timeouts.
- [x] **Burned-in captions** — Deepgram Nova-2 STT → ASS subtitles → ffmpeg burn-in. Default off (`captions.enabled: false`). Graceful degradation on failure.
- [x] **Roadmap Phase 2 status update** — Marked 2.1-2.4 as DONE, added 2.6 Captions.

## Backlog: Managed Service Sequence

These are documented in the roadmap but NOT being built yet. Phase discipline: don't build Phase 3-5 at Phase 1.

1. **Multi-platform distribution** (Phase 3.6) — TikTok + Instagram Reels. Requires TikTok business verification (action item: start verification process).
2. **Streamer dashboard** (Phase 4.6) — Web UI for performance monitoring + clip management.
3. **Stripe billing** (Phase 4.7) — Subscription model for managed service.
4. **Scaling infrastructure** (Phase 5) — Queue-based processing, horizontal scaling.

## Action Items

- [ ] **TikTok business verification** — Start the verification process now; it takes weeks. Doesn't require any engineering work.
- [x] **Enable analytics** — Flipped `analytics_enabled: true`, removed dead impression metrics tier (impressions/CTR not available via API). Collecting views, watch time, retention. Performance multiplier stays dormant (returns 1.0) until sufficient CTR data.

## P2 Backlog (Audit 2026-02-10)

Deferred findings from 7-auditor sweep. None are blockers; all are hardening.

### Security
- [ ] Validate `download_url` domain before setting `request.uri` in youtube_reporting.py (SSRF risk if API response is poisoned)
- [ ] Validate `clip.url` against expected Twitch domains before passing to yt-dlp
- [ ] Cast loudness values to float and validate finite before ffmpeg filter interpolation
- [ ] Expand bidi character stripping in `_sanitize_text` (missing `\u061c`, `\u200b-\u200d`, `\ufeff`)
- [ ] Add `--max-filesize` flag to yt-dlp downloads
- [ ] Add size cap on report CSV downloads in youtube_reporting.py

### Reliability
- [ ] Add HTTP timeout to YouTube Reporting/Analytics API service `build()` calls
- [ ] Add retry logic with backoff for Reporting API calls (currently fail-once-and-give-up)
- [ ] Fix `check_channel_for_duplicate` pagination loop (can infinite-loop on zero-item pages)
- [ ] Fix `_uploads_playlist_cache` key collision across streamers (default "default" key shared)
- [ ] Add `.jpg` to `clean_stale_tmp` suffix list for orphaned thumbnails
- [ ] Add ruff/mypy step to CI test workflow

### Integration
- [ ] Decouple analytics sync from clip-processing pipeline (idle streamers never get metrics synced)
- [ ] Clarify performance multiplier gate: docs say "2+ streamers" but code only checks per-streamer threshold
- [ ] Add `validate_config` check for analytics prerequisites when `analytics_enabled: true`

### Video Processing
- [ ] Add FacecamConfig `__post_init__` validation (bounds check x/y/w/h floats)
- [ ] Fix subtitle path escaping for `=` and `#` characters in ffmpeg filter syntax
- [ ] Add warning-level logging when YDIF parsing produces fewer values than expected

## Recently Completed

- [x] **Reach metrics audit + fix** (2026-02-10) — 7-auditor sweep, 9 P0+P1 bugs fixed, 51 new tests (276 total). Key fixes: wrong CSV columns (dead module), NULL overwrite destroying reach data, retry-blocking touch on double-failure.
- [x] **Audit fix sprint** (2026-02-05) — All 28 findings fixed, 166 tests passing. See `docs/audit-history.md`.
- [x] **3-layer upload dedup defense** (2026-02-05) — DB-before-verify, artifact fallback, channel dedup.
- [x] **Latent bug fixes** (2026-02-05) — Upload starvation, dead retries, spacing poisoning.

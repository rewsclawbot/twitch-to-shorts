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

## Recently Completed

- [x] **Audit fix sprint** (2026-02-05) — All 28 findings fixed, 166 tests passing. See `docs/audit-history.md`.
- [x] **3-layer upload dedup defense** (2026-02-05) — DB-before-verify, artifact fallback, channel dedup.
- [x] **Latent bug fixes** (2026-02-05) — Upload starvation, dead retries, spacing poisoning.

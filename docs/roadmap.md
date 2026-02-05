# Twitch-to-Shorts: Monetization Roadmap

> Grounded in reality. Written 2026-02-02. Updated 2026-02-04.
>
> **Current state:** Pre-revenue. Pipeline uploading to YouTube successfully.
> 6 production Shorts live, 158 total channel views (includes old test clips).
> Channel restriction lifted. Cron running, stabilizing. $100/mo spend on Claude Max.
> No streamer permissions. Streamer: TheBurntPeanut.

---

## Phase 1: Prove the Content Works

**Goal:** Answer the question "do automated Twitch clips get views on YouTube?"
**Timeline gate:** Do NOT move to Phase 2 until you have 2 weeks of data.
**Eng work:** Near zero. The pipeline is built.

| # | Milestone | How you know it's done | Eng lift | Status |
|---|-----------|----------------------|----------|--------|
| 1.1 | Channel restriction lifts | Can upload 1 video without error | None — wait | DONE |
| 1.2 | First clean production upload | 1 properly formatted Short is live and public | Run pipeline manually or wait for cron | DONE — 6 Shorts live |
| 1.3 | Ramp to 1-2 uploads/day | Pipeline runs on schedule, no failures for 3 consecutive days | Config tweak: `max_uploads_per_window: 1` is already correct for 4hr cron. Just let it run | IN PROGRESS — cron running, was flaky, stabilizing |
| 1.4 | 2-week data checkpoint | You can answer: avg views/Short, avg watch time, CTR, sub growth trend | Manual check in YouTube Studio | PENDING — target ~2026-02-16 |

**Early data snapshot (2026-02-04, 6 Shorts, <3 days of data — too early to draw conclusions):**

| Short | Length | Avg View Duration | Retention | Views |
|-------|--------|------------------|-----------|-------|
| Beth Oven | 1:01 | 0:38 | 65.6% | 64 |
| When You Flex on the Nut | 0:18 | 0:17 | 50.0% | 11 |
| PEANUT FLYS AWAY! | 0:52 | 1:12 | 40.0% | 4 |
| money | 0:31 | 0:07 | 100% | 2 |

**Decision gate after 1.4:**
- **Shorts averaging 500+ views** → Content works. Move to Phase 2.
- **Shorts averaging 100-500 views** → Content is marginal. Stay in Phase 1, experiment with different clip selection (streamer, game, scoring params) before scaling.
- **Shorts averaging <100 views** → Core hypothesis is shaky. Don't spend money scaling. Investigate: wrong streamer? wrong clips? bad titles/thumbnails? Possibly pivot.

**What NOT to build during Phase 1:** Compilation channels, analytics API, multi-streamer, SaaS. All of it is premature.

---

## Phase 2: Dial In the Machine (single channel)

**Goal:** Maximize views per upload on one channel before adding complexity.
**Entry criteria:** Phase 1.4 shows consistent traction (500+ avg views).
**Eng work:** Small, targeted.

| # | Milestone | Why it matters | Eng lift |
|---|-----------|---------------|----------|
| 2.1 | Thumbnail generation | Thumbnails are the #1 CTR driver on YouTube. Auto-extract peak-action frame (you already compute YDIF). Huge ROI for small work | Small — extract frame at max YDIF, save as jpg, pass to upload API |
| 2.2 | Title optimization | Twitch clip titles are often garbage. Test a template like "[Streamer] [action] in [game]" or let the clip title through — A/B compare manually | Tiny — string formatting |
| 2.3 | Fail-count guard | Stop wasting upload slots retrying broken clips forever. `fail_count < 3` in dedup query | 1 line |
| 2.4 | Basic YouTube data pull | Don't need the full analytics API yet. Just pull view count per video after 48hrs, store in DB. Tells you which clips perform | Small — 1 API call per video, new DB column |
| 2.5 | Scoring tuning | Use 2.4 data to manually adjust `velocity_weight`, `min_view_count`. Not ML — just look at what worked and tweak | Config changes informed by data |

**Decision gate after 2.5 (expect ~4-6 weeks in Phase 2):**
- **Channel growing, some Shorts hitting 5K-10K+ views** → Algorithm is learning. Move to Phase 3.
- **Flat at ~500 views regardless of tuning** → Ceiling on single-streamer content. Move to Phase 3 anyway (different streamers may break through).
- **Declining or stagnant below 500** → Re-evaluate. The business may not work as automated Shorts.

---

## Phase 3: Multi-Streamer + Legal Foundation

**Goal:** Scale content supply. Get legal footing before you have something to lose.
**Entry criteria:** Phase 2 proves the content format works on at least one channel.
**Eng work:** Moderate, but mostly config and ops.

| # | Milestone | Why it matters | Eng lift |
|---|-----------|---------------|----------|
| 3.1 | Streamer permission model | Before you feature anyone new, decide: are you asking permission or relying on Twitch's clip-sharing terms? Document your position. Reach out to 2-3 streamers | Zero eng. This is homework |
| 3.2 | Onboard streamer #2 | Pick based on: high clip velocity, game variety, existing fan demand for highlights. Separate YouTube channel with own creds | Config entry + OAuth setup |
| 3.3 | Onboard streamer #3 | Same. Different game/audience to test breadth | Same |
| 3.4 | Per-streamer scoring tuning | Each streamer's content performs differently. Use Phase 2.4 data per channel | Config per streamer (already supported) |
| 3.5 | Quota check | 3 channels × 6 uploads/day = 18 uploads = ~1,800 quota units. Well under 10K free. Confirm in practice | Monitor only |

**Decision gate after 3.5:**
- **Multiple channels growing, total view velocity increasing** → Move to Phase 4.
- **Only one channel works, others flat** → The winning formula is streamer-specific, not generalizable. Double down on what works.
- **Legal pushback from streamers** → Pivot to opt-in only model before scaling further.

---

## Phase 4: Revenue Unlock

**Goal:** Hit YouTube Partner Program thresholds. Generate first dollar.
**Entry criteria:** At least 1 channel with clear growth trajectory.

| # | Milestone | Why it matters | Eng lift |
|---|-----------|---------------|----------|
| 4.1 | YouTube Analytics feedback loop | Full integration: pull performance data, correlate with clip features, auto-adjust scoring. This is the moat | Medium — API integration, new DB tables, scoring weight updates |
| 4.2 | Compilation channel (game-specific) | "Best of [Game]" Shorts channel. Pulls from all tracked streamers by game_id. Broader appeal = faster sub growth | Medium — new query mode, new channel config |
| 4.3 | Long-form weekly compilation | 8-10 min "Best of the Week" video. Standard YouTube CPM ($3-8) vs Shorts CPM ($0.04-0.08). This is where real money is | Medium — ffmpeg concat, transitions, credit overlays |
| 4.4 | Hit YPP on first channel | 1,000 subs + either 10M Shorts views (90 days) or 4,000 watch hours (long-form, 12 months) | Operational — keep uploading, optimize |
| 4.5 | First ad revenue dollar | Prove the unit economics: cost per upload vs revenue per upload | Milestone, not engineering |

---

## Phase 5: Scale or Pivot (only think about this after $)

Not worth planning in detail now. Options if Phase 4 works:
- More channels, more streamers
- SaaS / white-label for streamers (rev share)
- Compilation empire across games

Options if Phase 4 doesn't work:
- Content licensing angle (sell clips to esports orgs)
- Pivot to a different platform (TikTok, Instagram Reels)
- Shut down and take the learnings

---

## What This Roadmap Deliberately Excludes

| Idea from ideas.md | Why it's excluded for now |
|---|---|
| SaaS / white-label | You have no proven product to sell yet |
| Cross-streamer scoring ML | You have no YouTube performance data to train on |
| Quota management / multiple API projects | You're at ~6 uploads/day. Quota is irrelevant until 50+/day |
| Revenue share model | No revenue to share |
| Full analytics feedback loop | Premature until you have 100+ uploads with view data |

These aren't bad ideas. They're just Phase 4-5 ideas being discussed at Phase 1.

---

## Capital Reality Check

- **$100/mo Claude Max** is your only hard cost right now

---

## Execution Plan

### ~~Wave 5A: Audit-Critical Correctness + Security~~ DONE (2026-02-04)
### ~~Wave 5B: Ops + Hygiene Polish~~ DONE (2026-02-04)
### ~~Wave 6: Scoring Improvements~~ DONE (2026-02-04)
### ~~Wave 7: YouTube Analytics Feedback Loop~~ DONE (2026-02-04, analytics dormant until Phase 2 data)
### ~~Wave 8: Content Optimization~~ DONE (2026-02-04)
### ~~Audit Fix Sprint~~ DONE (2026-02-05) — 28 findings fixed, 166 tests. See `docs/audit-history.md`.

### Wave 9: Scale + Compilation Channels
1. Multi-streamer onboarding workflow.
2. Compilation channel mode for Shorts and long-form.
3. Attribution and permission tracking.

### Wave 10: Monetization + Ops
1. Quota management across channels or API projects.
2. Health checks and status summary output.
3. Lightweight dashboard or export for performance tracking.
- GitHub Actions free tier covers the pipeline (for now)
- YouTube API is free at this scale
- **Your most expensive resource is your time and attention.** Every hour spent building Phase 4 features is an hour not spent learning from Phase 1 data
- **Break-even target:** Figure this out after Phase 4.5. Until then, this is a $100/mo bet on a hypothesis

---

## North Star Metric Per Phase

| Phase | Metric | Target |
|---|---|---|
| 1 | Views per Short | >500 avg |
| 2 | Views per Short (optimized) | >2,000 avg |
| 3 | Total views across channels / week | >50,000 |
| 4 | Monthly revenue | >$0 (then >$100 to break even) |

If a phase's metric isn't moving, don't advance. Fix or pivot.

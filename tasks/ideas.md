# Twitch-to-Shorts: Road to Monetization Ideas

> Working notes for planning. Not a task list — a thinking space.

## Current State

- Automated pipeline: Twitch clips → vertical video → YouTube Shorts, every 4 hours
- Single channel active (TheBurntPeanut), multi-streamer support built in
- Scoring algorithm: velocity-weighted view density, configurable per streamer
- CI/CD on GitHub Actions, SQLite for state, GPU/CPU video processing
- 33 tests, typed models, production-grade error handling

## Growth Strategy

### Phase 1: Multi-Streamer Scale-Up
- Onboard more streamers to the existing pipeline
- Each streamer = their own YouTube channel with dedicated credentials
- The infra already supports this — just config entries
- Priority: pick streamers with high clip velocity and existing fan demand for highlights

### Phase 2: Compilation Channels
- Once we have enough streamers in the catalog, the DB becomes a content library
- **Game-specific channels**: "Best of Valorant Clips", "Fortnite Daily Highlights"
  - Query DB by game_id, pull top clips across all tracked streamers
  - New scoring dimension: cross-streamer ranking within a game
- **Best of Twitch**: curated daily/weekly compilations from all streamers
  - Longer format (8-10 min) for YouTube standard videos, not just Shorts
  - Higher CPM on long-form than Shorts
  - Compilation = multiple clips stitched together with transitions
- The pipeline already tags clips with game_id and streamer — the data model supports this

### Phase 3: YouTube Performance Feedback Loop
- Integrate YouTube Analytics API to pull view counts, watch time, CTR on uploaded Shorts
- Feed performance data back into the scoring algorithm
- Train velocity_weight and other params on what actually performs, not just Twitch metrics
- This is the competitive moat — "we know which Twitch clips will pop on YouTube"

### Phase 4: Revenue
- **YouTube monetization**: 1,000 subs + 10M Shorts views in 90 days (per channel)
  - Multi-channel strategy accelerates this
  - Compilation channels may hit thresholds faster due to broader appeal
- **SaaS angle**: offer the pipeline as a service to streamers/orgs
  - "We run your clips channel for you, rev share on ad revenue"
  - White-label: streamer's branding, we handle the automation
- **Long-form compilations**: higher CPM, better for ad revenue
  - Weekly "Best of [Game]" videos, 8-15 minutes
  - Requires video stitching (ffmpeg concat) — not hard to add

## Technical Ideas

- **YouTube Analytics integration**: `youtube.googleapis.com/v3/reports` — pull video performance, store in DB alongside clip data
- **Cross-streamer scoring**: new table mapping clips to compilation candidates, ranked by a blend of Twitch velocity + YouTube historical performance for similar content
- **Compilation builder**: ffmpeg concat demuxer to stitch clips with crossfades, add intro/outro, overlay streamer credits
- **Quota management**: YouTube API quota (10k units/day free) limits uploads to ~6/day. Apply for quota increase. Or stagger uploads across channels on different API projects.
- **Streamer permission system**: simple opt-in/opt-out. Streamers who opt in get attribution + link back. De-risks the legal gray area.
- **Thumbnail generation**: auto-generate thumbnails from high-action frames (peak YDIF values — we already compute these for facecam detection)

## Scoring Algorithm Deep Dive

### Current Formula
```
score = (views / duration) + (views / age_hours) * velocity_weight
```
- **Density** (`views / duration`): Rewards clips that pack engagement into shorter runtime
- **Velocity** (`views / age_hours`): Rewards clips gaining views quickly
- **velocity_weight**: Currently 2.0 — velocity matters 2x as much as density

### What's Working
- Proven viral clips naturally rise (11K views / 144h still beats 200 views / 12h)
- Short clips get density boost, aligning with Shorts format
- Simple, explainable, no black box

### Potential Improvements

#### 1. Logarithmic Age Decay
**Problem**: Linear age penalty treats 6-day vs 7-day clips differently, but that gap doesn't matter. Meanwhile 1h vs 6h matters a lot.
**Idea**: `velocity = views / log(age_hours + 1)` — flattens the penalty for older proven clips

#### 2. Absolute View Floor
**Problem**: A 1-hour clip with 50 views has velocity=50, but it's noise not signal.
**Idea**: Require minimum 500 views (or top 10% of streamer's clips) before scoring

#### 3. YouTube Feedback Loop (Phase 3)
**Problem**: Twitch velocity ≠ YouTube performance. We're optimizing for the wrong platform.
**Idea**: Pull YouTube Analytics, compute `youtube_score = yt_views / twitch_views`, use as multiplier on future clips with similar characteristics

#### 4. Content-Type Multipliers
**Problem**: "Tech/exploit" clips might have dedicated YouTube audience that Twitch metrics don't capture
**Idea**: Auto-tag clips (tech, funny, clutch, fail), track YouTube performance by tag, apply learned multipliers

#### 5. Title Quality Signal
**Problem**: User-generated clip titles vary wildly in quality. "clip123" vs "PEANUT FLYS AWAY!"
**Idea**: Simple heuristics — ALL CAPS bonus, question marks, exclamation points. Or LLM scoring.

#### 6. Clipper Reputation
**Problem**: Some community members consistently clip bangers. Others clip everything.
**Idea**: Track `clipper_id`, compute historical hit rate, weight their clips accordingly

#### 7. Diminishing Returns at Scale
**Problem**: A clip with 11K vs 12K views — does the extra 1K matter? Probably not.
**Idea**: `log(views)` instead of linear views, or soft cap

#### 8. Time-Slot Competition Adjustment
**Problem**: Prime time streams have more viewers = more clips = inflated velocity for mediocre content
**Idea**: Normalize velocity against concurrent clip velocity from same time window

### Priority Order (my take)
1. **Absolute view floor** — easiest win, filters noise
2. **Logarithmic age decay** — better math for 7-day window
3. **YouTube feedback loop** — the real unlock, but needs Phase 3 infra
4. Everything else — nice to have, test when basics are solid

## Open Questions

- What's the right split between individual streamer channels vs compilation channels?
- At what scale does the YouTube free quota become a blocker?
- Should compilation channels be Shorts-only or mix Shorts + long-form?
- Revenue share model for streamers who opt in?
- Is there a market for "clips channel as a service" or is the value in owning the channels?

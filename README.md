# Twitch-to-Shorts (AI-Oriented Repo Map)

Automated pipeline: Twitch clips -> score -> dedup -> download -> vertical crop -> upload to YouTube Shorts. This README is intentionally concise so an agent can orient quickly; source files are the ground truth.

**Read Order**
- `main.py` - entry point, orchestration, locking, analytics sync.
- `src/models.py` - dataclasses and config validation.
- `src/clip_filter.py` - scoring and ranking.
- `src/dedup.py` and `src/db.py` - dedup rules and SQLite storage.
- `src/video_processor.py` - ffmpeg crop, facecam detection, loudnorm, thumbnails.
- `src/youtube_uploader.py` - OAuth, upload, template rendering, thumbnail set.
- `src/youtube_analytics.py` and `src/youtube_reporting.py` - metrics and reach fallback.

**Core Flow (Single Streamer)**
1. Fetch clips (Twitch Helix).
2. Score and rank (views density + velocity; optional title quality; optional performance multiplier).
3. Dedup (DB presence, blocklist, overlap window, fail_count >= 3).
4. Apply duration and count caps (`max_clip_duration_seconds`, `max_clips_per_streamer`).
5. Rate limit uploads per streamer or channel (`upload_spacing_hours`, `max_uploads_per_window`).
6. Download -> process -> upload -> thumbnail.
7. Analytics sync (if enabled and not dry-run).

Note: analytics sync only runs after new clips proceed through the streamer pipeline (early returns skip it).

**Metrics Notes**
- `yt_impressions_ctr` is stored as a fraction (0.02 == 2%).
- Analytics API provides per-video metrics; reach metrics may be missing.
- Reporting API fallback (`channel_reach_basic_a1`) backfills impressions and CTR; reports can lag after job creation.
- Performance multiplier: avg CTR per streamer (requires >= 3 data points), baseline 2%, clamped to [0.5, 2.0] (`src/db.py`).

**Data Model (SQLite)**
`clips` key columns: `clip_id` (PK), `streamer`, `channel_key`, `title`, `view_count`, `created_at`, `posted_at`, `youtube_id`, `fail_count`, `yt_views`, `yt_estimated_minutes_watched`, `yt_avg_view_duration`, `yt_avg_view_percentage`, `yt_impressions`, `yt_impressions_ctr`, `yt_last_sync`.

`streamer_stats` key columns: `streamer` (PK), `avg_views_30d`, `clip_count_30d`, `last_updated`.

Auto-migrations add missing columns on connect.

**Config and Secrets**
`config.yaml` sections: `twitch`, `youtube`, `streamers[]`, `pipeline`, optional `captions`.

Env vars: `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, and `DEEPGRAM_API_KEY` (only if captions enabled).

**CI/CD**
- `tests.yml`: `python -m pytest tests/ -v` on push to `master` and PRs.
- `pipeline.yml`: cron `17 2/4 * * *` plus manual dispatch; runs `python main.py`, caches DB, refreshes token.

**Gotchas / Invariants**
- Stale lock: delete `data/pipeline.lock` only if no process is running.
- GPU encode: CI sets `DISABLE_GPU_ENCODE=1` for ffmpeg.
- Template keys are limited to `{title}`, `{streamer}`, `{game}`, `{game_name}`; unknown keys log warnings.

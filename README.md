# Twitch-to-Shorts

Automated pipeline: Twitch clips → score → dedup → download → 9:16 crop → YouTube Shorts upload. Runs on GitHub Actions cron (every 4h) or locally. ~3,300 lines Python, 14 source files, 304 tests.

## Agent Quick Start

This README is designed as your primary orientation doc — it covers the full architecture, data flow, schema, config surface, and known bug patterns so you can get productive fast.

- **Module Map** → find the right file. **Pipeline Flow** → trace any behavior. **Scoring Formula** and **Data Model** → understand the domain without reading source.
- **Gotchas & Invariants** section documents every bug pattern that has actually shipped. Worth scanning before changes to uploads, dedup, analytics, or ffmpeg.
- **Config Reference** has all knobs with defaults — useful alternative to reading `models.py` for config questions.
- Tests: `python -m pytest tests/ -v` (~1s). Test files mirror source: `src/foo.py` → `tests/test_foo.py`.
- Local runs: `python sync_db.py` first — CI DB is source of truth, local runs without sync will duplicate uploads.

## Module Map

| File | Lines | Purpose |
|---|---|---|
| `main.py` | 687 | Entry point, config loading, PID lockfile, orchestration loop, analytics sync |
| `src/models.py` | 118 | Dataclasses: `Clip`, `CaptionWord`, `FacecamConfig`, `StreamerConfig`, `PipelineConfig` (with validation) |
| `src/twitch_client.py` | 149 | Twitch Helix API: OAuth client-credentials, clip fetch, game name resolution, retry/rate-limit |
| `src/clip_filter.py` | 97 | Scoring: `views/duration + (views/age) * velocity_weight`, optional title quality bonus, performance multiplier |
| `src/dedup.py` | 86 | Multi-layer dedup: DB presence, blocklist, created_at overlap (30s window), VOD-based overlap, batch intra-overlap |
| `src/db.py` | 334 | SQLite WAL, schema + auto-migrations, CRUD, metrics sync, streamer stats, performance multiplier |
| `src/downloader.py` | 105 | yt-dlp wrapper: URL allowlist, atomic rename, handles TS→MP4 remux path variance |
| `src/video_processor.py` | 463 | ffmpeg: center crop or facecam+gameplay composite, YDIF-based facecam detection, 2-pass EBU R128 loudnorm, thumbnail extraction, GPU/CPU fallback, leading silence trim |
| `src/captioner.py` | 269 | Deepgram Nova-2 STT → ASS subtitles. Burned-in captions. Optional (needs `deepgram-sdk` + `DEEPGRAM_API_KEY`) |
| `src/youtube_uploader.py` | 445 | YouTube Data API v3: OAuth, resumable upload, A/B title templates, channel dedup via `playlistItems.list`, thumbnail set |
| `src/youtube_analytics.py` | 131 | YouTube Analytics API: per-video views/watch time/retention/reach, retry, shared `_to_int`/`_to_float`/`_normalize_ctr` |
| `src/youtube_reporting.py` | 285 | YouTube Reporting API: bulk CSV download for reach metrics (impressions + CTR), fallback when Analytics API lacks reach |
| `src/media_utils.py` | 63 | Shared constants (`FFMPEG`, `FFPROBE`), `is_valid_video()`, `safe_remove()`, `extract_audio()` |
| `sync_db.py` | 69 | Downloads CI database artifact locally via `gh run download` — **run before local pipeline** |

## Read Order for New Agents

1. This README (you are here)
2. `src/models.py` — all data structures, config validation rules
3. `main.py` — full pipeline flow: `main()` → `load_config()` → `validate_config()` → `acquire_lock()` → `run_pipeline()` → per-streamer loop → `_process_single_clip()` → analytics sync
4. `src/db.py` — schema, dedup queries, metrics storage
5. Then read whichever module you need to modify

## Pipeline Flow (per streamer)

```
1. fetch_clips(twitch_id, lookback_hours)         # twitch_client.py
2. filter_and_rank(clips, scoring_params)          # clip_filter.py — score = density + velocity * weight
3. filter_new_clips(conn, ranked)                  # dedup.py — DB + blocklist + overlap + VOD overlap
4. duration cap → max_clips_per_streamer cap       # main.py
5. recent_upload_count() → rate limit check        # db.py — upload_spacing_hours / max_uploads_per_window
6. get_game_names(game_ids)                        # twitch_client.py
7. get_authenticated_service()                     # youtube_uploader.py
8. FOR each clip:
   a. check_channel_for_duplicate()                # youtube_uploader.py — playlistItems.list (2 quota units)
   b. download_clip()                              # downloader.py — yt-dlp
   c. detect_leading_silence()                     # video_processor.py
   d. generate_captions() [if enabled]             # captioner.py — Deepgram → ASS
   e. crop_to_vertical()                           # video_processor.py — ffmpeg 1080x1920
   f. upload_short()                               # youtube_uploader.py — resumable upload
   g. insert_clip(conn, clip)                      # db.py — IMMEDIATELY after upload, before verify/thumbnail
   h. extract_thumbnail() + set_thumbnail()        # video_processor.py + youtube_uploader.py
9. update_streamer_stats()                         # db.py
10. _sync_streamer_metrics()                       # main.py — Analytics API + Reporting API fallback
```

## Data Model (SQLite)

**`clips`** — one row per clip (uploaded or failed):
```
clip_id TEXT PK, streamer TEXT, channel_key TEXT, title TEXT, view_count INT,
created_at TEXT, posted_at TEXT, youtube_id TEXT, fail_count INT DEFAULT 0,
duration REAL, vod_id TEXT, vod_offset INT,
yt_views INT, yt_estimated_minutes_watched REAL, yt_avg_view_duration REAL,
yt_avg_view_percentage REAL, yt_impressions INT, yt_impressions_ctr REAL,
yt_last_sync TEXT
```

**`streamer_stats`** — rolling 30-day aggregates:
```
streamer TEXT PK, avg_views_30d REAL, clip_count_30d INT, last_updated TEXT
```

Indexes: `streamer`, `posted_at`, `channel_key`, `(vod_id, vod_offset)`.

Auto-migrations add missing columns on `get_connection()`. WAL mode. `yt_impressions_ctr` stored as fraction (0.02 = 2%).

## Dedup Layers

1. **DB check**: clip_id exists with youtube_id OR fail_count >= 3
2. **Blocklist**: `data/blocklist.txt` (one clip_id per line)
3. **Created_at overlap**: same streamer within 30s window (DB + batch)
4. **VOD overlap**: same vod_id with overlapping `[vod_offset, vod_offset+duration)` ranges
5. **Channel dedup**: `playlistItems.list` title match before download (2 quota units)

## Video Processing

- **Crop modes**: center crop (`crop=ih*9/16:ih`) or facecam composite (top: camera, bottom: gameplay)
- **Facecam detection**: YDIF signal analysis at 25%/50%/75% timestamps; avg YDIF > 1.0 = real camera
- **Facecam modes**: `auto` (YDIF detect), `always`, `off` — per-streamer config
- **Audio**: 2-pass EBU R128 loudnorm (I=-14, TP=-1.5, LRA=11), AAC 192k
- **Encoding**: GPU first (h264_nvenc, CQ 23), CPU fallback (libx264, CRF 20). `DISABLE_GPU_ENCODE=1` skips GPU
- **Captions**: Deepgram Nova-2 → ASS subtitles, 3-word groups, uppercase, MarginV=400, silence-offset aligned
- **Thumbnails**: YDIF sampling at N timestamps, extract frame with highest motion, scale to width

## Scoring Formula

```python
age_term = log1p(age_hours) if log else age_hours
views = log1p(view_count) if log else view_count
velocity = views / age_term
density = views / duration
score = density + velocity * velocity_weight  # default weight=2.0
score *= (1 + title_quality_weight * title_quality)  # optional
score *= performance_multiplier  # from historical CTR, requires >=20 data points
```

## Analytics & Reach Metrics

- **Analytics API**: per-video views, estimatedMinutesWatched, averageViewDuration, averageViewPercentage. May include reach (impressions + CTR) if available
- **Reporting API fallback**: `channel_reach_basic_a1` CSV reports for impressions + CTR when Analytics API lacks reach data. Reports lag ~48h after job creation
- **CTR format difference**: Analytics API returns CTR as percentage (5.0 = 5%), `_normalize_ctr` divides by 100. Reporting API returns raw fraction (0.05 = 5%), no normalization needed
- **Performance multiplier**: `avg_ctr / 0.02 * 0.5 + 0.5`, clamped [0.5, 2.0], requires >=20 clips with CTR data. Returns 1.0 (no effect) otherwise
- **Sync cadence**: configurable `analytics_min_age_hours` (default 48h wait), `analytics_sync_interval_hours` (default 24h between syncs), `analytics_max_videos_per_run` (default 20)

## Upload Reliability (3-Layer Defense)

1. **DB before verify**: `insert_clip()` immediately after `upload_short()`, before `verify_upload()`/`set_thumbnail()`
2. **Artifact fallback**: cache miss triggers `gh run download --name clips-db` before pipeline runs
3. **Channel dedup**: `check_channel_for_duplicate()` pre-upload via `playlistItems.list`
4. **Token save guard**: `if [ -s file ]` prevents saving empty/missing token as secret
5. **DB save guard**: cache/artifact save only on `if: success()` — prevents stale DB overwriting good state

## Config Reference

**`config.yaml`** sections: `twitch`, `youtube`, `streamers[]`, `pipeline`, optional `captions`.

**Env vars**: `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` (required), `DEEPGRAM_API_KEY` (if captions enabled), `DISABLE_GPU_ENCODE` (CI).

**YouTube templates**: `{title}`, `{streamer}`, `{game}`, `{game_name}` — unknown keys log warnings, render as empty string.

**Per-streamer**: `name`, `twitch_id`, `youtube_credentials`, `facecam` (x/y/w/h/output_w), `facecam_mode` (auto/always/off), `privacy_status`, `category_id`, `extra_tags[]`, `captions` (bool override).

**Pipeline**: `max_clips_per_streamer` (6), `max_clip_duration_seconds` (60), `velocity_weight` (2.0), `clip_lookback_hours` (168), `min_view_count` (50), `age_decay` (linear/log), `view_transform` (linear/log), `title_quality_weight` (0.05), `upload_spacing_hours` (2), `max_uploads_per_window` (1), `analytics_enabled`, `captions_enabled`.

## CI/CD

- **`tests.yml`**: pytest + ruff + mypy on push to master and PRs. Python 3.12
- **`pipeline.yml`**: cron `17 2/4 * * *` (every 4h) + manual dispatch. Ubuntu, ffmpeg, `DISABLE_GPU_ENCODE=1`
  - DB: cache (primary) + artifact (fallback, 7-day retention)
  - Token: base64-encoded GitHub secret, saved `if: always()` with file-size guard
  - Credentials: ephemeral (restored from secrets, deleted after run)
  - Concurrency: `group: pipeline-run, cancel-in-progress: false`

## Dependencies

```
requirements.txt:       requests, google-api-python-client, google-auth-oauthlib, pyyaml, python-dotenv, yt-dlp
requirements-dev.txt:   + pytest, ruff, mypy, pre-commit, types-requests, types-PyYAML
requirements-captions.txt: deepgram-sdk>=3.0.0
System:                 ffmpeg, ffprobe, yt-dlp (PATH)
```

## Gotchas & Invariants

- **Stale lock**: `data/pipeline.lock` — PID-based, auto-recovers from stale PIDs. Only delete manually if certain no process is running
- **Local runs**: always `python sync_db.py` first — CI and local DBs are separate, local runs duplicate CI uploads without sync
- **yt-dlp remux (Windows)**: may write to `output_path` instead of `tmp_path` after TS→MP4 remux. Code checks both paths
- **`build(credentials=creds)` not `build(http=)`**: custom `http=` breaks resumable uploads (httplib2 treats YouTube's HTTP 308 "Resume Incomplete" as a redirect)
- **Template rendering**: `format_map()` with `_TemplateDict` — unknown keys return empty string, logged as warnings
- **OAuth scopes**: `youtube.upload`, `youtube.readonly`, `yt-analytics.readonly`. Missing scopes → fatal error in CI (non-interactive), interactive re-auth locally
- **Token refresh**: `RefreshError` → interactive re-auth if TTY, fatal error in CI. Token saved back to secret after every run
- **Consecutive 403s**: 3 consecutive upload failures skip remaining clips for that streamer
- **Quota errors**: `QuotaExhaustedError` breaks the entire pipeline run (all streamers)
- **Auth errors**: `AuthenticationError` breaks the current streamer, pipeline raises `RuntimeError`

## Instagram Reels Integration

The pipeline can optionally upload processed clips to Instagram Reels in addition to YouTube Shorts. The existing 9:16 MP4 output is already compatible with Instagram's requirements.

### Prerequisites

1. **Instagram Business/Creator Account** linked to a Facebook Page
2. **Meta Developer App** at [developers.facebook.com](https://developers.facebook.com)
3. Required permissions: `instagram_content_publish`, `instagram_basic`, `pages_show_list`

### Token Setup

1. Generate a short-lived token via [Graph API Explorer](https://developers.facebook.com/tools/explorer/)
2. Exchange for a long-lived token (60-day validity):
   ```
   GET https://graph.instagram.com/access_token?grant_type=ig_exchange_token&client_secret={APP_SECRET}&access_token={SHORT_LIVED_TOKEN}
   ```
3. Save credentials as JSON:
   ```json
   {
     "access_token": "IGQV...",
     "ig_user_id": "17841400000000000",
     "token_expiry": "2026-04-11T00:00:00Z"
   }
   ```
4. Base64-encode and set as GitHub secret:
   ```bash
   base64 -w0 credentials/theburntpeanut_instagram.json | gh secret set INSTAGRAM_TOKEN_THEBURNTPEANUT
   ```

### Configuration

In `config.yaml`:
```yaml
instagram:
  caption_templates:
    - "{title}"
    - "{title} | {streamer}"
  hashtags:
    - "#gaming"
    - "#twitch"
    - "#clips"

pipeline:
  instagram_enabled: true

streamers:
  - name: "TheBurntPeanut"
    instagram_credentials: "credentials/theburntpeanut_instagram.json"
```

### How It Works

- Instagram uses a **pull model**: the API fetches video from a public URL
- Pipeline creates a temporary GitHub Release as the public URL source
- Flow: create temp release -> create reel container -> poll status -> publish -> delete temp release
- Instagram upload is **independent** of YouTube: IG failures never block YT uploads
- Long-lived tokens auto-refresh when within 7 days of expiry
- Instagram rate limit: 25 posts per 24 hours (enforced by API, tracked in DB)

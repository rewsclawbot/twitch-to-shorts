# Twitch-to-Shorts

Automated pipeline: Twitch clips → score → dedup → download → vertical crop → YouTube Shorts upload. Unattended via GitHub Actions cron.

## Architecture

| File | Lines | Purpose | Key Exports |
|------|-------|---------|-------------|
| `main.py` | 610 | Orchestration, config, locking, analytics sync | `run_pipeline()`, `load_config()`, `acquire_lock()` |
| `src/models.py` | 104 | Dataclasses, validation | `Clip`, `FacecamConfig`, `StreamerConfig`, `PipelineConfig` |
| `src/twitch_client.py` | 140 | Twitch Helix API (clips, games) | `TwitchClient` |
| `src/clip_filter.py` | 100 | Scoring + ranking | `filter_and_rank()`, `compute_score()` |
| `src/dedup.py` | 64 | DB/overlap/blocklist dedup | `filter_new_clips()` |
| `src/downloader.py` | 73 | yt-dlp wrapper, atomic .part→rename | `download_clip()` |
| `src/video_processor.py` | 444 | ffmpeg: crop, facecam, loudnorm, thumbnails | `crop_to_vertical()`, `extract_thumbnail()` |
| `src/media_utils.py` | 22 | Shared FFMPEG/FFPROBE constants, validation | `FFMPEG`, `FFPROBE`, `is_valid_video()` |
| `src/youtube_uploader.py` | 427 | YouTube Data API v3: upload, templates, tags, channel dedup | `upload_short()`, `build_upload_title()`, `QuotaExhaustedError` |
| `src/youtube_analytics.py` | 79 | YouTube Analytics API (dormant) | `fetch_video_metrics()` |
| `src/db.py` | 269 | SQLite WAL, schema, migrations, metrics | `get_connection()`, `insert_clip()`, `get_streamer_performance_multiplier()` |

## Pipeline Data Flow

1. **Fetch** — `TwitchClient.fetch_clips()` paginated Helix API, up to 500 clips per streamer within `clip_lookback_hours`
2. **Score** — `filter_and_rank()`: view density + velocity × weight, optional log transforms, title quality bonus, performance multiplier (if analytics enabled)
3. **Dedup** — `filter_new_clips()`: skip if already in DB (with youtube_id or fail_count≥3), in blocklist, or within 30s overlap window
4. **Rate limit** — `recent_upload_count()`: max 1 upload per streamer per 2h window
5. **Download** — `download_clip()`: yt-dlp → `.part` file → `os.replace()` atomic rename
6. **Process** — `crop_to_vertical()`:
   - Probe dimensions, detect leading silence (trim up to 5s)
   - Facecam detection: YDIF signal analysis at 25/50/75% timestamps, threshold >1.0
   - Composite filter (facecam top 20% + gameplay bottom 80%) or center-crop
   - 2-pass EBU R128 loudnorm (measure → apply with `linear=true`)
   - GPU encode (h264_nvenc) → CPU fallback (libx264)
   - Atomic write: `.tmp` → `os.replace()`
7. **Upload** — `upload_short()`: resumable upload, chunk retry (4 attempts, exponential backoff), quota detection
8. **Verify** — `verify_upload()`: confirm status is `uploaded`/`processed`
9. **Thumbnail** — `extract_thumbnail()`: YDIF-based best-frame selection, `set_thumbnail()`
10. **Analytics sync** — (when enabled) `_sync_streamer_metrics()` → `fetch_video_metrics()` → `update_youtube_metrics()`

## DB Schema

**`clips`** — one row per Twitch clip encountered

| Column | Type | Notes |
|--------|------|-------|
| `clip_id` | TEXT PK | Twitch clip ID |
| `streamer` | TEXT | Streamer name |
| `title`, `view_count`, `created_at` | TEXT/INT/TEXT | From Twitch |
| `posted_at` | TEXT | UTC ISO when uploaded |
| `youtube_id` | TEXT | NULL until uploaded |
| `fail_count` | INT | Incremented on failure, skip at ≥3 |
| `yt_views`, `yt_impressions`, `yt_impressions_ctr` | INT/INT/REAL | YouTube Analytics |
| `yt_estimated_minutes_watched`, `yt_avg_view_duration`, `yt_avg_view_percentage` | REAL | YouTube Analytics |
| `yt_last_sync` | TEXT | Last analytics sync timestamp |

**`streamer_stats`** — rolling 30-day aggregates per streamer

| Column | Type |
|--------|------|
| `streamer` | TEXT PK |
| `avg_views_30d` | REAL |
| `clip_count_30d` | INT |
| `last_updated` | TEXT |

Indexes: `idx_clips_streamer`, `idx_clips_posted`. Auto-migration adds missing columns on connect.

## Config Reference

`config.yaml` top-level keys:

- **`twitch`** — credentials read from env (`TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`), not from file
- **`youtube`** — `client_secrets_file`, `title_template`/`title_templates` (A/B), `description_template`/`description_templates`, `extra_tags`, `thumbnail_enabled`/`thumbnail_samples`/`thumbnail_width`
- **`streamers[]`** — `name`, `twitch_id`, `youtube_credentials`, `facecam` (x/y/w/h/output_w), `facecam_mode` (auto|always|off), `privacy_status`, `category_id`, `extra_tags`
- **`pipeline`** — `max_clips_per_streamer`, `max_clip_duration_seconds`, `velocity_weight`, `clip_lookback_hours`, `min_view_count`, `age_decay` (linear|log), `view_transform` (linear|log), `title_quality_weight`, `tmp_dir`, `db_path`, `log_file`, `upload_spacing_hours`, `max_uploads_per_window`, `analytics_enabled`, `analytics_min_age_hours`, `analytics_sync_interval_hours`, `analytics_max_videos_per_run`

Template placeholders: `{title}`, `{streamer}`, `{game}`, `{game_name}`

## Patterns

**Error handling**
- Twitch: 3 retries with token refresh on 401, rate-limit backoff on 429
- YouTube upload: 4-attempt exponential backoff per chunk, `QuotaExhaustedError` breaks entire run
- YouTube 403: consecutive counter, 3 consecutive 403s skips remaining clips for that streamer
- ffmpeg: GPU attempt → CPU fallback, 300s timeout, killed on timeout

**Atomic writes**
- Downloads: `.part` → `os.replace()` to final path
- ffmpeg output: `.tmp` → `os.replace()` to final path
- Credentials: `os.open()` with `O_CREAT|O_TRUNC` + mode 0o600

**Auth**
- Twitch: client credentials grant, token cached with monotonic expiry (refresh 60s early)
- YouTube: OAuth2 with refresh token, auto-refresh on expiry, interactive flow if no token, scope validation on load

**Locking**
- PID-based lockfile at `data/pipeline.lock`
- Atomic creation via `O_CREAT|O_EXCL`
- Stale lock detection: checks if PID is alive (Windows `OpenProcess`/`GetExitCodeProcess`, POSIX `kill(0)`)
- Released in `finally` block

**Scoring formula**
- `score = (views/duration) + (views/age) × velocity_weight`
- Optional: log age decay, log view transform, title quality bonus (0-1 × weight)
- Performance multiplier from past CTR data (baseline 2%, clamped [0.5, 2.0], requires ≥3 data points)

## Testing

```
python -m pytest tests/ -v
```

183 tests across 8 test files. Uses `conftest.py` with shared fixtures. All modules have test coverage including `main.py`, `video_processor.py`, `twitch_client.py`, and subprocess safety tests.

## CI/CD

- **Tests workflow** (`tests.yml`): on push to master + PRs. Python 3.12, `pytest tests/ -v`
- **Pipeline workflow** (`pipeline.yml`): cron `17 */4 * * *` (every 4h at :17) + manual dispatch
  - Concurrency group `pipeline-run`, cancel-in-progress: false
  - Secrets: `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `YOUTUBE_CLIENT_SECRETS`, `YOUTUBE_TOKEN_THEBURNTPEANUT`, `GH_PAT`
  - Credentials restored from base64-encoded secrets, token re-saved after run
  - DB persisted via `actions/cache` (key: `clips-db-v1`, delete-before-save pattern)
  - `DISABLE_GPU_ENCODE=1` (no CUDA in GitHub Actions)

## Dependencies

Python 3.12+, ffmpeg/ffprobe on PATH, yt-dlp. See `requirements.txt` for pinned versions.

Runtime: requests, google-api-python-client, google-auth-oauthlib, pyyaml, python-dotenv, yt-dlp
Dev: pytest, ruff, mypy, pre-commit

## Gotchas

- **`analytics_enabled` must stay `false`** until first successful uploads exist — the performance multiplier needs ≥3 data points with CTR data
- **Variable scoping in except blocks**: Codex previously introduced a `title` vs `full_title` NameError in upload error handlers — always verify variable names in except blocks match the enclosing scope
- **Stale lock**: if pipeline crashes without cleanup, delete `data/pipeline.lock` after confirming no process is running
- **CPU-only encode**: set `DISABLE_GPU_ENCODE=1` when no CUDA GPU available
- **Template `{game}` vs `{game_name}`**: both work (aliased in `_TemplateDict`)
- **Clip duration check**: allows 0.5s overage (60.5s) since YouTube Shorts limit is ~60s
- **Dedup fail threshold**: clips with `fail_count ≥ 3` are treated as permanently failed and skipped
- **Token scope migration**: if credentials file has fewer scopes than `SCOPES` list, pipeline errors with "missing required scopes" — delete token and re-auth
- **A/B template selection**: deterministic via `md5(clip_id) % len(templates)`, not random

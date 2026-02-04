# Twitch-to-Shorts

Automated pipeline that pulls Twitch clips, scores and deduplicates them, reformats to 9:16,
and uploads as YouTube Shorts. Designed for unattended runs with a small SQLite state
database and repeatable config.

## Features
- Twitch clip fetch + game name resolution
- Scoring by view density and velocity
- Deduplication (DB, overlap window, blocklist)
- Vertical crop with optional facecam overlay
- Loudness normalization + leading silence trim
- YouTube Shorts upload + verification
- SQLite stats for per-streamer rollups

## Requirements
- Python 3.12+
- ffmpeg + ffprobe available on PATH
- `yt-dlp` (installed via `pip`)

## Setup
1. Install dependencies:
```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
```

2. Set Twitch credentials (env or `.env`):
```powershell
$env:TWITCH_CLIENT_ID="..."
$env:TWITCH_CLIENT_SECRET="..."
```

3. Place your YouTube OAuth client secrets at:
`credentials/client_secret.json`

4. Create a YouTube token for each streamer (interactive, one-time):
```powershell
python -c "from src.youtube_uploader import get_authenticated_service; get_authenticated_service('credentials/client_secret.json','credentials/theburntpeanut_youtube.json')"
```

5. Configure streamers and pipeline settings in `config.yaml`.

## Run
```powershell
python main.py
```

Dry run (processes clips, skips upload):
```powershell
python main.py --dry-run
```

## Tests
```powershell
python -m pytest tests/ -v
```

## Configuration
`config.yaml` supports optional templating and facecam controls:
- `youtube.title_template` and `youtube.description_template` support placeholders:
  - `{title}`, `{streamer}`, `{game}`
- `youtube.title_templates` / `youtube.description_templates` allow A/B templates (list of strings)
- `youtube.extra_tags` adds tags to every upload
- `streamers[].extra_tags` adds per-streamer tags
- `youtube.thumbnail_enabled` generates and uploads thumbnails
- `youtube.thumbnail_samples` controls sampling density (default 8)
- `youtube.thumbnail_width` controls thumbnail width (default 1280)
- `streamers[].facecam_mode`:
  - `auto` (default): detect facecam before overlay
  - `always`: always overlay facecam (skip detection)
  - `off`: never overlay facecam
- Scoring controls:
  - `pipeline.min_view_count`: minimum Twitch views before scoring
  - `pipeline.age_decay`: `linear` or `log`
  - `pipeline.view_transform`: `linear` or `log`
  - `pipeline.title_quality_weight`: 0.0 disables title bonus (keep small, e.g. 0.05)
- Analytics sync:
  - `pipeline.analytics_enabled`: enable YouTube Analytics sync
  - `pipeline.analytics_min_age_hours`: minimum age before syncing
  - `pipeline.analytics_sync_interval_hours`: re-sync interval
  - `pipeline.analytics_max_videos_per_run`: safety cap per run

## Files and Data
- `data/clips.db` stores processed clips and stats
- `data/tmp/` holds intermediate video files
- `data/pipeline.lock` prevents concurrent runs
- `credentials/` contains OAuth secrets and tokens (gitignored)

## Notes
- To force CPU encoding, set `DISABLE_GPU_ENCODE=1`.
- If you see a stale lock error, delete `data/pipeline.lock` after confirming no run is active.

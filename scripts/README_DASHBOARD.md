# Daily Dashboard Script

## Overview

`scripts/daily_dashboard.py` generates a concise daily performance report for the ClipFrenzy YouTube Shorts channel.

## Features

The report includes:

1. **Upload Summary (last 24h)** - Recent uploads with titles and YouTube links
2. **Analytics Snapshot** - Total views, best performing short, average metrics
3. **Pipeline Health** - Database stats, queue status, run history, failures
4. **Trending Games** - Top trending games and which streamers play them
5. **Growth Metrics** - Daily and weekly comparison metrics

## Usage

### Command Line

```bash
# Use default paths (data/clips.db, config.yaml)
.venv/bin/python scripts/daily_dashboard.py

# Or specify custom paths
.venv/bin/python scripts/daily_dashboard.py path/to/clips.db path/to/config.yaml
```

### OpenClaw Cron Integration

Add to your OpenClaw cron configuration to get daily reports via Telegram:

```yaml
# Send daily dashboard at 9am CST
- schedule: "0 9 * * *"
  command: "cd ~/Projects/twitch-to-shorts-claw && .venv/bin/python scripts/daily_dashboard.py"
  channel: telegram
  timezone: America/Chicago
  label: clipfrenzy-daily-report
```

## Output Format

The script outputs plain text formatted for Telegram with:
- Emoji section headers
- Bullet points (no markdown tables)
- Formatted numbers (e.g., "10,000" instead of "10000")
- Clickable YouTube Shorts URLs

## Example Output

```
📊 ClipFrenzy Daily Dashboard
📅 2026-02-16 00:25 UTC

🎬 Upload Summary (Last 24h)
  • 2 short(s) uploaded
  • TheBurntPeanut: INSANE Play!
    https://youtube.com/shorts/yt123
  • xQc: This is CRAZY
    https://youtube.com/shorts/yt456

📈 Analytics Snapshot
  • Total channel views: 10,000
  • Total shorts: 12
  • Avg views/short: 833
  • Avg retention: 65.5%
  • Best performing: LEGENDARY moment (5,000 views)
    https://youtube.com/shorts/yt789

⚙️ Pipeline Health
  • Total clips in DB: 34
  • Uploaded: 12
  • Queue: 3 pending, 0 expired
  • Last successful run: 4h ago
  • ⚠️ Failed uploads (24h): 1

🔥 Trending Games
  Top 5 trending games:
  1. League of Legends (our streamers: TheBurntPeanut)
  2. Valorant (our streamers: xQc)
  3. Minecraft
  4. Grand Theft Auto V
  5. Counter-Strike

📊 Growth Metrics
  • Views today: 4,000
  • Views yesterday: 3,000
  • Change: 📈 +33.3%
  • Uploads this week: 8
  • Uploads last week: 12
```

## Data Sources

- **clips.db** - SQLite database with clips, analytics, queue, and run history
- **config.yaml** - Streamer configuration
- **data/trending_cache.json** - Cached Twitch trending games (if available)

## Error Handling

- Gracefully handles missing data (new channel, no analytics yet)
- Shows "No data available" for sections without data
- Returns exit code 1 on errors (prints error message to stderr)

## Testing

Run tests with:

```bash
.venv/bin/python -m pytest tests/test_daily_dashboard.py -v
```

Tests cover:
- All report sections with full and minimal data
- Helper functions (time formatting, URL generation)
- Config and cache loading
- Database queries
- Full report generation

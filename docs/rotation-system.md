# Automated Streamer Rotation System

## Overview

The rotation system automatically evaluates current streamers' performance and replaces underperformers with high-potential discoveries from the streamer discovery module.

## Components

### `scripts/rotate_streamers.py`

Main rotation script with the following features:

**Health Evaluation:**
- Queries clips DB for upload count, avg views, and clip availability (14-day lookback)
- Calculates a 0-1 health score based on:
  - Clip availability (40%): Fresh clips from Twitch
  - Upload activity (30%): How many we actually uploaded
  - YouTube performance (30%): Average views on uploads

**Discovery Integration:**
- Uses `discover_streamers.py` to find replacement candidates
- Filters out streamers already in config
- Scores candidates based on clip virality potential (0-100 scale)

**Smart Rotation Logic:**
- Protects top 3 performers (never rotated out)
- Only rotates streamers below health threshold (default 0.4)
- Maximum 2 rotations per run (avoids drastic changes)
- Only swaps when replacement has better potential
- Logs all changes to `data/rotation_log.json`

**Safety Features:**
- Dry-run mode by default (`--execute` flag required for real changes)
- Automatic config.yaml backup before modifications
- New streamers inherit standard settings:
  - `youtube_credentials`: Same as existing streamers
  - `privacy_status: public`
  - `category_id: '20'`
  - Default facecam coords: x=0.75, y=0.02, w=0.22, h=0.30

### `scripts/run-rotation.sh`

Cron-compatible wrapper script for automated execution:
- Activates venv
- Loads environment variables
- Runs rotation in execute mode
- Captures output for cron logs

### `tests/test_rotate_streamers.py`

Comprehensive test suite (22 tests) covering:
- Health score calculation logic
- Streamer evaluation with DB queries
- Rotation decision rules (protection, limits, thresholds)
- Config backup and modification (dry-run vs execute)
- Rotation logging

## Usage

### Manual Execution

```bash
# Dry-run (default) - see what would change
python scripts/rotate_streamers.py

# Execute mode - actually apply changes
python scripts/rotate_streamers.py --execute

# Custom threshold
python scripts/rotate_streamers.py --execute --threshold 0.3
```

### Automated (Cron)

```bash
# Run weekly rotation (Sunday 3am)
0 3 * * 0 /path/to/twitch-to-shorts-claw/scripts/run-rotation.sh >> /var/log/rotation.log 2>&1
```

## Health Score Components

**Perfect health (1.0):**
- 10+ clips available from Twitch
- 5+ uploads in last 14 days
- 500+ average YouTube views

**Unhealthy (0.0):**
- 0 clips available
- 0 uploads
- 0 views

**Example:**
- Streamer with 8 clips available, 3 uploads, 300 avg views = ~0.5 health score

## Rotation Decision Flow

1. Evaluate all current streamers → health scores
2. Protect top 3 performers (never eligible for rotation)
3. Identify underperformers (health < threshold)
4. Run discovery to find replacements
5. Match each underperformer with best available candidate
6. Only rotate if candidate's potential > current health
7. Maximum 2 rotations per run
8. Apply changes and log to rotation_log.json

## Rotation Log Format

```json
{
  "rotations": [
    {
      "timestamp": "2024-02-15T18:30:00Z",
      "dry_run": false,
      "changes": [
        {
          "removed": {
            "name": "InactiveStreamer",
            "twitch_id": "12345",
            "health_score": 0.15,
            "upload_count": 0,
            "avg_youtube_views": 0
          },
          "added": {
            "name": "RisingStreamer",
            "twitch_id": "67890",
            "discovery_score": 85,
            "clip_count": 20,
            "avg_views": 1200
          }
        }
      ]
    }
  ]
}
```

## Testing

```bash
# Run rotation tests only
.venv/bin/python -m pytest tests/test_rotate_streamers.py -v

# Run full test suite
.venv/bin/python -m pytest tests/ -x -q
```

## Configuration

Key constants in `rotate_streamers.py`:
- `MIN_PROTECTED_STREAMERS = 3` - Top performers never rotated
- `MAX_ROTATIONS_PER_RUN = 2` - Maximum changes per run
- `HEALTH_THRESHOLD = 0.4` - Score below this triggers rotation consideration
- `LOOKBACK_DAYS = 14` - Days to analyze for metrics

## Integration with Existing Systems

- Uses `src.db.get_connection()` for database access
- Uses `src.twitch_client.TwitchClient` for Twitch API
- Uses `scripts.discover_streamers` for finding replacements
- Reads/writes `config.yaml` using PyYAML
- Compatible with existing pipeline database schema

# Automated Streamer Discovery

The streamer discovery module automatically finds promising Twitch streamers to add to your YouTube Shorts pipeline based on clip virality potential.

## Overview

The discovery algorithm analyzes:
- **Live streamers** in top trending games
- **Viewer count** (1K-50K sweet spot — big enough for quality clips, not oversaturated)
- **Clip creation rate** (more community clips = more entertaining content)
- **Average clip views** (higher engagement = viral potential)
- **Game diversity** (multi-game streamers = broader audience appeal)
- **Shorts-ready percentage** (clips ≤30s that fit YouTube Shorts format)

## Usage

### Basic Discovery

Find streamers across all top games:

```bash
.venv/bin/python scripts/discover_streamers.py
```

This will:
1. Search top 10 trending games on Twitch
2. Find live streamers with 1K-50K viewers
3. Analyze their recent clips (last 7 days)
4. Score and rank by viral potential
5. Output results to `data/streamer_recommendations.json`

### Game-Specific Search

Focus on a specific game:

```bash
.venv/bin/python scripts/discover_streamers.py --game "Fortnite"
```

### Custom Viewer Range

Adjust the viewer count sweet spot:

```bash
.venv/bin/python scripts/discover_streamers.py --min-viewers 2000 --max-viewers 30000
```

### Analyze More Streamers

Increase the number of candidates to analyze:

```bash
.venv/bin/python scripts/discover_streamers.py --max-results 50
```

## Scoring Methodology

Each streamer receives a score from 0-100 based on:

| Component | Weight | What It Measures |
|-----------|--------|------------------|
| **Clip Volume** | 25 pts | Clips per day (target: 10+/week) |
| **Avg Views** | 25 pts | Engagement per clip (target: 2K+) |
| **Peak Virality** | 15 pts | Highest clip views (target: 50K+) |
| **Game Diversity** | 10 pts | Unique games played (target: 5+) |
| **Shorts-Ready %** | 25 pts | Clips ≤30s (target: 70%+) |

**Total:** 100 points

### What Makes a Good Candidate?

- **Score 80-100:** Excellent — high viral potential
- **Score 60-79:** Good — solid content with engagement
- **Score 40-59:** Moderate — may require manual review
- **Score <40:** Poor — likely not worth adding

## Output Format

### JSON Output

The script saves detailed results to `data/streamer_recommendations.json`:

```json
{
  "generated_at": "2024-02-15T12:00:00Z",
  "criteria": {
    "game": null,
    "min_viewers": 1000,
    "max_viewers": 50000
  },
  "streamers": [
    {
      "user_id": "123456789",
      "user_login": "coolstreamer",
      "user_name": "CoolStreamer",
      "current_viewers": 5000,
      "current_game": "Valorant",
      "clip_count": 50,
      "avg_views": 1500.5,
      "max_views": 25000,
      "total_views": 75025,
      "unique_games": 4,
      "top_games": ["Valorant", "Fortnite", "Apex Legends"],
      "clip_frequency": 7.1,
      "shorts_ready_pct": 65.0,
      "avg_duration": 22.3,
      "score": 78.5
    }
  ]
}
```

### Console Output

The script prints a formatted summary with:
- Top 10 recommendations with key metrics
- Ready-to-paste YAML config blocks (top 5)

Example output:

```
================================================================================
STREAMER DISCOVERY RESULTS
================================================================================

Generated: 2024-02-15 12:00:00
Criteria: 1,000 - 50,000 viewers

--------------------------------------------------------------------------------
TOP RECOMMENDATIONS
--------------------------------------------------------------------------------

1. CoolStreamer (Score: 78.5/100)
   Twitch: twitch.tv/coolstreamer
   Current: 5,000 viewers, playing Valorant
   Clips: 50 in last 7 days (7.1/day)
   Engagement: 1,501 avg views, 25,000 peak
   Games: 4 unique, top: Valorant, Fortnite
   Shorts-ready: 65% of clips ≤30s

--------------------------------------------------------------------------------
YAML CONFIG (Top 5)
--------------------------------------------------------------------------------

streamers:
  - name: CoolStreamer
    twitch_id: "123456789"
    youtube_credentials: credentials-coolstreamer.json
    facecam:
      x: 0.75
      y: 0.02
      w: 0.22
      h: 0.30
    # 50 clips/week, 1501 avg views, 4 games
    # Top games: Valorant, Fortnite
```

## Integration Workflow

1. **Run discovery:**
   ```bash
   .venv/bin/python scripts/discover_streamers.py --max-results 30
   ```

2. **Review results:**
   - Check `data/streamer_recommendations.json`
   - Look for scores 60+ for good candidates
   - Watch a few clips from top recommendations

3. **Add to config:**
   - Copy YAML block from console output
   - Paste into your `streamers` config
   - Set up YouTube credentials for the streamer

4. **Run pipeline:**
   - The pipeline will automatically fetch clips for new streamers
   - Monitor first uploads to verify quality

## API Methods

The discovery module extends `TwitchClient` with new methods:

### `get_streams(game_id=None, first=20)`

Get currently live streams, optionally filtered by game.

```python
from src.twitch_client import TwitchClient

client = TwitchClient(client_id, client_secret)

# Get top 20 live streams
streams = client.get_streams(first=20)

# Filter by game
fortnite_streams = client.get_streams(game_id="33214", first=50)
```

**Returns:** List of dicts with stream data (user_id, user_name, viewer_count, game_name, etc.)

### `get_user_by_login(login)`

Get user info by Twitch username.

```python
user = client.get_user_by_login("coolstreamer")
# Returns: {"id": "123", "login": "coolstreamer", "display_name": "CoolStreamer", ...}
```

## Tips

- **Run during peak hours** (evenings/weekends) to find more live streamers
- **Try different games** to diversify your content
- **Lower min-viewers** to discover smaller, growing streamers before they blow up
- **Check current_game** in results — streamers with variety content may be better
- **Manual verification** — Always watch a few clips before adding a streamer

## Troubleshooting

### "No streamers found matching criteria"

- Try adjusting `--min-viewers` and `--max-viewers`
- Run during peak streaming hours
- Check if the game name is spelled correctly (case-sensitive)

### "Only X clips, skipping (need at least 5)"

The streamer doesn't have enough recent clips to analyze. This is normal — the algorithm filters out low-activity streamers.

### Rate limiting

If you hit Twitch API rate limits:
- The client automatically waits and retries
- Reduce `--max-results` to analyze fewer streamers
- Wait a few minutes and try again

## Environment Variables

Required:
- `TWITCH_CLIENT_ID` — Your Twitch API client ID
- `TWITCH_CLIENT_SECRET` — Your Twitch API client secret

Get these from: https://dev.twitch.tv/console/apps

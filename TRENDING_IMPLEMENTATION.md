# Trending Games Detector Implementation

## Overview

Implemented a trending games boost system that increases clip scores based on current game popularity on Twitch. Clips from trending games get multipliers applied to their scores, improving their ranking in the upload queue.

## What Was Built

### 1. `src/trending.py` - Core Module

**Functions:**
- `get_trending_games(twitch_client)` - Fetches top 20 games from Twitch API with 6-hour caching
- `get_trending_multiplier(game_name, twitch_client)` - Returns multiplier for a single game
- `get_trending_multipliers(twitch_client)` - Returns dict of all trending multipliers (efficient batch lookup)

**Caching:**
- Cache stored in `data/trending_cache.json`
- TTL: 6 hours (21,600 seconds)
- Handles corrupted cache gracefully
- Auto-refreshes on expiration

**Multiplier Tiers:**
- Top 5 games: **1.5x**
- Top 6-10: **1.3x**
- Top 11-20: **1.15x**
- Not trending: **1.0x**

### 2. `src/twitch_client.py` - API Integration

**Added Method:**
```python
def get_top_games(self, limit: int = 20) -> list[dict]:
    """Get top trending games from Twitch.
    
    Returns:
        List of dicts with keys: id, name, rank (1-indexed)
    """
```

- Uses existing auth/retry logic
- Validates limit (1-100)
- Returns ranked list

### 3. `src/clip_filter.py` - Scoring Integration

**Changes:**
- Added `trending_multipliers` parameter to `compute_score()`
- Added `trending_multipliers` parameter to `filter_and_rank()`
- Case-insensitive game name matching
- Multipliers stack with existing `game_multipliers`

**Application Order:**
```
base_score * game_multiplier * trending_multiplier * streamer_multiplier
```

### 4. `src/pipeline.py` - Pipeline Integration

**Changes:**
- Fetches trending multipliers **once per pipeline run** (efficient)
- Passes them through `_process_streamer()` → `filter_and_rank()`
- Applied to both initial ranking and audio re-ranking
- Graceful fallback on fetch failure

### 5. `src/models.py` - Config

**Added Field:**
```python
class PipelineConfig:
    trending_boost_enabled: bool = False  # New field
```

### 6. `config.yaml` - Configuration

**Added Option:**
```yaml
pipeline:
  trending_boost_enabled: true  # Enable trending boost
```

### 7. `tests/test_trending.py` - Comprehensive Tests

**Test Coverage:**
- ✅ Cache loading/saving
- ✅ Cache expiration/refresh
- ✅ Corrupted cache handling
- ✅ API failure handling
- ✅ Multiplier tier logic
- ✅ Case-insensitive matching
- ✅ Integration with `compute_score()`
- ✅ Stacking with game multipliers
- ✅ TwitchClient.get_top_games() method
- ✅ Empty/invalid inputs

**21 tests added, all passing**

## How It Works

### Pipeline Flow

1. **Pipeline Start** (`_run_pipeline_inner`)
   - If `trending_boost_enabled=true`, fetch trending multipliers once
   - Cache result for 6 hours to avoid API spam

2. **Per-Streamer Processing** (`_process_streamer`)
   - Fetch clips from Twitch
   - Pass trending multipliers to `filter_and_rank()`

3. **Clip Scoring** (`compute_score`)
   - Calculate base score (density + velocity)
   - Apply duration bonus, audio excitement, etc.
   - **Apply trending multiplier if game matches**
   - Apply streamer performance multiplier

4. **Result**
   - Clips from trending games rank higher
   - More likely to be uploaded during posting windows

### Example

**Without trending boost:**
```
Clip A (Stardew Valley, 1000 views, 20s):  score = 100
Clip B (League of Legends, 1000 views, 20s): score = 100
```

**With trending boost:**
```
Clip A (Stardew Valley, 1000 views, 20s):  score = 115  (rank 20, 1.15x)
Clip B (League of Legends, 1000 views, 20s): score = 150  (rank 1, 1.5x)
```

Clip B now ranks significantly higher and is more likely to be uploaded first.

## Testing

### Run Trending Tests Only
```bash
.venv/bin/python -m pytest tests/test_trending.py -v
```

### Run Full Test Suite
```bash
.venv/bin/python -m pytest tests/ -x -q
```

**Results:**
- 602 total tests passed ✅
- 2 skipped
- 0 failures

## Configuration

### Enable Trending Boost

In `config.yaml`:
```yaml
pipeline:
  trending_boost_enabled: true
```

### Disable Trending Boost

Set to `false` or remove the field (defaults to `false`).

## Cache Management

### Cache Location
```
data/trending_cache.json
```

### Cache Format
```json
{
  "timestamp": 1676554321.123,
  "games": [
    {"id": "123", "name": "League of Legends", "rank": 1},
    {"id": "456", "name": "Fortnite", "rank": 2},
    ...
  ]
}
```

### Manual Cache Refresh

Delete the cache file to force a fresh fetch:
```bash
rm data/trending_cache.json
```

## Performance

- **API calls**: Max 1 per 6 hours per pipeline run
- **Overhead**: Negligible (dict lookup per clip)
- **Cache size**: ~2KB

## Future Enhancements

Potential improvements (not currently implemented):

1. **Configurable multipliers** - Allow custom tier values in config
2. **YouTube trending** - Fetch from YouTube API as well
3. **Combined trending score** - Weighted average of Twitch + YouTube
4. **Per-streamer trending** - Different multipliers based on streamer's audience
5. **Time-decay trending** - Reduce multiplier for games trending >7 days

## Files Modified

1. `src/trending.py` - **NEW**
2. `src/twitch_client.py` - Added `get_top_games()` method
3. `src/clip_filter.py` - Added trending multiplier support
4. `src/pipeline.py` - Fetch and pass trending data
5. `src/models.py` - Added `trending_boost_enabled` config field
6. `config.yaml` - Enabled trending boost
7. `tests/test_trending.py` - **NEW** - 21 comprehensive tests

## Summary

The trending games detector is fully implemented, tested, and integrated into the pipeline. It:

- ✅ Fetches top 20 games from Twitch API
- ✅ Caches results for 6 hours
- ✅ Applies 1.15x-1.5x score multipliers
- ✅ Integrates cleanly with existing scoring
- ✅ Handles failures gracefully
- ✅ Includes comprehensive tests
- ✅ Does not break any existing tests (602 passing)

Enable it by setting `trending_boost_enabled: true` in the pipeline config.

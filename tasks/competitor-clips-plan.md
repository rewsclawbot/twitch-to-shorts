# Competitor Clip Discovery

## Context
The pipeline currently discovers clips purely from Twitch's recency-biased clip API. High-performing clips on competitor YouTube channels are a strong signal of what resonates with audiences but are currently invisible to the scoring system. This feature adds two ways to leverage that signal:

1. **Auto-scan**: Search YouTube for Shorts featuring tracked streamers, find ones with high views on competitor channels, and boost matching Twitch clips' scores
2. **Manual queue**: CLI command to queue a specific YouTube or Twitch clip URL for the next pipeline run

## New Module: `src/competitor_scanner.py`

Core functions:

### `scan_competitor_shorts(yt_service, streamer_name, own_channel_id, lookback_days, max_results) -> list[CompetitorMatch]`
- Calls `search.list(q="{streamer_name} #Shorts", type="video", videoDuration="short", order="viewCount", publishedAfter=N_days_ago, maxResults=25)`
- Cost: **100 quota units** per call
- Filters out results from `own_channel_id`
- Returns `CompetitorMatch` dataclass (video_id, title, description, channel_id, view_count, published_at)

### `match_competitors_to_clips(competitor_results, twitch_clips, threshold) -> list[tuple[Clip, int]]`
- For each competitor result:
  1. Try `extract_twitch_clip_ids(description)` - parse Twitch clip URLs from description (most reliable)
  2. Fall back to `fuzzy_title_match(youtube_title, twitch_clips, threshold)` - token-based Jaccard similarity
- Returns list of (matched_clip, competitor_view_count) tuples

### `get_own_channel_id(yt_service) -> str | None`
- `channels.list(part="id", mine=True)` - 1 quota unit, cached per service instance

### URL helpers
- `extract_twitch_clip_ids(text) -> list[str]` - regex for `clips.twitch.tv/X` and `twitch.tv/channel/clip/X`
- `extract_youtube_video_id(url) -> str | None` - handles `watch?v=`, `youtu.be/`, `shorts/`
- `fuzzy_title_match(yt_title, clips, threshold) -> Clip | None` - Jaccard on lowercased word tokens, no external deps

## Pipeline Integration (`main.py:_process_streamer`)

Reorder `_process_streamer` for clean integration:

```
1. fetch_clips (existing, line 377)
2. filter_and_rank (existing, line 392)
3. filter_new_clips (existing, line 402)
4. uploads_remaining check (MOVE from line 416 to here - early exit saves quota)
5. yt_service creation (MOVE from line 432 to here - needed for scan)
6. >>> COMPETITOR SCAN + BOOST (NEW) <<<
   - Process manual queue: get_queued_clips() -> fetch_clip_by_id() -> add to new_clips
   - Auto-scan: scan_competitor_shorts() -> match_competitors_to_clips()
   - Boost matched clips: clip.score *= competitor_score_boost
   - Re-sort new_clips by score
7. duration filter + cap to max_clips_per_streamer (existing, lines 405-409)
8. game names fetch (existing, line 423)
9. upload loop (existing, uses same yt_service)
```

Wrapped in try/except - scan failure never breaks the pipeline.

## Manual Queue CLI

New argparse flags in `main()`:
- `--queue URL [URL ...]` - queue one or more URLs
- `--queue-streamer NAME` - required with --queue

Flow: parse URL type -> resolve clip_id if Twitch -> insert into `queued_clips` table -> exit (no pipeline lock needed).

For YouTube URLs: store the URL, resolve to Twitch clip at pipeline run time (fetch video metadata via `videos.list`, parse description for Twitch source).

## DB Changes (`src/db.py`)

New table in `init_schema()`:
```sql
CREATE TABLE IF NOT EXISTS queued_clips (
    clip_url TEXT PRIMARY KEY,
    clip_id TEXT,
    streamer TEXT NOT NULL,
    source TEXT DEFAULT 'manual',
    queued_at TEXT NOT NULL,
    processed INTEGER DEFAULT 0
);
```

New functions: `queue_clip()`, `get_queued_clips()`, `mark_queued_processed()`

## Config Additions

`config.yaml` under `pipeline:`:
```yaml
competitor_scan_enabled: false      # Master toggle (default off)
competitor_lookback_days: 7         # YouTube search window
competitor_max_results: 25          # Results per search (max 50)
competitor_score_boost: 1.5         # Score multiplier for matched clips
competitor_min_views: 1000          # Min YouTube views to consider
competitor_title_match_threshold: 0.6
```

Add to `PipelineConfig` dataclass in `src/models.py` with validation.

## Twitch Client Addition (`src/twitch_client.py`)

`fetch_clip_by_id(clip_id) -> Clip | None` - `GET /helix/clips?id=X`, returns single clip. Needed for manual queue items and description-matched clips outside the lookback window.

## Quota Budget

- Current: ~7,500 units/day (5 uploads x 1,500)
- Scan cost: 100 units/search + 1 unit for channel ID = ~101 per streamer per run
- 1 streamer, 6 runs/day = 606 units/day -> total ~8,100 -> well within 10,000
- 3 streamers, 6 runs/day = 1,818 units/day -> total ~9,300 -> tight but feasible

## Files to Create/Modify

| File | Action |
|------|--------|
| `src/competitor_scanner.py` | **Create** - scanning, matching, URL parsing |
| `src/models.py` | Edit - add competitor fields to PipelineConfig |
| `src/db.py` | Edit - add queued_clips table + helpers |
| `src/twitch_client.py` | Edit - add fetch_clip_by_id() |
| `main.py` | Edit - reorder _process_streamer, add --queue CLI, integrate scan |
| `config.yaml` | Edit - add competitor config (disabled by default) |
| `tests/test_competitor_scanner.py` | **Create** - unit tests for new module |

## Implementation Order

1. **Foundation**: `src/competitor_scanner.py` with URL parsing + fuzzy matching (no API calls yet)
2. **Config + DB**: PipelineConfig fields, queued_clips table, DB helpers
3. **Twitch**: fetch_clip_by_id()
4. **YouTube API**: scan_competitor_shorts(), get_own_channel_id()
5. **Pipeline integration**: reorder _process_streamer, insert competitor scan + queue processing
6. **CLI**: --queue command
7. **Tests**: comprehensive test file
8. **Config**: update config.yaml with commented defaults

## Verification

1. Run `python -m pytest` - all existing 183+ tests pass
2. Run `python main.py --dry-run` - verify no errors from new code paths (scan disabled by default)
3. Enable `competitor_scan_enabled: true` in config, run dry-run - verify scan logs appear
4. Test `python main.py --queue https://clips.twitch.tv/SomeClip --queue-streamer TheBurntPeanut`
5. Verify queued clip appears in DB: `sqlite3 data/clips.db "SELECT * FROM queued_clips"`
6. Run pipeline - verify queued clip is processed and boost logic works

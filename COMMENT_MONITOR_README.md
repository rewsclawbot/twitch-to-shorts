# YouTube Comment Monitor & Auto-Engagement

Automated comment monitoring system that checks for new comments on uploaded Shorts and auto-responds to drive engagement. The YouTube algorithm rewards active comment sections, especially in the first hour after posting.

## Features

- **Smart Reply Generation**: Template-based contextual replies (no LLM overhead)
  - Laugh responses for humor comments
  - Helpful responses for questions
  - Streamer-specific replies when mentioned
  - Positive engagement for compliments
  - Generic engagement fallbacks
  
- **Rate Limiting**: Quota-aware (budgeted for ~100 replies/day max)
  - Max 2 replies per video per run
  - Max 10 total replies per run
  - Prioritizes comments by like_count

- **Deduplication**: Tracks replied comments in DB to avoid spam

- **Pipeline Integration**: Auto-runs after uploads when analytics enabled

## Usage

### Standalone CLI

```bash
# Dry run (no actual replies)
.venv/bin/python scripts/monitor_comments.py --dry-run

# Production run (default: max 5 videos, 2 replies/video, 10 total)
.venv/bin/python scripts/monitor_comments.py

# Custom limits
.venv/bin/python scripts/monitor_comments.py --max-videos 10 --max-replies-per-video 3
```

### Pipeline Integration

Comment monitoring automatically runs at the end of each pipeline run when `analytics_enabled: true` in config.yaml.

To disable, set `analytics_enabled: false` or run pipeline in dry-run mode.

## Database Schema

The system adds a new table `comment_replies`:

```sql
CREATE TABLE comment_replies (
    comment_id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL,
    reply_text TEXT,
    replied_at TEXT
);
```

## API Quota Usage

- `commentThreads.list`: 1 unit per video (fetch comments)
- `comments.insert`: 50 units per reply (post reply)

With defaults (5 videos, 10 replies max):
- Worst case: 5 (fetch) + 10 × 50 (replies) = **505 units/run**
- Typical case: 5 (fetch) + 3 × 50 (replies) = **155 units/run**

Daily quota is 10,000 units, so budget ~20 runs/day with typical engagement.

## Reply Templates

The system uses 5 template categories:

1. **Laugh replies**: For "lol", "lmao", "😂", "haha", etc.
2. **Question replies**: For comments with "?" or question words
3. **Streamer mention replies**: When comment mentions the streamer
4. **Positive replies**: For "great", "amazing", "epic", "fire", etc.
5. **Generic replies**: Fallback engagement

Templates rotate deterministically based on comment hash (same comment always gets same reply).

## Testing

```bash
# Run comment monitor tests
.venv/bin/python -m pytest tests/test_comment_monitor.py -v

# Run full test suite
.venv/bin/python -m pytest tests/ -x -q
```

All 28 comment monitor tests pass, covering:
- Comment fetching and parsing
- Reply generation (all template paths)
- Rate limiting (per-video and total caps)
- DB tracking of replied comments
- Dry run mode
- API error handling

## Safety Features

- **Dry run mode**: Test without posting real replies
- **Quota protection**: Hard limits prevent API quota exhaustion
- **Error handling**: Non-critical failures logged, don't crash pipeline
- **Recent uploads only**: Only monitors videos posted in last 48h
- **Already-replied filter**: Never replies to same comment twice
- **High-value prioritization**: Replies to comments with most likes first

## Maintenance

Monitor the `comment_replies` table growth. Old entries can be pruned after 90 days:

```sql
DELETE FROM comment_replies 
WHERE datetime(replied_at) < datetime('now', '-90 days');
```

## Future Enhancements (Optional)

- [ ] Per-streamer reply templates
- [ ] LLM-powered dynamic replies (with quota budget)
- [ ] Sentiment analysis to avoid replying to negative comments
- [ ] Reply scheduling (batch replies at optimal times)
- [ ] Metrics dashboard (reply rate, engagement lift)

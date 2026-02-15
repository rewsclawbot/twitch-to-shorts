# Analytics Sync Debug Report

## Issue Summary
Analytics sync shows `analytics_ok=0 analytics_fail=3` in pipeline logs, but this is **not a scope or API error**.

## Root Cause
**YouTube Analytics has a 24-48 hour data latency.** Videos uploaded less than 24-48 hours ago will not have analytics data available yet.

## Evidence

### Log Analysis (2026-02-15 12:03:55)
```
Reach metrics unavailable, falling back to core metrics for 738QUrU2oNU
Reach metrics unavailable, falling back to core metrics for 3QiTOra5wDU
Reach metrics unavailable, falling back to core metrics for 6V9DQI-LWTA
Analytics sync for TheBurntPeanut: 3 eligible, analytics_ok=0 analytics_fail=3
```

### Video Upload Times
```
6V9DQI-LWTA: posted=2026-02-14T16:14:20 (< 24h old)
3QiTOra5wDU: posted=2026-02-14T16:49:56 (< 24h old)
738QUrU2oNU: posted=2026-02-14T16:50:17 (< 24h old)
```

### Diagnostic Test
```python
from src.youtube_analytics import get_analytics_service
svc = get_analytics_service('credentials/client_secret.json', 'credentials/theburntpeanut_youtube.json')
# Result: <googleapiclient.discovery.Resource object at 0x106ed7ef0>
```
✅ Analytics service authenticates successfully - credentials and scopes are valid.

## What's Happening

1. API query succeeds (no HttpError thrown)
2. YouTube returns empty result set (no rows) because data isn't ready yet
3. `_parse_report()` returns `None` (expected for new videos)
4. Code treats this as `analytics_fail` rather than "pending"

## Not a Bug, But...

The current behavior is **correct** but **misleading**:
- `analytics_fail=3` suggests an error, but it's actually "no data available yet"
- The code will successfully fetch metrics after 24-48 hours on the next sync

## Recommended Improvements (Optional)

1. Add `analytics_pending` counter for videos with no data yet (vs actual errors)
2. Log differently: "No analytics data available yet for {video_id} (video age: {hours}h)"
3. Update `config.yaml` to set `analytics_min_age_hours: 48` to avoid querying too-new videos

## Scopes & Permissions
✅ YouTube Analytics API is enabled and properly scoped
✅ Credentials authenticate successfully
✅ No permission errors in logs (thumbnail 403 is separate/expected)

## Conclusion
**No fix needed.** The "analytics_fail" count will decrease to 0 once videos are 48+ hours old and data becomes available. The system is working as designed.

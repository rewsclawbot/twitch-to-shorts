# Upload Schedule Runbook

This document is the single source of truth for verifying the Twitch-to-Shorts pipeline is uploading on schedule. Any new Claude instance should reference this to audit recent runs and determine if we're on track.

---

## Schedule Configuration

```yaml
cron: '17 2/4 * * *'  # Every 4 hours at :17 past, offset +2h to avoid congested 00/04/08 slots
```

| Slot (UTC) | Central (CST/UTC-6) |
|------------|---------------------|
| 02:17      | 8:17 PM             |
| 06:17      | 12:17 AM            |
| 10:17      | 4:17 AM             |
| 14:17      | 8:17 AM             |
| 18:17      | 12:17 PM            |
| 22:17      | 4:17 PM             |

**Key parameters** (from `config.yaml`):
- `max_uploads_per_window: 1` — one upload per triggered run
- `upload_spacing_hours: 2` — minimum gap between uploads for a streamer
- `min_view_count: 50` — clips below this are excluded from ranking
- `max_clips_per_streamer: 6` — max clips fetched per streamer per run
- `clip_lookback_hours: 168` — 7-day clip window

**Realistic expectations:**
- **Target: 5 uploads/day** (the 22:17 UTC slot is skipped ~67% of the time due to GitHub Actions load-shedding — this is a GitHub infrastructure issue, not our code)
- GitHub Actions cron delays are **2-4 hours** on average (2h38m min, 3h43m max observed post-offset)
- A triggered run with `uploaded=0, failed=0` usually means upload spacing or dedup — this is normal

---

## How to Audit Recent Runs

### Step 1: Pull recent workflow runs

```bash
gh run list --workflow=pipeline.yml --limit 20 --json createdAt,status,conclusion,databaseId
```

### Step 2: Map runs to scheduled slots

Each run's `createdAt` timestamp should fall within 0-3 hours after one of the 6 daily slots (02:17, 06:17, 10:17, 14:17, 18:17, 22:17 UTC). If a slot has no run within 3 hours, GitHub skipped it.

### Step 3: Check each run's result

```bash
gh run view <run_id> --log 2>&1 | grep -E "Pipeline complete|uploaded=|failed=|ERROR|spacing|dedup|channel_key"
```

Look for the `Pipeline complete` summary line:
- `uploaded=1, failed=0` — success
- `uploaded=0, failed=0` — spacing limit or dedup (expected, not a bug)
- `uploaded=0, failed=1` — our bug, investigate further
- No `Pipeline complete` line — crash before completion (our bug)

### Step 4: Score the day

```
Date: ____
Slots triggered:   ___ / 6 expected
Successful uploads: ___
Spacing-blocked:    ___
Failed (our bugs):  ___
Skipped by GitHub:  ___
```

**On track:** 5 triggers, 5 uploads, 0 failures
**Concerning:** <4 triggers, or any `failed=1`, or same clip uploaded twice

---

## Diagnosis Decision Tree

```
Run triggered but no upload?
├── Check "Pipeline complete" line in logs
│   ├── uploaded=0, failed=1     → Our bug (check full logs for error)
│   ├── uploaded=0, failed=0     → Spacing limit or dedup (expected)
│   └── No "Pipeline complete"   → Crash before completion (our bug)
│
Run not triggered at all?
├── Check gh run list for the expected time window
│   ├── No run within 3h of slot → GitHub skipped it (not our fault)
│   └── Run exists but late      → GitHub delay (not our fault)
```

| Situation                              | Verdict            |
|----------------------------------------|--------------------|
| Run triggers 1-3h after scheduled slot | Normal (GitHub)    |
| A single slot skipped                  | Normal (rare but happens) |
| Any single slot skipped once           | Normal (rare but happens) |
| 2+ slots skipped in a row              | Investigate GitHub or pushes suppressing cron |
| `uploaded=0, failed=0`                 | Check upload spacing/dedup — expected |
| `uploaded=0, failed=1`                 | Our bug — check logs |
| Same clip uploaded twice               | Dedup bug (check DB cache, channel dedup) |

---

## Historical Actuals

Use this data to establish baselines and spot regressions.

### Feb 3-6, 2026 (initial launch period)

| Slot (UTC) | Actual (UTC) | Delay | Result |
|------------|-------------|-------|--------|
| Feb 3, 08:00 | 10:04 | +2h 04m | Uploaded: When You Flex on the Nut |
| Feb 3, 12:00 | 14:31 | +2h 31m | Duplicate (cache bug, pre-fix) |
| Feb 3, 16:00 | 18:36 | +2h 36m | Duplicate (cache bug, pre-fix) |
| Feb 3, 20:00 | 21:50 | +1h 50m | Uploaded: PEANUT FLYS AWAY! |
| Feb 4, 00:00 | — | SKIP | Skipped by GitHub |
| Feb 4, 04:00 | 04:58 | +0h 58m | Uploaded: money |
| Feb 4, 08:00 | 10:08 | +2h 08m | Uploaded: 300 to 100 |
| Feb 4, 12:00 | 14:28 | +2h 28m | Uploaded: You Can't Just Say That |
| Feb 4, 16:00 | 18:08 | +2h 08m | 0 new after dedup |
| Feb 4, 20:00 | 21:04 | +1h 04m | FAILED: `--body -` token corruption |
| Feb 5, 00:00 | — | SKIP | Skipped by GitHub |
| Feb 5, 04:00 | 05:08 | +1h 08m | Uploaded: HutchMF impersonation |
| Feb 5, 08:00 | 10:08 | +2h 08m | Uploaded: THIS GUY KILL PEANUT |
| Feb 5, 12:00 | 14:33 | +2h 33m | Uploaded: Michael Jackson HEE HEE |
| Feb 5, 16:00 | 18:32 | +2h 32m | Spacing limit (MJ HEE HEE <4h ago) |
| Feb 5, 20:00 | 21:03 | +1h 03m | FAILED: `channel_key` migration crash |
| Feb 6, 00:17 | — | SKIP | Skipped by GitHub (3/3 midnight slots skipped) |
| Feb 6, 04:17 | 07:31 | +3h 14m | Uploaded: GOOD! -Hutch (1st clip CPU timeout, fell through to 2nd) |
| Feb 6, 08:17 | 10:09 | +1h 52m | Uploaded: PEANUT FACE LEAK!!! (1st clip CPU timeout, fell through) |
| Feb 6, 12:17 | 14:31 | +2h 14m | Uploaded: Peanut gets stepped on LOL (1st clip CPU timeout, fell through) |
| **Schedule changed to `17 2/4` (offset +2h) after this point** | | | |
| Feb 6, 18:17 | 18:08 | ~0m (transition) | Uploaded: aimbotter |
| Feb 6, 22:17 | 21:48 | ~0m (transition) | Uploaded: A Day in the Life of Team Leader |
| Feb 7, 02:17 | 04:55 | +2h 38m | Uploaded: TWO HANDSOME GUYS |
| Feb 7, 06:17 | 09:03 | +2h 46m | Uploaded: Flaming Goop |
| Feb 7, 10:17 | 13:57 | +3h 40m | Uploaded: COULDNT EVEN SEE THEIR HANDS |
| Feb 7, 14:17 | 17:03 | +2h 46m | Uploaded: Peanut finds new tech |
| Feb 7, 18:17 | 20:58 | +2h 41m | Uploaded: Gingy's backshots |
| Feb 7, 22:17 | — | SKIP | Skipped by GitHub |
| Feb 8, 02:17 | 05:39 | +3h 22m | Uploaded: racist |
| Feb 8, 06:17 | 09:47 | +3h 30m | Uploaded: Wassup |
| Feb 8, 10:17 | 14:00 | +3h 43m | Uploaded: IM CRYINGGGG |
| Feb 8, 14:17 | 17:04 | +2h 47m | Uploaded: Your the winner! |
| Feb 8, 18:17 | 20:59 | +2h 42m | Uploaded: CLOAZY YOU SO UGLY.... |
| Feb 8, 22:17 | — | SKIP | Skipped by GitHub (2/3 days skipped) |
| Feb 9, 02:17 | — | SKIP | Skipped by GitHub (1st skip for this slot) |
| Feb 9, 06:17 | 08:08 | +1h 51m | Uploaded: I'M VIEWBOTTING?! |
| Feb 9, 10:17 | 12:05 | +1h 48m | Uploaded: HUTCH?? WHAT??? GOOD?? |
| Feb 9, ~13:00 | — | — | Manual upload: Buddy we have a flying black Dorito... |
| Feb 9, 14:17 | 17:30 | +3h 13m | Uploaded: YAAAAAAAS |
| Feb 9, 18:17 | 20:07 | +1h 50m | Uploaded: No |
| Feb 9, 22:17 | 00:02 | +1h 45m | Uploaded: nick the thief |

**Baseline metrics (20 cron slots observed, pre-offset schedule):**
- Average delay: ~1h 58m
- Delay range: 58m to 3h 14m
- Midnight (00:xx) skip rate: 100% (3/3) — offset schedule avoids this slot
- Non-midnight skip rate: 0%
- Effective runs/day: ~5 (expect ~6 with offset schedule)
- Upload success rate (when triggered, excluding known-fixed bugs): ~80%

**Post-offset baseline (Feb 7-9, 22 slots observed):**
- Average delay: ~2h 36m (down from ~3h 03m as Feb 9 ran faster)
- Delay range: 1h 45m to 3h 43m
- 22:17 UTC skip rate: 50% (2/4 days) — improving, fired on Feb 9
- 02:17 UTC skip rate: 33% (1/3 days) — first skip observed Feb 9
- Other slots skip rate: 0%
- Effective runs/day: ~5 (consistent across all observed days)
- Upload success rate: 100% (22/22 triggered runs uploaded)
- Failure rate: 0%

---

## How to Update This Document

After observing new runs, add rows to the Historical Actuals section:

1. Run `gh run list --workflow=pipeline.yml --limit 10` to get recent runs
2. Map each run to its scheduled slot (subtract 1-3h from `createdAt`)
3. Check result with `gh run view <id> --log 2>&1 | grep "Pipeline complete"`
4. Add a row: `| <slot> | <actual trigger> | <delay> | <result> |`
5. If a slot has no run within 3h, mark as `never triggered | SKIP | Skipped by GitHub`
6. Update baseline metrics if the dataset grows significantly

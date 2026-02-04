# Schedule Expectations & Upload Benchmark

## Cron Configuration

```yaml
cron: '0 */4 * * *'  # Every 4 hours: 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC
```

**Max uploads per run:** 1 (controlled by `max_uploads_per_window`)
**Upload spacing:** 4 hours between uploads per streamer
**Theoretical max uploads per day:** 6

---

## UTC to Central Time Mapping

| Scheduled (UTC) | Central (CST/UTC-6) |
|-----------------|---------------------|
| 00:00           | 6:00 PM             |
| 04:00           | 10:00 PM            |
| 08:00           | 2:00 AM             |
| 12:00           | 6:00 AM             |
| 16:00           | 10:00 AM            |
| 20:00           | 2:00 PM             |

---

## Observed Behavior (Baseline Data)

### Run History: Feb 3-4, 2026

| Scheduled Slot (UTC) | Actual Trigger (UTC) | Delay    | Clip Uploaded               | Result    |
|----------------------|---------------------|----------|-----------------------------|-----------|
| Feb 3, 08:00         | 10:04               | +2h 04m  | When You Flex on the Nut    | Uploaded  |
| Feb 3, 12:00         | 14:31               | +2h 31m  | When You Flex on the Nut    | Duplicate (cache bug) |
| Feb 3, 16:00         | 18:36               | +2h 36m  | When You Flex on the Nut    | Duplicate (cache bug) |
| Feb 3, 20:00         | 21:50               | +1h 50m  | PEANUT FLYS AWAY!           | Uploaded  |
| Feb 4, 00:00         | **never triggered**  | **SKIP** | —                           | Skipped by GitHub |
| Feb 4, 04:00         | 04:58               | +0h 58m  | money                       | Uploaded  |
| Feb 4, 08:00         | 10:08               | +2h 08m  | 300 to 100                  | Uploaded  |
| Feb 4, 12:00         | 14:28               | +2h 28m  | You Can't Just Say That     | Uploaded  |

### Key Metrics from Baseline

| Metric                          | Value         |
|---------------------------------|---------------|
| Data points                     | 8 slots (7 triggered, 1 skipped) |
| Average delay                   | ~2h 05m       |
| Min delay                       | 58m           |
| Max delay                       | 2h 36m        |
| Skipped runs (observed)         | 1 out of 8    |
| Skip rate                       | ~12.5%        |
| Effective runs per day          | ~5 (not 6)    |

---

## How to Diagnose Issues

### Is this a GitHub Actions delay or our bug?

```
Run triggered but no upload?
├── Check "Pipeline complete" line in logs
│   ├── uploaded=0, failed=1     → Our bug (processing/upload failure)
│   ├── uploaded=0, failed=0     → Rate limit or dedup (expected)
│   └── No "Pipeline complete"   → Crash (our bug)
│
Run not triggered at all?
├── Check gh run list for the expected time window
│   ├── No run within 3h of slot → GitHub skipped it (not our fault)
│   └── Run exists but late      → GitHub delay (not our fault)
```

### Expected vs Concerning

| Situation                              | Verdict            |
|----------------------------------------|--------------------|
| Run triggers 1-3h after scheduled slot | Normal (GitHub)    |
| Run skipped once in a 24h period       | Normal (GitHub)    |
| Run skipped 2+ times in a row          | Investigate GitHub  |
| Run triggers but uploads 0, failed=0   | Check upload spacing/dedup |
| Run triggers but uploads 0, failed=1   | Check logs for processing error |
| Same clip uploaded twice               | Dedup/cache bug (our side) |
| Cache save failed                      | Check permissions or key conflict |

---

## Benchmarking Checklist

Use this after each day to track consistency:

```
Date: ____
Runs triggered:    ___ / 6 expected
Unique uploads:    ___
Duplicate uploads: ___
Failed uploads:    ___
Cache saved OK:    ___ / ___ runs
Longest delay:     ___
Skipped slots:     ___
```

---

## Rolling Projection: Feb 4-5, 2026

> **Purpose:** Validate assumptions about GitHub Actions timing, clip selection, and pipeline reliability.
> Compare actual results against these predictions, then update the baseline metrics above.
> Delete this section once the projection window has passed and lessons are captured.

### Assumptions Being Tested

| #  | Assumption                                                    | Confidence |
|----|---------------------------------------------------------------|------------|
| A1 | Delays will average ~2h, ranging from 1h to 3h               | Medium     |
| A2 | ~1 in 7 scheduled slots will be skipped by GitHub             | Low        |
| A3 | Dedup fix holds — zero duplicate uploads                      | High       |
| A4 | Cache persists correctly (GITHUB_TOKEN fix)                   | High       |
| A5 | Clips are selected in ranked order from the filter            | High       |
| A6 | Each run uploads exactly 1 clip (upload spacing enforced)     | High       |

### Assumption Validation (as of Feb 4, 14:28 UTC)

| #  | Assumption                              | Status        | Evidence |
|----|-----------------------------------------|---------------|----------|
| A1 | Delays avg ~2h, range 1-3h              | **VALIDATED** | 7 data points, avg 2h05m, range 58m–2h36m |
| A2 | ~1 in 7 slots skipped by GitHub         | **TRACKING**  | 1 skip in 8 slots (12.5%). Need more data |
| A3 | Zero duplicate uploads (dedup fix)      | **VALIDATED** | All 4 post-fix uploads unique (money, 300 to 100, You Can't Just Say That) |
| A4 | Cache persists (GITHUB_TOKEN fix)       | **VALIDATED** | clips-db-v1 restored + saved on every run since fix |
| A5 | Clips selected in ranked order          | **VALIDATED** | "You Can't Just Say That" predicted as next → exact match at 12:00 slot |
| A6 | Each run uploads exactly 1 clip         | **VALIDATED** | All 4 post-fix runs: uploaded=1, failed=0 |

### Predicted Clip Queue

Based on current filter ranking with already-uploaded clips excluded:

| Position | Title                    | Duration | Views | Clip ID |
|----------|--------------------------|----------|-------|---------|
| Next     | You Can't Just Say That  | 60s      | 3,821 | MushyPerfectOryxBatChest-2j85b3_bwoooQ2bm |
| 2nd      | THIS GUY KILL PEANUT     | 30s      | 1,503 | SillyInterestingWoodpeckerJebaited-WkelOnBOUtuW1FLd |
| 3rd      | HutchMF impersonation    | 60s      | 2,186 | FurryHardBeeMikeHogu-CfARvAJMSnkgCaUV |
| 4th      | CLOAK SNATCHED AWAY      | 10s      | 553   | BetterOutstandingSowRitzMitz-V7K_ctzntV3HVXbH |

> **Note:** Queue may shift between runs as new clips appear on Twitch or view counts change.
> After position 4, the filter will need to pull from lower-ranked clips or newly created ones.

### Feb 4 Projection (remaining slots)

| Slot (UTC) | Expected Trigger   | Central         | Predicted Clip            | Predicted Result |
|------------|-------------------|-----------------|---------------------------|------------------|
| 12:00      | ~13:00-14:30 UTC  | ~7-8:30 AM      | You Can't Just Say That   | Upload 1 clip    |
| 16:00      | ~17:00-18:30 UTC  | ~11 AM-12:30 PM | THIS GUY KILL PEANUT      | Upload 1 clip    |
| 20:00      | ~21:00-22:30 UTC  | ~3-4:30 PM      | HutchMF impersonation     | Upload 1 clip    |
| 00:00      | ~01:00-02:30 UTC  | ~7-8:30 PM      | CLOAK SNATCHED AWAY       | Upload 1 clip (or skip) |

**Feb 4 total prediction:** 4 unique uploads (3 if the 00:00 slot gets skipped like last night)

### Feb 5 Projection

| Slot (UTC) | Expected Trigger   | Central          | Predicted Clip              | Predicted Result |
|------------|-------------------|------------------|-----------------------------|------------------|
| 04:00      | ~05:00-06:30 UTC  | ~11 PM-12:30 AM  | Queue position 4 or 5       | Upload 1 clip    |
| 08:00      | ~09:00-10:30 UTC  | ~3-4:30 AM       | New clip from rankings      | Upload 1 clip    |
| 12:00      | ~13:00-14:30 UTC  | ~7-8:30 AM       | New clip from rankings      | Upload 1 clip    |
| 16:00      | ~17:00-18:30 UTC  | ~11 AM-12:30 PM  | New clip from rankings      | Upload 1 clip    |
| 20:00      | ~21:00-22:30 UTC  | ~3-4:30 PM       | New clip from rankings      | Upload 1 clip    |
| 00:00      | ~01:00-02:30 UTC  | ~7-8:30 PM       | New clip from rankings      | Upload 1 clip (or skip) |

**Feb 5 total prediction:** 5-6 unique uploads

### Actuals (fill in as runs complete)

| Slot (UTC)       | Actual Trigger | Delay   | Clip Uploaded | Cache Saved? | Matches Prediction? |
|------------------|----------------|---------|---------------|--------------|---------------------|
| Feb 4, 12:00     | 14:28          | +2h 28m | You Can't Just Say That | Yes  | YES — exact match   |
| Feb 4, 16:00     |                |         |               |              |                     |
| Feb 4, 20:00     |                |         |               |              |                     |
| Feb 4/5, 00:00   |                |         |               |              |                     |
| Feb 5, 04:00     |                |         |               |              |                     |
| Feb 5, 08:00     |                |         |               |              |                     |
| Feb 5, 12:00     |                |         |               |              |                     |
| Feb 5, 16:00     |                |         |               |              |                     |
| Feb 5, 20:00     |                |         |               |              |                     |
| Feb 5/6, 00:00   |                |         |               |              |                     |

### Scorecard (fill in after projection window)

```
Assumptions validated:    ___ / 6
Clip predictions correct: ___ / ___
Timing predictions correct (within window): ___ / ___
Skips predicted correctly: ___ / ___
```

**Takeaways:** (write after projection window closes)

---

## Fixes Applied (for reference)

| Date    | Issue                          | Fix                                      |
|---------|--------------------------------|------------------------------------------|
| Feb 3   | Cache not persisting (static key) | Dynamic key with `run_id`, then switched to delete-then-save with `clips-db-v1` |
| Feb 3   | Duplicate uploads              | Added `data/blocklist.txt` checked in `src/dedup.py` |
| Feb 3   | Beth Oven upload failed (403 `insufficientPermissions` on verify) | Added trust-upload-success fallback (skip verification step) |
| Feb 4   | Cache delete 403 (PAT scope)   | Switched to `GITHUB_TOKEN` with `actions: write` permission |

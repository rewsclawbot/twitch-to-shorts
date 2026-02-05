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

### Run History: Feb 3-5, 2026

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
| Feb 4, 16:00         | 18:08               | +2h 08m  | None                        | 0 new after dedup |
| Feb 4, 20:00         | 21:04               | +1h 04m  | None                        | FAILED (`--body -` token corruption) |
| Feb 5, 00:00         | **never triggered**  | **SKIP** | —                           | Skipped by GitHub |

### Key Metrics from Baseline

| Metric                          | Value         |
|---------------------------------|---------------|
| Data points                     | 11 slots (9 triggered, 2 skipped) |
| Average delay                   | ~1h 56m       |
| Min delay                       | 58m           |
| Max delay                       | 2h 36m        |
| Skipped runs (observed)         | 2 out of 11   |
| Skip rate                       | ~18%          |
| Effective runs per day          | ~5 (not 6)    |
| Successful uploads              | 6 unique + 2 duplicates (pre-fix) |
| Failed runs (our bugs)          | 1 (`--body -` corruption) |

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

### Assumption Validation (as of Feb 5, 00:49 UTC)

| #  | Assumption                              | Status          | Evidence |
|----|-----------------------------------------|-----------------|----------|
| A1 | Delays avg ~2h, range 1-3h              | **VALIDATED**   | 9 data points, avg ~1h56m, range 58m–2h36m |
| A2 | ~1 in 7 slots skipped by GitHub         | **VALIDATED**   | 2 skips in 11 slots (18%). Consistent with ~1-in-7 rate |
| A3 | Zero duplicate uploads (dedup fix)      | **INVALIDATED** | Manual dispatch at 23:21 duplicated "300 to 100" (stale DB from `--body -` corruption chain). Fixed by 3-layer defense (commit `83fed74`) + `--body -` fix (`37a75f0`) |
| A4 | Cache persists (GITHUB_TOKEN fix)       | **VALIDATED**   | Cache works; token save was the problem (`--body -` corrupted secret every successful run). Fixed in `37a75f0` |
| A5 | Clips selected in ranked order          | **VALIDATED**   | "You Can't Just Say That" predicted as next → exact match at 12:00 slot |
| A6 | Each run uploads exactly 1 clip         | **VALIDATED**   | All successful pipeline runs: uploaded≤1, spacing enforced correctly |

### Predicted Clip Queue (as of Feb 5, 00:50 UTC)

Based on current filter ranking with DB + blocklist exclusions:

| Position | Title                                   | Duration | Views | Status |
|----------|-----------------------------------------|----------|-------|--------|
| 1        | HutchMF impersonation                   | 60s      | 2,306 | New |
| 2        | THIS GUY KILL PEANUT                    | 30s      | 1,699 | New |
| 3        | Michael Jackson HEE HEE                 | 44s      | 1,334 | New |
| 4        | CLOAK SNATCHED AWAY                     | 10s      | 600   | New |
| 5        | Ultimate GOOP to the Snap hook flex!    | 44s      | 639   | New |
| 6        | money                                   | 30s      | 389   | Already on YT (`8QWb8hFFWEo`) — channel dedup will catch |

After queue exhausts, next eligible clips (positions 9-13 in rankings):
PEANUT FACE LEAK!!! (483 views), aimbotter (379), macro sound (365), COULDNT EVEN SEE THEIR HANDS (346), Peanut Gingy and Officer (311)

> **Note:** CI DB lost most upload history during `--body -` corruption chain.
> Only "300 to 100" is tracked. Blocklist covers 3 older uploads.
> Channel dedup check (new) is the safety net for "money" and any other untracked uploads.
> Queue may shift as new Twitch clips appear or view counts change.

### Feb 4 Projection (remaining slots) — CLOSED

| Slot (UTC) | Predicted                   | Actual                              | Match? |
|------------|-----------------------------|-------------------------------------|--------|
| 12:00      | You Can't Just Say That     | You Can't Just Say That (14:28)     | YES    |
| 16:00      | THIS GUY KILL PEANUT        | No upload — 0 new after dedup       | NO — queue was empty at that point |
| 20:00      | HutchMF impersonation       | FAILED — `--body -` token corruption | NO — our bug |
| 00:00      | CLOAK SNATCHED AWAY (or skip) | Skipped by GitHub                 | Partial — skip predicted as possibility |

**Feb 4 actual:** 1 unique upload (predicted 3-4). Missed due to dedup exhaustion + token corruption.

### Feb 5-6 Projection

| Slot (UTC) | Expected Trigger   | Central          | Predicted Clip                        | Predicted Result |
|------------|-------------------|------------------|---------------------------------------|------------------|
| 04:00      | ~05:00-06:30 UTC  | ~11 PM-12:30 AM  | HutchMF impersonation                 | Upload 1 clip |
| 08:00      | ~09:00-10:30 UTC  | ~3-4:30 AM       | THIS GUY KILL PEANUT                  | Upload 1 clip |
| 12:00      | ~13:00-14:30 UTC  | ~7-8:30 AM       | Michael Jackson HEE HEE               | Upload 1 clip |
| 16:00      | ~17:00-18:30 UTC  | ~11 AM-12:30 PM  | CLOAK SNATCHED AWAY                   | Upload 1 clip |
| 20:00      | ~21:00-22:30 UTC  | ~3-4:30 PM       | Ultimate GOOP to the Snap hook flex!  | Upload 1 clip |
| 00:00      | ~01:00-02:30 UTC  | ~7-8:30 PM       | money → channel dedup catches         | Skip (or GitHub skips slot) |
| 04:00      | ~05:00-06:30 UTC  | ~11 PM-12:30 AM  | PEANUT FACE LEAK!!!                   | Upload 1 clip |

**Feb 5 prediction:** 5 unique uploads, 0 duplicates (channel dedup blocks "money")
**Key test:** First fully unattended cycle with all 3-layer defense + `--body -` fix

### Actuals (fill in as runs complete)

| Slot (UTC)       | Actual Trigger | Delay   | Clip Uploaded | Cache Saved? | Matches Prediction? |
|------------------|----------------|---------|---------------|--------------|---------------------|
| Feb 4, 12:00     | 14:28          | +2h 28m | You Can't Just Say That | Yes  | YES — exact match   |
| Feb 4, 16:00     | 18:08          | +2h 08m | None (0 new after dedup) | Yes | NO — predicted upload but only ranked clip already uploaded |
| Feb 4, 20:00     | 21:04          | +1h 04m | FAILED (creds) | N/A  | NO — `--body -` token corruption broke restore |
| Feb 4/5, 00:00   | **skipped**    | **SKIP** | —             | —    | Skipped by GitHub (2nd time) |
| *manual 23:21*   | 23:21          | N/A     | 300 to 100 (DUPLICATE `wKGoDHSlbx0`, deleted) | Yes | Stale DB from corruption chain |
| *manual 00:26*   | 00:26          | N/A     | None (spacing limit) | Yes | Artifact fallback restored DB correctly |
| *manual 00:42*   | 00:42          | N/A     | None (spacing limit) | Yes | Token save fixed, 2 consecutive green runs |
| Feb 5, 04:00     |                |         |               |              | Predict: HutchMF impersonation |
| Feb 5, 08:00     |                |         |               |              | Predict: THIS GUY KILL PEANUT |
| Feb 5, 12:00     |                |         |               |              | Predict: Michael Jackson HEE HEE |
| Feb 5, 16:00     |                |         |               |              | Predict: CLOAK SNATCHED AWAY |
| Feb 5, 20:00     |                |         |               |              | Predict: Ultimate GOOP... |
| Feb 5/6, 00:00   |                |         |               |              | Predict: money (dedup skip or GitHub skip) |
| Feb 6, 04:00     |                |         |               |              | Predict: PEANUT FACE LEAK!!! |

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
| Feb 4   | DB cache saved on failed runs  | Changed DB save steps to `if: success()` (`b6700a2`) |
| Feb 5   | 3-layer upload dedup defense   | DB-before-verify + artifact fallback + channel dedup check (`83fed74`) |
| Feb 5   | `--body -` corrupts token secret every successful run | Removed `--body` flag so `gh secret set` reads from stdin (`37a75f0`) |

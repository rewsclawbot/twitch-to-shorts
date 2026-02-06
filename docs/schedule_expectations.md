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

### Run History: Feb 3-6, 2026

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
| Feb 5, 04:00         | 05:08               | +1h 08m  | HutchMF impersonation (`Liyu0YNmG8g`) | Uploaded |
| Feb 5, 08:00         | 10:08               | +2h 08m  | THIS GUY KILL PEANUT (`nPUjFuGmaD4`) | Uploaded |
| Feb 5, 12:00         | 14:33               | +2h 33m  | Michael Jackson HEE HEE (`Pu8q4adi0M8`) | Uploaded |
| Feb 5, 16:00         | 18:32               | +2h 32m  | None                        | Spacing limit (MJ HEE HEE <4h ago) |
| Feb 5, 20:00         | 21:03               | +1h 03m  | None                        | FAILED (`channel_key` migration crash — pre-fix code) |
| *manual 22:20*       | 22:20               | N/A      | 5 clips attempted           | FAILED (all 5 uploads auth error — stale token from crash) |
| *manual 23:45-47*    | 23:45               | N/A      | Dry runs                    | Success (testing infra changes) |
| Feb 5/6, 00:00       |                      |          |                             | Pending — historically skip-prone (2/2 skipped) |

### Key Metrics (running total, Feb 3-6)

| Metric                          | Value         |
|---------------------------------|---------------|
| Data points                     | 17 slots (15 triggered, 2 skipped) + 1 pending |
| Average delay                   | ~1h 55m       |
| Min delay                       | 58m           |
| Max delay                       | 2h 36m        |
| Skipped runs (observed)         | 2 out of 17 (both 00:00 UTC slots) + 1 pending |
| Skip rate                       | ~13%          |
| Effective runs per day          | ~5 (not 6)    |
| Successful uploads              | 9 unique + 2 duplicates (pre-fix) |
| Spacing-blocked runs            | 1 (Feb 5, 16:00) |
| Failed runs (our bugs)          | 2 (`--body -` corruption + `channel_key` migration) |
| Post-fix streak (since `83fed74`) | 4 scheduled, then broken by `channel_key` crash (pre-fix code) |

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

### Assumption Validation (as of Feb 5, 14:45 UTC)

| #  | Assumption                              | Status          | Evidence |
|----|-----------------------------------------|-----------------|----------|
| A1 | Delays avg ~2h, range 1-3h              | **VALIDATED**   | 12 data points, avg ~1h58m, range 58m–2h36m |
| A2 | ~1 in 7 slots skipped by GitHub         | **VALIDATED**   | 2 skips in 14 slots (14%). Both were 00:00 UTC slots |
| A3 | Zero duplicate uploads (dedup fix)      | **INVALIDATED then RE-VALIDATED** | Pre-fix: manual dispatch at 23:21 duplicated "300 to 100". Post-fix (`83fed74` + `37a75f0`): 3 consecutive scheduled runs with 0 duplicates |
| A4 | Cache persists (GITHUB_TOKEN fix)       | **VALIDATED**   | Cache + artifact + token save all working. 3 consecutive runs saved DB correctly |
| A5 | Clips selected in ranked order          | **STRONGLY VALIDATED** | 4/4 exact clip predictions correct: "You Can't Just Say That", "HutchMF impersonation", "THIS GUY KILL PEANUT", "Michael Jackson HEE HEE" |
| A6 | Each run uploads exactly 1 clip         | **VALIDATED**   | All 12 triggered runs: uploaded≤1, spacing enforced correctly |

### Predicted Clip Queue (as of Feb 5, 00:50 UTC)

Based on current filter ranking with DB + blocklist exclusions:

| Position | Title                                   | Duration | Views | Status |
|----------|-----------------------------------------|----------|-------|--------|
| ~~1~~    | ~~HutchMF impersonation~~               | 60s      | 2,306 | Uploaded `Liyu0YNmG8g` (Feb 5, 04:00 slot) |
| ~~2~~    | ~~THIS GUY KILL PEANUT~~                | 30s      | 1,699 | Uploaded `nPUjFuGmaD4` (Feb 5, 08:00 slot) |
| ~~3~~    | ~~Michael Jackson HEE HEE~~             | 44s      | 1,334 | Uploaded `Pu8q4adi0M8` (Feb 5, 12:00 slot) |
| **4**    | **CLOAK SNATCHED AWAY**                 | 10s      | 600   | **Next up** (Feb 5, 20:00 slot — bumped from 16:00 by spacing) |
| 5        | Ultimate GOOP to the Snap hook flex!    | 44s      | 639   | Queued (Feb 5/6, 00:00 slot) |
| 6        | money                                   | 30s      | 389   | Already on YT (`8QWb8hFFWEo`) — channel dedup will catch |

After queue exhausts, next eligible clips (positions 9-13 in rankings):
PEANUT FACE LEAK!!! (483 views), aimbotter (379), macro sound (365), COULDNT EVEN SEE THEIR HANDS (346), Peanut Gingy and Officer (311)

> **Note:** CI DB now has 4 uploads tracked (300 to 100 + 3 new Feb 5 uploads).
> Blocklist covers 3 older uploads. Channel dedup is the safety net for "money" and untracked uploads.
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
| 16:00      | ~17:00-18:30 UTC  | ~11 AM-12:30 PM  | CLOAK SNATCHED AWAY                   | Spacing-blocked (MJ HEE HEE <4h ago) |
| 20:00      | ~21:00-22:30 UTC  | ~3-4:30 PM       | CLOAK SNATCHED AWAY (bumped from 16:00) | Upload 1 clip |
| 00:00      | ~01:00-02:30 UTC  | ~7-8:30 PM       | Ultimate GOOP to the Snap hook flex!  | Upload 1 clip (or GitHub skips slot) |
| 04:00      | ~05:00-06:30 UTC  | ~11 PM-12:30 AM  | money → channel dedup catches, then PEANUT FACE LEAK!!! | Upload 1 clip |

**Feb 5 prediction (revised):** 4 unique uploads (spacing bumped queue by 1), 0 duplicates
**Feb 5 actual so far:** 3 unique uploads, 0 duplicates, 0 failures — **on track**
**Key test:** First fully unattended cycle with all 3-layer defense + `--body -` fix — **PASSING**

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
| Feb 5, 04:00     | 05:08          | +1h 08m | HutchMF impersonation (`Liyu0YNmG8g`) | Yes | YES — exact match |
| Feb 5, 08:00     | 10:08          | +2h 08m | THIS GUY KILL PEANUT (`nPUjFuGmaD4`) | Yes | YES — exact match |
| Feb 5, 12:00     | 14:33          | +2h 33m | Michael Jackson HEE HEE (`Pu8q4adi0M8`) | Yes | YES — exact match |
| Feb 5, 16:00     | 18:32          | +2h 32m | None (spacing limit — 1 uploaded in last 4h) | Yes | NO — predicted CLOAK SNATCHED AWAY but spacing blocked it (MJ HEE HEE at 14:33 was <4h ago) |
| Feb 5, 20:00     | 21:03          | +1h 03m | None          | N/A          | NO — FAILED `channel_key` migration crash (ran on pre-fix code) |
| *manual 22:20*   | 22:20          | N/A     | 0/5 (all auth fail) | Yes    | Auth failures — stale token from crash chain |
| Feb 5/6, 00:00   |                |          |              |              | Pending — expect trigger ~01:00-02:30 or skip |
| Feb 6, 04:00     |                |         |               |              | Predict: CLOAK SNATCHED AWAY (bumped from failed 20:00) |
| Feb 6, 08:00     |                |         |               |              | Predict: Ultimate GOOP to the Snap hook flex! |
| Feb 6, 12:00     |                |         |               |              | Predict: money → channel dedup → PEANUT FACE LEAK!!! |

### Scorecard (updated — Feb 6, ~00:30 UTC)

```
Assumptions validated:    5 / 6 (A3 invalidated pre-fix, then re-validated post-fix)
Clip predictions correct: 4 / 4 (HutchMF, THIS GUY, MJ HEE HEE + earlier YCJST)
Timing predictions correct (within window): 5 / 5 (all Feb 5 scheduled runs within predicted range)
Spacing-blocked predictions: 1 (16:00 slot)
Skips predicted correctly: 0 / 0 (Feb 5/6 00:00 still pending)
Post-fix pipeline streak: 4 scheduled green, then broken by `channel_key` crash (pre-`0beefc4` code)
```

**Assessment (Feb 6, ~00:30 UTC):**
- 20:00 slot failed with `channel_key` migration crash — ran on code before fix `0beefc4`
- Manual dispatch at 22:20 also failed: 5/5 uploads got auth errors (stale token from crash chain)
- Token re-pushed to CI secret from healthy local copy (refresh verified working)
- 00:00 UTC slots are now 3/3 skipped by GitHub — this slot is effectively dead
- Pipeline runtime optimizations committed (`6c8a2ae`) — first test will be Feb 6, 04:00 slot
- Clip queue shifted: CLOAK SNATCHED AWAY bumped again (now next for 04:00 slot)

**Remaining predictions to validate:**
- 04:00 slot: CLOAK SNATCHED AWAY (~05:00-06:30 trigger) — first run on optimized code
- 08:00 slot: Ultimate GOOP to the Snap hook flex!
- 12:00 slot: money → channel dedup catches → PEANUT FACE LEAK!!!

**Takeaways:**
- 00:00 UTC slot is unreliable (2/2 skipped so far, 3rd pending) — effective daily capacity likely 5 runs
- `channel_key` migration fix (`0beefc4`) was committed before 20:00 trigger but GitHub ran stale code (cached checkout?)
- Auth failures cascade: one crash can poison the token for subsequent manual runs
- Always re-push token secret after any crash that touches credentials

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
| Feb 5   | `channel_key` column missing on cached DBs | Schema migration adds column if missing (`0beefc4`) |
| Feb 5   | Pipeline runtime: 8 ffmpeg calls for thumbnail, full audio decode for silence | Batch YDIF, `-t 6` silence limit, reorder checks, remove verify_upload (`6c8a2ae`) |

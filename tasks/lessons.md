# Lessons Learned

## 2026-02-02 — Initial Audit

- **Score vs filter disconnect**: When building a scoring system, make sure the filtering threshold actually uses the score. Easy to compute a fancy metric and then threshold on a raw field instead.
- **Silent error suppression**: Bare `except: pass` blocks are tech debt time bombs. Always log at minimum `log.warning()` so failures are visible in production.
- **Track ALL failure paths in DB**: If you track success in the DB, you must also track failure. Otherwise failed items re-enter the pipeline forever.
- **INSERT OR IGNORE is dangerous with pre-existing rows**: If another code path can insert the same primary key first (e.g., fail tracking), `INSERT OR IGNORE` will silently drop your data. Use `INSERT ... ON CONFLICT UPDATE` instead.
- **Filter early, process late**: If the API gives you metadata (like duration), filter on it before downloading. Don't waste bandwidth on clips you'll reject.

## 2026-02-02 — Competition Audit

### Verify claims empirically before acting
- Two "critical" bugs from the initial deep audit were false positives:
  - `julianday()` with `Z` and `+00:00` — both work fine in SQLite. Tested.
  - `bash -e` prevents empty credential overwrite in CI. Tested.
- One was a **true positive wrongly dismissed**: `os.kill(pid, 0)` on Windows does NOT reliably detect dead processes. Python's implementation calls `OpenProcess` and returns success for recently-dead PIDs without checking `GetExitCodeProcess`. The proper fix uses ctypes to call `OpenProcess` + `GetExitCodeProcess` against `STILL_ACTIVE (259)`.
- **Rule**: If a finding claims something "silently fails" or "does the opposite," run a 3-line test before writing the fix. But also re-test your own "disproven" findings — the test itself might have been wrong.

### Multi-agent audits need a verification pass
- Deploying multiple agents in parallel produces thorough coverage but also conflicting claims. Two agents said `julianday` was broken, two said it wasn't. Without empirical testing, you'd flip a coin.
- **Rule**: After parallel agent audits, run a dedicated verification pass that tests every disputed finding. The truth is in the runtime, not the reasoning.

### Tests are the highest-ROI structural change
- The entire agent roster (4 specialized agents) missed the most obvious gap: zero test files. Karen caught it. A superintelligent judge stops reading at "no tests."
- **Rule**: Before auditing code quality, check for tests first. A well-tested codebase with style nits beats a polished codebase with no proof it works.

### Typed models pay for themselves immediately
- Replacing raw dicts with dataclasses caught implicit assumptions (missing fields, wrong types) at definition time. Every `clip["key"]` → `clip.key` change is a future KeyError prevented.
- **Rule**: If dicts flow through 3+ functions, promote to a dataclass. The refactor is mechanical but the safety is permanent.

### Don't duplicate expensive work across retry paths
- `_measure_loudness` (full audio decode) was called inside `_run_ffmpeg`, which runs twice on GPU→CPU fallback. Common pattern: expensive computation buried inside a function that gets called in a retry loop.
- **Rule**: If a function is called in a retry/fallback pattern, audit whether it contains expensive invariant work that should be hoisted to the caller.

### GitHub Actions caches are immutable — plan for it
- Once a cache key exists, `cache/save` silently fails. Two viable patterns:
  1. **Static key + delete-before-save** — requires `permissions: actions: write` on the job and `GITHUB_TOKEN` (not a PAT without `actions` scope).
  2. **Dynamic key (`run_id`) + `restore-keys` prefix** — no delete needed, caches auto-evict at 10GB/7 days.
- The `run_id` approach at `8cb34a1` was actually working. It was prematurely reverted to a static key pattern that used a broken PAT → 30 hours of duplicate uploads.
- **Rule**: Never revert a working fix before the replacement is proven. Test with `workflow_dispatch` first.

### `excluded.*` in SQLite upserts prevents parameter duplication
- `INSERT ... ON CONFLICT DO UPDATE SET col=?` requires passing the same value twice. `SET col = excluded.col` references the INSERT values directly — fewer params, no risk of double `datetime.now()` returning different values.
- **Rule**: Always use `excluded.*` in SQLite/Postgres upserts. Never duplicate bind parameters.

### Fail-count thresholds prevent infinite retry loops
- `increment_fail_count` inserted DB rows for failed clips, but `filter_new_clips` excluded ALL existing clip IDs — meaning a clip that failed once was never retried, while a clip not yet in the DB would be retried forever. The fix: exclude clips with `youtube_id IS NOT NULL OR fail_count >= 3`, allowing 1-2 retries before giving up.
- **Rule**: Every retry system needs a circuit breaker. Track attempt count and define "permanently failed."

### Cut busywork from competition submissions
- Import reordering, augmented assignment, and micro-optimizations on 6-element lists signal "ran a linter" not "thinks like a staff engineer." These were correctly cut from the final plan.
- **Rule**: Every diff line should pass the test: "Would a senior engineer notice this in review?" If not, it's noise that dilutes the signal from real improvements.

## 2026-02-04 — CI Cache Bug Post-Mortem (Duplicate YouTube Uploads)

### Don't stack error suppression — you need visibility
- The cache delete step had `if: always()` + `continue-on-error: true` + `|| true`. The step showed green even when it 403'd. The job always reported success, hiding the broken cache for 30 hours.
- **Rule**: Use `continue-on-error: true` OR `|| true`, not both. A step that fails should be visually distinguishable from one that succeeds. Remove `|| true` when `continue-on-error` is already set.

### Add concurrency controls to stateful pipelines
- Without a `concurrency` block, two overlapping runs can both restore the same stale DB, both upload the same clip, then clobber each other's cache saves. This is a silent data-loss race condition.
- **Rule**: Any workflow that mutates shared state (caches, DBs, secrets) must have `concurrency: { group: <name>, cancel-in-progress: false }`. Queue, don't overlap.

### Verify which commit CI actually ran before diagnosing further
- The 04:58 UTC run was blamed on a broken fix, but it was running the *previous* commit. Scheduled triggers pick up HEAD at enqueue time, not at execution time.
- **Rule**: Before concluding a fix didn't work, check the commit SHA in the CI run logs.

### `GITHUB_TOKEN` vs PAT scopes are different failure modes
- `GH_PAT` lacking `actions:write` → 403 with "Resource not accessible by personal access token."
- `GITHUB_TOKEN` without `permissions: actions: write` in YAML → 403 with a different message.
- `GITHUB_TOKEN` WITH the permissions block → works, regardless of repo-level default permissions (the explicit block overrides).
- **Rule**: For cache deletion, use `GITHUB_TOKEN` + explicit `permissions: actions: write`. Don't rely on PATs for operations that `GITHUB_TOKEN` can handle natively.

### SQLite WAL mode + CI caching = potential data loss
- WAL mode creates `-wal` and `-shm` sidecar files. If only the main `.db` file is cached and the process is killed before `conn.close()` checkpoints the WAL, committed data can be lost.
- **Rule**: Add a `PRAGMA wal_checkpoint(TRUNCATE)` step before cache save to flush WAL into the main DB file. Belt and suspenders.

### Never revert a working fix before the replacement is proven
- The `run_id` cache key approach (`8cb34a1`) was working. It was reverted to a static key + delete pattern using a PAT without the right scope. The revert caused a DB reset and more duplicate uploads.
- **Rule**: Test the replacement with `workflow_dispatch` before reverting what works. The cost of a bad revert is higher than the cost of cache bloat.

## 2026-02-04 — Codex Hardening Review

### `HttpError.error_details` can be a string, not just a list
- When YouTube (or a WAF/CDN in front of it) returns a non-JSON body, `error_details` is a raw string. Iterating over it yields individual characters, and calling `.get()` on a character crashes with `AttributeError`. This was a pre-existing bug that Codex extracted into a helper without fixing.
- **Rule**: Always check `isinstance(error_details, list)` before iterating. Don't assume API error responses are always well-structured JSON.

### ctypes on 64-bit Windows needs explicit `restype`
- `ctypes` defaults to `c_int` (32-bit) return type for all foreign functions. On 64-bit Windows, `HANDLE` is a pointer (64-bit). `OpenProcess` returns a handle that could be truncated to 32 bits, causing silent corruption.
- **Rule**: Always set `.restype` and `.argtypes` on ctypes foreign function calls. Never rely on the default `c_int` return type.

### ALWAYS sync CI database before running pipeline locally
- Ran `python main.py` locally without downloading the CI database cache first. CI had already uploaded "You Can't Just Say That" but the local DB didn't know about it → duplicate upload to YouTube.
- The CI and local databases are completely separate (`data/clips.db` is gitignored, CI caches its own copy).
- **Rule**: Before ANY local pipeline run (dry or real), download the CI database to `data/clips.db`. Never assume the local DB is in sync with CI. Build a `sync-db.sh` script to make this frictionless.

### Non-quota 403s should skip, not halt — but add a circuit breaker
- YouTube 403s can be clip-specific (content policy) or channel-level (suspension/strike). Halting the entire pipeline on any 403 is too conservative (wastes remaining quota). Skipping without limit is too aggressive (burns quota on doomed retries if the channel is banned).
- **Rule**: Skip on 403 but track consecutive failures. After 3 in a row, assume the issue is channel-level and stop uploading for that streamer.

## 2026-02-04 — Upload Reliability (3-Layer Duplicate Defense)

### Record state BEFORE fallible post-processing
- There was a ~40-line gap between `upload_short()` returning a youtube_id and `insert_clip()` writing it to DB. If `verify_upload()` or `set_thumbnail()` crashed/returned false, the upload was orphaned — no DB record, so the next run would re-upload.
- A phantom DB entry (upload recorded but verify failed) is trivially cleanable. A duplicate YouTube upload is not.
- **Rule**: When an operation has an irreversible side effect (upload to external service), record it to your local state store immediately. Do verification and enrichment after. "Record then verify" beats "verify then record."

### Defense-in-depth beats single-point-of-failure state
- SQLite DB was the sole dedup mechanism. It lived on ephemeral CI runners with fragile cache persistence. Cache miss = full re-upload of everything.
- Fix: 3 independent layers — (1) DB insert before verify, (2) artifact fallback when cache misses, (3) YouTube channel title check before upload.
- **Rule**: For destructive/irreversible operations, never rely on a single state source. Add at least one fallback that can reconstruct state from the authoritative external system.

### `playlistItems.list` is 50x cheaper than `search.list` for channel dedup
- YouTube `search.list` costs 100 quota units. `channels.list` (1 unit) + `playlistItems.list` (1 unit per page of 50) = 2 units to check 50 recent uploads.
- **Rule**: Always check YouTube API quota costs before choosing an endpoint. The "obvious" endpoint (`search`) is often the most expensive.

### Extract shared logic when adding pre-checks
- The duplicate check needed the same title that `upload_short()` would generate. Rather than duplicating template rendering, extract `build_upload_title()` and have both call it.
- **Rule**: When a pre-check needs the same derived value as the main operation, extract the computation into a shared function. Don't duplicate the logic.

### Guard CI secret saves against empty/missing files
- `if: always()` on token save is correct (refreshed tokens must persist even on pipeline failure), but without a file existence check, a failed run that never created the credentials file would overwrite the secret with empty data.
- `base64: invalid input` on restore is the telltale sign of a corrupted secret.
- **Rule**: Always wrap `gh secret set` with `if [ -s file ]` to prevent saving empty/missing files. The `if: always()` and the file guard serve different purposes — you need both.

### Artifacts are a reliable cache fallback
- GitHub Actions cache can be evicted (10GB limit, 7-day TTL) or lost (delete succeeds but save fails). Artifacts from `upload-artifact` are independently stored and retained for the configured period.
- `gh run download --name <artifact>` in a fallback step after cache miss provides a second restoration path at zero additional cost (artifacts are already uploaded on success).
- **Rule**: For critical state, upload as both cache (fast restore) and artifact (durable fallback). Use `cache-hit` output to conditionally download artifact.

### `gh secret set --body -` does NOT read from stdin
- `gh secret set NAME --body -` sets the secret to the **literal string `"-"`**, not stdin. This is unlike most CLI tools where `-` means stdin. The `gh` CLI reads from stdin only when `--body` is omitted entirely.
- This was a silent time bomb: every successful pipeline run saved `"-"` as the token secret, corrupting it for the next run. The next run would fail at `base64 -d` with `base64: invalid input`.
- **Correct**: `base64 -w0 file | gh secret set NAME` (no `--body` flag)
- **Wrong**: `base64 -w0 file | gh secret set NAME --body -`
- **Rule**: Never use `--body -` with `gh secret set`. Omit `--body` to read from stdin. When re-setting secrets manually, prefer Python: `python -c "import base64; print(base64.b64encode(open('file','rb').read()).decode())" | gh secret set NAME`.

### A self-corrupting pipeline can pass N times before you notice
- The `--body -` bug was present from the start but masked: we'd manually re-set secrets, the next run would succeed, its token save would corrupt the secret, and the following run would fail. We blamed "secret corruption" without realizing the pipeline itself was the corruption source.
- Two consecutive successful runs is the minimum bar for proving a CI secret round-trips correctly. One success proves nothing — it might be consuming a manually-set good value while writing back garbage.
- **Rule**: After fixing any CI secret handling, trigger two back-to-back runs. If the second one fails at credential restore, the save step is corrupting the secret.

## 2026-02-05 — Upload Spacing vs Cron Interval

### Spacing thresholds must account for execution delay variance
- `upload_spacing_hours: 4` matched the cron interval (every 4h), leaving zero tolerance for GitHub Actions delay variance. Delays range 58m–2h36m, so the worst-case wall-clock gap between consecutive slots is `4h - (max_delay - min_delay)` = ~2h22m. A 4h spacing check on actual upload times (`posted_at`) blocked valid cron slots whenever `slot_B_delay < slot_A_delay`.
- **Rule**: `upload_spacing_hours` must be strictly less than `cron_interval - max_delay_variance`. With 4h cron and ~1h38m observed variance, 2h gives safe margin. The cron schedule + `max_uploads_per_window` are the primary rate limiters — spacing is just a safety net against rapid-fire manual runs.

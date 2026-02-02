# Twitch-to-Shorts: Final Competition Plan

> Synthesized from 6 agent perspectives: 2x Opus deep auditors + @Jenny + @code-quality-pragmatist + @task-completion-validator + @karen
> All disputed findings resolved with empirical tests. False positives eliminated.

---

## Eliminated False Positives (from previous plan)

These were claimed as critical but empirically disproven:

| Claim | Verdict | Evidence |
|-------|---------|----------|
| `os.kill(pid, 0)` kills on Windows | **False positive** | Python 3.2+ handles signal 0 safely. Tested on 3.12. |
| `julianday()` NULL on Z/+00:00 | **False positive** | Both `Z` and `+00:00` return valid julian days in SQLite. Tested. |
| Empty credential overwrites secret | **False positive** | `bash -e` (GitHub Actions default) aborts on `base64` failure. Tested. |
| Upload retry loop unbounded | **False positive** | 3 retries per chunk, total bounded by file size. |
| Facecam fails on short clips | **False positive** | Only affects <1s clips. Twitch minimum clip length is 5s. |

---

## Wave 1: Structural (What a Staff Engineer Notices First)
*These signal engineering maturity. A judge sees zero tests and stops reading.*

- [ ] **Add test suite** — Zero test files in the entire repo. Add focused unit tests for the core logic: `clip_filter.py` (scoring, thresholding, edge cases), `dedup.py` (overlap detection, batch query), `db.py` (upsert semantics, stats rollup, overlap window). Mock external APIs. ~20 tests. Use `pytest`. This is the single highest-impact change for competition scoring.

- [ ] **Add typed data models** — Clips are raw `dict` everywhere. One typo = runtime `KeyError`. Replace with `@dataclass` or Pydantic `BaseModel` for `Clip`, `StreamerConfig`, `PipelineConfig`. Self-documenting, catches bugs at definition time, shows architectural discipline.

- [ ] **Add `.pre-commit-config.yaml`** with `ruff` (lint + format) and `mypy` (type checking). Replaces 5+ manual style fixes with automated tooling. Shows systems thinking.

## Wave 2: Real Bugs (Confirmed by 3+ agents, empirically verified)

- [ ] **Fix CI cache key to prevent DB eviction** — `pipeline.yml:35` uses `clips-db-${{ github.run_id }}` (unique per run), accumulating cache entries until 10GB eviction. Fix: stable key `clips-db` or better, use a release artifact for the DB. *(Confirmed by all 4 agents)*

- [ ] **Hoist `_measure_loudness` out of `_run_ffmpeg`** — `video_processor.py:286` runs a full audio decode inside `_run_ffmpeg`, which is called twice (GPU then CPU fallback). On CI (no GPU), loudness is measured twice for zero benefit. Compute once in `crop_to_vertical`, pass result in. *(Confirmed by all 4 agents)*

- [ ] **Guard Ratelimit-Reset header parsing** — `twitch_client.py:49` `int(reset)` crashes on non-numeric header values. Wrap in try/except with fallback to 5s. *(Confirmed by Jenny + Code Quality)*

- [ ] **Sanitize clip titles for YouTube** — `youtube_uploader.py:84` only strips `<>`. Twitch titles can contain newlines, control chars, causing YouTube API rejection recorded as permanent failure via `increment_fail_count`. Use `re.sub(r'[\x00-\x1f<>]', '', title)`. *(Confirmed by Jenny + Code Quality)*

## Wave 3: Code Quality (Things that signal "every line was chosen deliberately")

- [ ] **Remove dead `if youtube_id:` guard** — `main.py:237` is unreachable after `if not youtube_id: continue` on line 233. Signals the author tracks control flow. *(Confirmed by all 4 agents)*

- [ ] **Use `excluded.*` in `update_streamer_stats` upsert** — `db.py:92-98` duplicates 3 parameters and calls `datetime.now()` twice (potentially different timestamps). Use `excluded.avg_views_30d` etc. Fixes a subtle bug while being more idiomatic. *(Confirmed by Jenny + Code Quality)*

- [ ] **Eliminate redundant re-sort** — `clip_filter.py:41` builds a `scores` list and sorts it when `clips` is already sorted descending at line 34. Direct index lookup: O(1) vs O(n log n). Shows algorithmic awareness. *(Confirmed by Code Quality + Jenny)*

- [ ] **Unify binary discovery** — `downloader.py:9-15` hand-rolls Windows path search with `os.sys.executable` (undocumented alias) while `video_processor.py` uses `shutil.which`. Replace with `shutil.which("yt-dlp") or "yt-dlp"`. *(Confirmed by Jenny + Code Quality)*

- [ ] **Merge duplicate retry blocks** — `youtube_uploader.py:120-139` has two near-identical except blocks. Merge with a `retryable` predicate. *(Confirmed by Code Quality)*

- [ ] **Clean up tmp on failed crop** — `main.py:203-214` when `crop_to_vertical` fails, the downloaded raw file isn't cleaned up. Add `os.remove(video_path)` after `increment_fail_count`. *(Confirmed by Jenny)*

## Wave 4: Polish (Only if time permits)

- [ ] **Simplify `_has_facecam` Popen to `subprocess.run`** — `video_processor.py:174-196` has 23 lines of manual Popen lifecycle per sample point. No temp file cleanup needed here, so `subprocess.run(timeout=15)` is a clean win. *(Identified by Code Quality as missed by original plan)*

- [ ] **Add `fail_count` threshold** — `db.py` + `dedup.py`: clips that always fail processing retry every 4-hour pipeline run forever. Add `AND fail_count < 3` to the dedup query. *(Missed by original plan, identified by Jenny)*

- [ ] **Add pipeline summary metrics** — At end of `run_pipeline`, log structured summary: clips fetched/filtered/downloaded/processed/uploaded/failed. Shows operational maturity. *(Identified by Karen)*

---

## Prioritized Execution Order

| # | Item | Impact Signal |
|---|------|---------------|
| 1 | Add test suite | "I prove my code works" |
| 2 | Add typed data models | "I design before I code" |
| 3 | Fix CI cache key | "I understand infrastructure" |
| 4 | Hoist loudness measurement | "I understand FFmpeg pipelines" |
| 5 | Remove dead if-guard + fix upsert | "I read every line" |
| 6 | Eliminate redundant sort | "I think about algorithms" |
| 7 | Sanitize titles + guard rate-limit | "I handle the real world" |
| 8 | Merge retry blocks + unify binary discovery | "I value consistency" |
| 9 | Add fail_count threshold | "I think about steady-state behavior" |
| 10 | Add pre-commit + pipeline metrics | "I build systems, not scripts" |

---

## What Was Cut (and why)

| Dropped Item | Reason |
|-------------|--------|
| `os.kill` Windows fix | Empirically disproven — works on Python 3.2+ |
| `julianday()` timezone fix | Empirically disproven — SQLite handles both Z and +00:00 |
| Credential overwrite guard | Empirically disproven — bash -e prevents it |
| Upload retry cap | Not actually unbounded — bounded per chunk |
| Facecam short-clip fix | Twitch minimum clip is 5s — bug can't trigger |
| Sort imports | Linter work, not engineering signal |
| Augmented assignment | Zero semantic value |
| Single glob pattern | Micro-optimization nobody notices |
| DRY twitch config | Saving 1 line of dict access |
| `os.makedirs("")` guards (3 files) | Config always provides directory-prefixed paths |
| Legacy Z replacement | Working code, Python version awareness is a weak signal |
| Popen→subprocess.run in _run_ffmpeg | Marginal gain, regression risk on critical path |
| Facecam coordinate validation | Static author-controlled config |
| `getattr(e, 'error_details')` | Current library version has the attribute |

---

## Review Notes
- Audit date: 2026-02-02
- Agents consulted: 2x Opus deep auditors, @Jenny, @code-quality-pragmatist, @task-completion-validator, @karen
- Disputed findings resolved with empirical testing (SQLite julianday, os.kill on Win, bash -e)
- 14 items cut as false positives or low-ROI busywork
- 3 items added that all agents missed (tests, typed models, pre-commit)
- Final plan: 16 items across 4 waves, ordered by judge impact

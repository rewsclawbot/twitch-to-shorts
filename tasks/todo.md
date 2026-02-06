# Active Tasks

## Decision: ffmpeg CPU encode preset

A clip timed out during CPU encoding on GitHub Actions (hit the 300s limit). The pipeline fell through to the next clip and uploaded successfully.

**Current setting:** `src/video_processor.py:392` — `-preset medium`
**Proposed change:** `-preset fast`

### Context
- GitHub Actions runners use CPU-only encoding (`DISABLE_GPU_ENCODE=1`, no CUDA)
- The timeout is 300s (5 min) at `_run_ffmpeg` line 416
- Clip `LazyStrangeMagpieCmonBruh-SISzVDIKz5q259Op` timed out at exactly 300s
- The next clip (`BeautifulObedientDillDansGame-QU1e0B6gKVA9eyLV`) encoded in ~2 min
- Output is 1080x1920 vertical video, uploaded as YouTube Shorts
- YouTube re-encodes all uploads to its own formats

### Option A: Change `-preset medium` to `-preset fast`
- ~40-50% faster encode times
- ~5-10% larger files at same CRF 20
- Visual quality identical (CRF controls quality, preset controls compression search effort)
- Fewer timeouts, faster pipeline runs overall
- Tradeoff: slightly larger upload size (e.g., 32MB vs 30MB for a 60s clip)

### Option B: Keep `-preset medium`
- Current behavior, optimized for compression efficiency
- Some clips will continue to time out on slower runners
- The fallthrough mechanism handles it (skip to next clip)
- Tradeoff: occasional wasted slots when the top-ranked clip is long + runner is slow

### Option C: Increase timeout from 300s
- Would let slow encodes finish instead of killing them
- Risk: worst case 6 clips × higher timeout could approach GitHub Actions 60-min job limit
- Doesn't improve the normal case, only delays failure for stuck processes
- Tradeoff: longer wait before fallthrough on genuinely stuck encodes

### What to evaluate
- Is the quality difference between `medium` and `fast` visible on phone screens at 1080x1920?
- How often are clips hitting the 300s timeout? (check `gh run list` logs for "timed out")
- Does the larger file size matter for upload speed on GitHub Actions?

---

## Recently Completed

- [x] **Audit fix sprint** (2026-02-05) — All 28 findings fixed, 166 tests passing. See `docs/audit-history.md`.
- [x] **3-layer upload dedup defense** (2026-02-05) — DB-before-verify, artifact fallback, channel dedup.
- [x] **Latent bug fixes** (2026-02-05) — Upload starvation, dead retries, spacing poisoning.

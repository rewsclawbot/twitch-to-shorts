# Unified Audit Summary — 2026-02-09

**8 auditors** | **~134 raw findings** → **38 deduplicated** | Previous audit (Feb 5): 28 fixes all confirmed held

## Cross-Auditor Agreement Matrix

Issues flagged by 3+ auditors independently are highest confidence:

| Issue | Auditors | Confidence |
|-------|----------|------------|
| Caption-silence desync | jenny, captioner-review, reliability, performance | **Very High** |
| Audio file OOM / duration cap | reliability, captioner-review, performance | **Very High** |
| Playlist cache id(service) | reliability, architecture, pragmatist | **Very High** |
| Subtitle path escaping | security, reliability | High |
| Deepgram timeout | reliability, captioner-review | High |
| deepgram-sdk as hard dep | architecture, pragmatist | High |
| _remove_file duplication | architecture, pragmatist | High |
| Parameter explosion | architecture, pragmatist | High |
| MarginV=120 too low | jenny, captioner-review | High |
| Word grouping deviates | jenny, captioner-review | High |
| Text not uppercased | jenny, captioner-review | High |
| ass= vs subtitles= filter | jenny, captioner-review | High |

---

## P0 — Must Fix (12 items)

These are critical/high issues that must be resolved before enabling captions or next production push.

| # | Issue | Sources | Effort | Files |
|---|-------|---------|--------|-------|
| 1 | **Caption-silence desync**: ASS timestamps not adjusted for `-ss` trim. Up to 5s desync on clips with leading silence. Make `detect_leading_silence` public, pass offset to captioner. | SPEC-C1/C2/C3, CAP-H1 | M | captioner.py, video_processor.py, main.py |
| 2 | **Deepgram API timeout**: `transcribe_file()` has no timeout. Pipeline hangs indefinitely if Deepgram is slow. | R-C1, CAP-H3 | S | captioner.py |
| 3 | **Audio extraction guards**: No duration cap (`-t 65`) and no file size check before `f.read()`. OOM risk on corrupt files. | R-C2, CAP-C1, P-C1 | S | captioner.py, media_utils.py |
| 4 | **Playlist cache key**: `id(service)` reused after GC → stale cache → wrong channel dedup. Key by `credentials_file` instead. | R-C3, A-H4, CQ-M3 | S | youtube_uploader.py |
| 5 | **Subtitle path escaping**: `_escape_subtitle_path()` misses `'`, `;`, `[`, `]`. Could break ffmpeg filter. | S-H1, R-H4 | S | video_processor.py |
| 6 | **Clip ID validation**: No regex at Twitch API trust boundary. Add `^[a-zA-Z0-9_-]+$` check. | S-H2, S-H3 | S | twitch_client.py |
| 7 | **ASS special character escaping**: `{}`, `\N`, `\n` in dialogue text not escaped. Breaks subtitle rendering. | CAP-H5 | S | captioner.py |
| 8 | **Deepgram response validation**: No bounds checking on `response.results.channels[0].alternatives[0].words`. IndexError on unexpected response. | CAP-H4 | S | captioner.py |
| 9 | **Clean stale tmp suffixes**: Missing `.ass` and `.wav`. Stale caption files accumulate. | SPEC-M3, CAP-M4 | S | main.py |
| 10 | **Symlink check in clean_stale_tmp**: `os.remove()` follows symlinks, could delete files outside tmp_dir. | S-H4 | S | main.py |
| 11 | **extract_audio error handling**: Bare `check=True` gives opaque errors. No output validation. | R-H1, A-L2 | S | media_utils.py |
| 12 | **DB connection leak**: If `init_schema()` fails, connection leaks. Wrap in try/close. | R-H5 | S | db.py |

---

## P1 — Should Fix (14 items)

Important for caption quality, spec compliance, and reliability. Fix in same sprint if time allows.

| # | Issue | Sources | Effort | Files |
|---|-------|---------|--------|-------|
| 13 | **Text uppercase**: Spec requires `.upper()` on caption text. One-line fix. | SPEC-H2, CAP-M2 | S | captioner.py |
| 14 | **MarginV 120→400**: Captions positioned too low, overlapping phone UI. | SPEC-H3, CAP-M7 | S | captioner.py |
| 15 | **Outline 3→4, Bold -1→1**: Match spec for readability. | CAP-M6, CAP-M8 | S | captioner.py |
| 16 | **Word grouping: 3 words, 0.3s gap, punctuation breaks**: Significant spec deviation. | SPEC-H1, CAP-M1 | S | captioner.py |
| 17 | **ass= filter instead of subtitles=**: More correct for pre-generated ASS files. | SPEC-M8, CAP-C2 | S | video_processor.py |
| 18 | **Deepgram retry logic**: Only external API without retries. Add 2-3 attempts with backoff. | R-H2 | M | captioner.py |
| 19 | **Deepgram client singleton**: New client per clip is wasteful. Accept optional client param. | P-H1, CAP-H2 | S | captioner.py |
| 20 | **WAV→FLAC audio format**: Uncompressed WAV is 3x larger than FLAC. Deepgram accepts FLAC natively. | P-C1 | S | media_utils.py |
| 21 | **Cached crop output validation**: `crop_to_vertical` returns stale non-empty but corrupt files. Add `is_valid_video` check. | R-H3, A-L1 | S | video_processor.py |
| 22 | **_format_ass_time rounding**: Centisecond overflow at boundary (1.999→0:00:01.99 instead of 0:00:02.00). | A-M4, A-M6 | S | captioner.py, test_captioner.py |
| 23 | **captions_enabled in PipelineConfig**: Currently bypasses validated config. Add to dataclass. | SPEC-H6, A-M2 | S | models.py, main.py |
| 24 | **Consolidate _remove_file**: Duplicated in captioner.py, video_processor.py, main.py. Move to media_utils. | A-H3, CQ-M7 | S | media_utils.py, captioner.py, video_processor.py |
| 25 | **deepgram-sdk optional**: Move to `requirements-captions.txt`. Reduces install footprint. | A-H2, CQ-H3 | S | requirements.txt |
| 26 | **Deepgram API key startup warning**: Spec requires soft warning if enabled but no key. | SPEC-M4 | S | main.py |

---

## P2 — Nice to Have (12 items)

Lower priority. Address in future sprints or as time permits.

| # | Issue | Sources | Effort | Files |
|---|-------|---------|--------|-------|
| 27 | YouTubeConfig dataclass (parameter explosion 18→8 params) | A-H1, CQ-H1 | M | main.py, models.py |
| 28 | Delete dead `_sample_ydif` | CQ-M4 | S | video_processor.py |
| 29 | Fix lazy import of `extract_audio` (move to top-level) | A-M1, CQ-M6 | S | captioner.py |
| 30 | Per-streamer `captions` config field | SPEC-H5, CAP-M3 | S | models.py, main.py |
| 31 | String result codes → enum | CQ-H2 | M | main.py |
| 32 | FacecamConfig bounds validation | R-M9 | S | models.py |
| 33 | Twitch pagination max_pages guard | R-M5 | S | twitch_client.py |
| 34 | Invalid timestamp bypasses batch dedup | R-M6 | S | dedup.py |
| 35 | `julianday()` → `created_at_epoch` column (prev audit, still open) | P-C2 | M | db.py |
| 36 | README updates (5 stale sections) | SPEC-L1–L5 | S | README.md |
| 37 | YouTube API timeout regression (fix #4 lost with AuthorizedHttp removal) | Jenny | M | youtube_uploader.py |
| 38 | Combine silence detection + loudnorm into single ffmpeg pass | P-H5 | M | video_processor.py |

---

## Fix Team Design

### File Ownership Map

| Owner | Files | P0 Tasks | P1 Tasks |
|-------|-------|----------|----------|
| **captioner-fixes** | captioner.py, media_utils.py | #1(captioner part), #2, #3, #7, #8, #11 | #13, #14, #15, #16, #18, #19, #20, #22, #24(captioner) |
| **pipeline-fixes** | main.py, models.py, db.py, dedup.py, twitch_client.py | #6, #9, #10, #12 | #23, #26 |
| **media-fixes** | video_processor.py, youtube_uploader.py | #1(video_processor part), #4, #5 | #17, #21, #24(video_processor) |
| **test-and-docs** | tests/*, requirements.txt, README.md | — | #22(test), #25, #36 |

### Execution Order
1. **captioner-fixes** and **pipeline-fixes** start in parallel (no file conflicts)
2. **media-fixes** starts in parallel (no file conflicts with above)
3. **test-and-docs** starts after all code fixes land (tests may reference changed code)
4. Verification gate: `python -m pytest tests/ -v` must pass

---

## Overall Assessment

**Grade: B+ (unchanged from Karen's assessment)**

The core pipeline is battle-tested and production-solid. All 28 Feb 5 audit fixes are confirmed held. The new captioner module is the dominant risk surface — 25 of 38 findings relate to it. The captioner works end-to-end but has spec deviations, missing safety guards, and the silence desync bug that would produce out-of-sync captions on any clip with leading silence.

**Key insight**: The captioner was built with the right architecture (graceful degradation, clean integration) but rushed on details (spec parameters, safety bounds, character escaping). A focused fix sprint on the P0 items would make it production-ready.

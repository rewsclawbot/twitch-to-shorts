# Add Auto-Captions to twitch-to-shorts

## Context
YouTube Shorts watched on mute (70%+) benefit massively from burned-in captions. The sister project (`twitch-to-youtube-gaming`) already uses Deepgram for transcription. This plan adds Deepgram transcription + ASS subtitle generation + ffmpeg burn-in to the Shorts pipeline, making captions a configurable per-streamer feature.

## Production Safety
- **Feature branch**: All work done on `feature/auto-captions`, not merged to master until verified
- **Default off**: `captions_enabled: false` in config — production pipeline unchanged until explicitly toggled
- **Non-blocking**: Any caption failure gracefully degrades to no-captions (existing behavior)
- **All tests must pass** (existing + new) before merge is considered

## Team Structure
| Agent | Role | Tasks |
|---|---|---|
| **models-agent** | Models + transcriber | `src/models.py`, `src/transcriber.py`, `tests/test_transcriber.py` |
| **captions-agent** | Caption generator | `src/caption_generator.py`, `tests/test_caption_generator.py` |
| **integration-agent** | Video processor + pipeline wiring | `src/video_processor.py`, `main.py`, `config.yaml`, `requirements.txt` |

Models-agent runs first (others depend on `TranscriptWord`). Captions-agent and integration-agent can run in parallel after models are done.

## Overview
- Transcribe clip audio via Deepgram (word-level timestamps)
- Group words into 1-3 word chunks, generate styled ASS subtitle file
- Burn ASS into video during the existing ffmpeg encode step
- Fully non-blocking: any caption failure → proceed without captions

---

## Files to Create

### 1. `src/transcriber.py` — Deepgram integration
Ported from `twitch-to-youtube-gaming/src/transcriber.py` (reference: lines 1-152), simplified:
- `transcribe_clip(video_path: str, tmp_dir: str) -> list[TranscriptWord] | None`
  - Extract audio to WAV (16kHz mono) via ffmpeg (`-vn -acodec pcm_s16le -ar 16000 -ac 1`)
  - Call Deepgram Nova-2 (`smart_format=True, utterances=True, punctuate=True`)
  - Parse word-level timestamps, return list of `TranscriptWord`
  - Returns `None` if `DEEPGRAM_API_KEY` missing or any error occurs
  - Cleans up temp WAV file in `finally` block
- No caching (clips are short, one-shot processing)

### 2. `src/caption_generator.py` — ASS subtitle generation
- `generate_captions(words: list[TranscriptWord], output_path: str, silence_offset: float = 0.0) -> str | None`
  - Groups words into 1-3 word chunks via `_group_words()`
  - Adjusts timestamps by subtracting `silence_offset` (clamped to 0)
  - Writes styled ASS file, returns path on success, `None` on failure
- `_group_words(words) -> list[tuple[str, float, float]]` — grouping rules:
  - Max 3 words per chunk
  - Break on sentence punctuation (`.` `!` `?`)
  - Break on comma
  - Break when gap between words > 0.3s
  - Text upper-cased for Shorts style
- ASS style: Arial Bold 72pt, white text, black outline (4px), bottom-center alignment, MarginV=400 (positions text in lower-middle of 1080x1920 frame)

### 3. `tests/test_transcriber.py`
- Test returns `None` when no API key
- Test audio extraction ffmpeg command (mock subprocess)
- Test Deepgram response parsing (mock SDK)
- Test cleanup of temp WAV

### 4. `tests/test_caption_generator.py`
- Test word grouping: max 3, punctuation breaks, gap breaks, empty input
- Test silence offset subtracted from timestamps
- Test negative timestamps clamped to 0
- Test ASS time formatting (`H:MM:SS.cc`)
- Test ASS file structure (sections present)
- Test text uppercased

---

## Files to Modify

### 5. `src/models.py` — Add TranscriptWord
```python
@dataclass
class TranscriptWord:
    word: str
    start: float
    end: float
    confidence: float = 0.0
```
Add `captions: bool | None = None` to `StreamerConfig` (after `extra_tags`)
Add `captions_enabled: bool = False` to `PipelineConfig` (after `analytics_max_videos_per_run`)

### 6. `src/video_processor.py` — Burn-in subtitles
**`_detect_leading_silence`** (line 176): Rename to `detect_leading_silence` (make public)

**`crop_to_vertical`** (line 198): Add two params:
```python
def crop_to_vertical(input_path, tmp_dir, max_duration=60,
                     facecam=None, facecam_mode="auto",
                     ass_path=None, silence_offset=None):
```
- If `silence_offset is None`, detect internally (backward compatible)
- If `ass_path` provided, append ASS filter to the video filter chain:
  - Simple crop: `crop=...,scale=1080:1920,ass='escaped_path'`
  - Facecam composite: rename `[out]` → `[composite]`, add `[composite]ass='escaped_path'[out]`
- Path escaping for Windows: replace `\` → `/`, escape `:` → `\:`

### 7. `main.py` — Wire into pipeline
**`_process_single_clip`** (line 263): Add `captions_enabled` param. Between download (line 291) and `crop_to_vertical` (line 296), insert:
```
if captions_enabled:
    silence_offset = detect_leading_silence(video_path)
    transcript_words = transcribe_clip(video_path, cfg.tmp_dir)
    if transcript_words:
        ass_path = generate_captions(transcript_words, ..., silence_offset)
```
Pass `ass_path` and `silence_offset` to `crop_to_vertical`. Wrap in try/except — any failure → proceed without captions.

**`_process_streamer`** (line 360): Resolve captions setting (`streamer.captions ?? cfg.captions_enabled`), pass to `_process_single_clip`.

**`load_config`**: Pop `captions` from streamer dict before constructing `StreamerConfig`.

**`clean_stale_tmp`**: Add `.ass` and `.wav` to cleanup suffixes.

**`_cleanup_tmp_files`**: Pass and clean `ass_path`.

**`run_pipeline`**: Add soft warning if `captions_enabled` but no `DEEPGRAM_API_KEY`.

### 8. `config.yaml`
```yaml
pipeline:
  captions_enabled: false  # Requires DEEPGRAM_API_KEY env var
```

### 9. `requirements.txt`
Add: `deepgram-sdk>=3.0.0`

---

## Implementation Order
1. Create `feature/auto-captions` branch
2. **Phase 1 (models-agent):** `src/models.py` — Add `TranscriptWord`, config fields
3. **Phase 2 (parallel):**
   - **models-agent:** `src/transcriber.py` + `tests/test_transcriber.py`
   - **captions-agent:** `src/caption_generator.py` + `tests/test_caption_generator.py`
   - **integration-agent:** `src/video_processor.py` changes (public `detect_leading_silence`, ASS filter chain, new params)
4. **Phase 3 (integration-agent):** `main.py` wiring, `config.yaml`, `requirements.txt`
5. **Phase 4:** Run full test suite, fix any issues

## Verification
1. Run `pytest tests/ -v` — all existing + new tests pass
2. Set `DEEPGRAM_API_KEY` and `captions_enabled: true` in config
3. Run pipeline with `--dry-run` on a real clip — verify no crashes
4. Run on a real clip without dry-run — verify output video has visible captions
5. Test with facecam enabled — verify captions appear correctly over composite
6. Test with `captions_enabled: false` — verify transcriber is never called
7. Test with missing API key + captions enabled — verify warning logged, clip still uploads
8. Only merge `feature/auto-captions` → master after all above verified

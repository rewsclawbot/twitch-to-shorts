# Codex Task: Whisper Caption Fallback

## Context
This is a Twitch-to-YouTube-Shorts pipeline. The existing `src/captioner.py` uses Deepgram API for speech-to-text to generate captions (ASS subtitles). However, Deepgram requires an API key and costs money. We need a free, local fallback using OpenAI's Whisper model.

## Task
Modify `src/captioner.py` to add Whisper as a fallback when `DEEPGRAM_API_KEY` is not set. Create `tests/test_captioner_whisper.py` for new tests.

## Current Behavior (DO NOT BREAK)
- `captioner.py` has `generate_captions(audio_path, output_ass_path)` 
- Uses Deepgram API when `DEEPGRAM_API_KEY` env var is set
- Returns path to ASS subtitle file
- ASS styling: uppercase, bold, positioned at MarginV=400

## Requirements

### Changes to src/captioner.py

1. **Add `_transcribe_whisper(audio_path: str) -> list[dict]`**
   - Uses `whisper` package (openai-whisper) to transcribe locally
   - Model: "base" (good balance of speed/accuracy for short clips)
   - Returns list of segments: `[{"start": float, "end": float, "text": str}, ...]`
   - Handles errors gracefully (returns empty list on failure)

2. **Modify the main flow:**
   - If `DEEPGRAM_API_KEY` is set → use Deepgram (existing behavior, unchanged)
   - If `DEEPGRAM_API_KEY` is NOT set → try Whisper fallback
   - If neither works → return None (no captions, pipeline continues)
   - Log which backend is being used: `log.info("Using %s for captions", backend)`

3. **Add `_segments_to_ass(segments: list[dict], output_path: str) -> str`**
   - Convert Whisper segments to ASS subtitle format
   - Match EXACTLY the same ASS styling as the existing Deepgram path:
     - Uppercase text
     - Bold
     - MarginV=400
     - Same font, size, colors as existing ASS template
   - Return path to written ASS file

4. **Config:**
   - `CAPTION_BACKEND` env var: "deepgram", "whisper", or "auto" (default: "auto")
   - "auto" = try Deepgram first, fall back to Whisper
   - "whisper" = always use Whisper (even if Deepgram key exists)
   - "deepgram" = only use Deepgram (existing behavior)

### tests/test_captioner_whisper.py

Write tests (mock whisper to avoid needing the model downloaded):
1. `test_transcribe_whisper_success` — mock whisper.load_model and transcribe, verify segments
2. `test_transcribe_whisper_failure` — mock error, returns empty list
3. `test_segments_to_ass_format` — verify ASS output format matches expected styling
4. `test_segments_to_ass_uppercase` — all text is uppercased
5. `test_caption_backend_auto_no_deepgram` — without DEEPGRAM_API_KEY, falls back to whisper
6. `test_caption_backend_whisper_forced` — CAPTION_BACKEND=whisper uses whisper even with deepgram key
7. `test_caption_backend_deepgram_forced` — CAPTION_BACKEND=deepgram only uses deepgram

### Important
- Do NOT break existing Deepgram functionality
- Do NOT modify the ASS subtitle styling (must match existing format exactly)
- Read the existing captioner.py carefully before making changes
- Run existing tests after changes to ensure nothing breaks: `python -m pytest tests/ -v`

### Dependencies
Use `openai-whisper` package. Add a comment noting the dependency but don't modify requirements.txt.

### Style
- Follow existing code style in captioner.py
- Graceful degradation on all failures
- All new functions should have docstrings

# Codex Task: Title Optimizer Module

## Context
This is a Twitch-to-YouTube-Shorts pipeline. Twitch clip titles are created by viewers and are often garbage ("lol", "omg", "nice shot"). YouTube Shorts CTR is heavily driven by title quality. We need an AI-powered title rewriter.

## Task
Create `src/title_optimizer.py` and `tests/test_title_optimizer.py`.

## Requirements

### src/title_optimizer.py

1. **`optimize_title(clip_title, streamer_name, game_name, clip_id) -> str`**
   - Takes the raw Twitch clip title and context
   - Returns an optimized YouTube-friendly title
   - Must be deterministic per clip_id (use MD5 hash for A/B selection)
   - 50% of clips get AI-rewritten title, 50% keep original (for A/B testing)
   - The A/B split should use the same MD5 approach as `_choose_template` in youtube_uploader.py
   - If AI rewriting is disabled (no API key) or fails, return the original title
   - Max title length: 100 characters

2. **`_rewrite_title_with_llm(clip_title, streamer_name, game_name) -> str | None`**
   - Uses OpenAI API (gpt-4o-mini) to rewrite the title
   - System prompt should instruct the LLM to:
     - Make the title attention-grabbing and click-worthy for YouTube Shorts
     - Use emotional hooks, curiosity gaps, or action verbs
     - Keep it under 80 characters (leave room for game/streamer suffix)
     - UPPERCASE key words for emphasis
     - Include relevant emoji (1-2 max)
     - Don't make it clickbait that doesn't match the content
     - Base it on the original title — don't invent content
   - Returns the rewritten title or None on failure
   - Timeout: 10 seconds
   - Retry: 1 retry on failure with 2s backoff
   - Reads API key from `OPENAI_API_KEY` env var
   - If no API key, return None (don't crash)

3. **`_should_optimize(clip_id) -> bool`**
   - Returns True for 50% of clips (A/B split)
   - Uses `hashlib.md5(clip_id.encode()).hexdigest()` — if int(hex, 16) % 2 == 0, optimize
   - Deterministic: same clip_id always gets same decision

4. **Config support:**
   - Read `TITLE_OPTIMIZER_ENABLED` env var (default: "false")
   - Read `OPENAI_API_KEY` env var
   - If either is missing/false, return original title

5. **Logging:**
   - Log when title is rewritten: `log.info("Title rewritten for %s: '%s' -> '%s'", clip_id, original, rewritten)`
   - Log when keeping original: `log.info("Keeping original title for %s (A/B control)", clip_id)`
   - Log warnings on API failure

### tests/test_title_optimizer.py

Write comprehensive tests:
1. `test_should_optimize_deterministic` — same clip_id always returns same bool
2. `test_should_optimize_roughly_50_50` — over 1000 random IDs, split is ~50/50 (40-60% range)
3. `test_optimize_title_no_api_key` — returns original when OPENAI_API_KEY not set
4. `test_optimize_title_disabled` — returns original when TITLE_OPTIMIZER_ENABLED is not "true"
5. `test_optimize_title_control_group` — returns original for A/B control clips
6. `test_optimize_title_treatment_group` — calls LLM for treatment clips (mock the API)
7. `test_rewrite_title_with_llm_success` — mock OpenAI response, verify formatting
8. `test_rewrite_title_with_llm_failure` — mock API error, returns None
9. `test_rewrite_title_with_llm_timeout` — mock timeout, returns None
10. `test_optimize_title_truncation` — titles over 100 chars get truncated
11. `test_optimize_title_llm_failure_fallback` — when LLM fails, returns original

### Integration
Do NOT modify main.py or any other files. Just create the two new files.
The module should be importable and usable like:
```python
from src.title_optimizer import optimize_title
title = optimize_title(clip.title, clip.streamer, clip.game_name, clip.id)
```

### Dependencies
Use `openai` package. Add `openai>=1.0.0` to a comment at the top of the file (don't modify requirements.txt).

### Style
- Follow the existing code style in src/ (logging patterns, error handling, type hints)
- Use `log = logging.getLogger(__name__)`
- Graceful degradation on all failures (never crash the pipeline)
- All functions should have docstrings

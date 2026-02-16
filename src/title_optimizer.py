"""AI-powered Twitch clip title optimization for YouTube Shorts."""

# Dependency: openai>=1.0.0 (for local LLM fallback)

import hashlib
import logging
import os
import subprocess
import time

try:
    from openai import OpenAI  # type: ignore[import-not-found]
except ImportError:
    OpenAI = None

log = logging.getLogger(__name__)

_LLM_MODEL = "gpt-4o-mini"
_MAX_TITLE_LEN = 100
_LLM_MAX_TITLE_LEN = 80
_LLM_TIMEOUT_SECONDS = 8
_LLM_MAX_ATTEMPTS = 2
_LLM_RETRY_BACKOFF_SECONDS = 2


def _truncate_title(title: str, max_len: int) -> str:
    """Return title constrained to max_len with ellipsis when truncated."""
    if len(title) <= max_len:
        return title
    if max_len <= 3:
        return title[:max_len]
    return title[: max_len - 3].rstrip() + "..."


def _title_optimizer_enabled() -> bool:
    """Return whether title optimizer feature flag is enabled."""
    return os.environ.get("TITLE_OPTIMIZER_ENABLED", "false").strip().lower() == "true"


def _should_optimize(clip_id: str) -> bool:
    """Return deterministic 50/50 A/B decision for a clip ID."""
    digest = hashlib.md5(clip_id.encode("utf-8")).hexdigest()
    return int(digest, 16) % 2 == 0


def _template_fallback_title(clip_title: str, streamer_name: str, game_name: str) -> str:
    """Apply viral-style patterns to title without LLM (last resort fallback)."""
    # Common filler words to remove
    filler_words = {
        "just", "really", "very", "actually", "basically", "literally",
        "um", "uh", "like", "you know", "i mean", "kind of", "sort of"
    }
    
    words = clip_title.split()
    # Remove filler words
    filtered_words = [w for w in words if w.lower() not in filler_words]
    
    # Capitalize key action/impact words for emphasis
    impact_words = {
        "insane", "perfect", "crazy", "massive", "epic", "clutch", "impossible",
        "destroyed", "amazing", "best", "worst", "fails", "wins", "legendary",
        "unbelievable", "never", "first", "last", "only", "ever", "ace", "steal"
    }
    
    capitalized = []
    for word in filtered_words:
        if word.lower() in impact_words:
            capitalized.append(word.upper())
        else:
            capitalized.append(word)
    
    # Add strategic emphasis with punctuation (viral pattern)
    result = " ".join(capitalized)
    
    # Add game suffix if provided and not already in title
    if game_name and game_name.lower() not in result.lower():
        result = f"{result} | {game_name}"
    
    return _truncate_title(result, _LLM_MAX_TITLE_LEN)


def _rewrite_title_with_llm(clip_title: str, streamer_name: str, game_name: str) -> str | None:
    """Rewrite a clip title using Claude Opus (preferred) or OpenAI fallback."""
    _SYSTEM_PROMPT = (
        "You rewrite Twitch clip titles for YouTube Shorts to maximize click-through rate.\n"
        "CONTEXT: These are gaming highlight clips. The original titles are often inside jokes,\n"
        "streamer slang, or gibberish that ONLY makes sense to that streamer's community.\n"
        "Your job is to make them compelling to a GENERAL gaming audience on YouTube.\n\n"
        "Analytics show these patterns WIN:\n"
        "- Describe what HAPPENS in the clip (action-focused): 'Insane 1v5 clutch nobody expected'\n"
        "- Curiosity hooks that create a gap: 'He tried the DUMBEST strategy and it actually worked'\n"
        "- Numbers and specifics: '3 kills in 5 seconds', 'Level 1 vs Final Boss'\n"
        "- Conversational tone, like telling a friend about a wild moment\n"
        "Analytics show these patterns LOSE:\n"
        "- Keeping streamer inside jokes that outsiders won't understand\n"
        "- ALL CAPS without substance\n"
        "- Generic filler ('insane moment', 'you won't believe this')\n"
        "- Titles that require knowing the streamer to understand\n"
        "Rules:\n"
        "- Keep it under 70 characters (shorter = fully visible on mobile)\n"
        "- If the original title is an inside joke or incomprehensible, REWRITE it as a\n"
        "  compelling description of what likely happens in a gaming clip with that title\n"
        "- Include the game name with '| GameName' at the end\n"
        "- One emoji MAX, only if it adds genuine emphasis (not required)\n"
        "- Return ONLY the title text, nothing else"
    )
    user_prompt = (
        f"Original title: {clip_title}\n"
        f"Streamer: {streamer_name}\n"
        f"Game: {game_name}"
    )

    # Try Claude CLI first (uses OAuth via Claude Max plan — no API key needed)
    for attempt in range(_LLM_MAX_ATTEMPTS):
        try:
            full_prompt = f"{_SYSTEM_PROMPT}\n\n{user_prompt}"
            result = subprocess.run(
                ["claude", "-p", "--model", "sonnet"],
                input=full_prompt, capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                rewritten = result.stdout.strip().splitlines()[0].strip().strip("\"'")
                if rewritten:
                    log.info("Claude CLI title: '%s' -> '%s'", clip_title, rewritten)
                    return _truncate_title(rewritten, _LLM_MAX_TITLE_LEN)
            log.warning("Claude CLI returned empty/error for '%s' (rc=%d)", clip_title, result.returncode)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            log.warning("Claude CLI attempt %d failed for '%s'", attempt + 1, clip_title, exc_info=True)
        if attempt < _LLM_MAX_ATTEMPTS - 1:
            time.sleep(_LLM_RETRY_BACKOFF_SECONDS)
    log.warning("All Claude CLI attempts failed, falling back to local LLM")

    # Fallback to OpenAI-compatible API
    base_url = os.environ.get("LLM_BASE_URL")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not base_url and not api_key:
        log.info("No LLM API configured, using template fallback for '%s'", clip_title)
        return _template_fallback_title(clip_title, streamer_name, game_name)
    if OpenAI is None:
        log.warning("openai package not installed, using template fallback for '%s'", clip_title)
        return _template_fallback_title(clip_title, streamer_name, game_name)

    model_name = os.environ.get("LLM_MODEL_NAME", _LLM_MODEL)
    client_kwargs: dict[str, str] = {"api_key": api_key or "not-needed"}
    if base_url:
        client_kwargs["base_url"] = base_url

    client = OpenAI(**client_kwargs)
    for attempt in range(_LLM_MAX_ATTEMPTS):
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                timeout=_LLM_TIMEOUT_SECONDS,
            )
            content = ""
            if response.choices:
                content = response.choices[0].message.content or ""

            rewritten = content.strip().splitlines()[0].strip().strip("\"'")
            if not rewritten:
                log.warning("OpenAI returned empty rewritten title for '%s'", clip_title)
                return None
            return _truncate_title(rewritten, _LLM_MAX_TITLE_LEN)
        except Exception as err:
            if attempt < _LLM_MAX_ATTEMPTS - 1:
                log.warning(
                    "OpenAI title rewrite attempt %d/%d failed: %s (retrying in %ds)",
                    attempt + 1,
                    _LLM_MAX_ATTEMPTS,
                    err,
                    _LLM_RETRY_BACKOFF_SECONDS,
                )
                time.sleep(_LLM_RETRY_BACKOFF_SECONDS)
                continue
            log.warning("OpenAI title rewrite failed: %s", err)
            break

    # All LLM attempts failed, use template-based fallback
    log.info("All LLM attempts failed for '%s', using template fallback", clip_title)
    return _template_fallback_title(clip_title, streamer_name, game_name)


def optimize_title(
    clip_title: str,
    streamer_name: str,
    game_name: str,
    clip_id: str,
) -> str:
    """Return optimized title with deterministic A/B split and graceful fallback."""
    original = clip_title
    original_capped = _truncate_title(original, _MAX_TITLE_LEN)

    if not _title_optimizer_enabled():
        return original_capped

    if not os.environ.get("OPENAI_API_KEY") and not os.environ.get("LLM_BASE_URL"):
        return original_capped

    if not _should_optimize(clip_id):
        log.info("Keeping original title for %s (A/B control)", clip_id)
        return original_capped

    rewritten = _rewrite_title_with_llm(clip_title, streamer_name, game_name)
    if not rewritten:
        return original_capped

    rewritten_capped = _truncate_title(rewritten, _MAX_TITLE_LEN)
    log.info("Title rewritten for %s: '%s' -> '%s'", clip_id, original, rewritten_capped)
    return rewritten_capped

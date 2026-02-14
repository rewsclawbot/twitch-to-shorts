"""AI-powered Twitch clip title optimization for YouTube Shorts."""

# Dependency: openai>=1.0.0

import hashlib
import logging
import os
import time

try:
    from openai import OpenAI  # type: ignore[import-not-found]
except ImportError:
    OpenAI = None

log = logging.getLogger(__name__)

_LLM_MODEL = "gpt-4o-mini"
_MAX_TITLE_LEN = 100
_LLM_MAX_TITLE_LEN = 80
_LLM_TIMEOUT_SECONDS = 10
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


def _rewrite_title_with_llm(clip_title: str, streamer_name: str, game_name: str) -> str | None:
    """Rewrite a clip title with OpenAI, returning None on any failure."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    if OpenAI is None:
        log.warning("openai package not installed, skipping title rewrite")
        return None

    system_prompt = (
        "You rewrite Twitch clip titles for YouTube Shorts.\n"
        "Requirements:\n"
        "- Make the title attention-grabbing and click-worthy for YouTube Shorts.\n"
        "- Use emotional hooks, curiosity gaps, or action verbs.\n"
        "- Keep it under 80 characters.\n"
        "- UPPERCASE key words for emphasis.\n"
        "- Include 1-2 relevant emoji.\n"
        "- Do not use clickbait that does not match the clip.\n"
        "- Base it on the original title and context; do not invent content.\n"
        "- Return title text only."
    )
    user_prompt = (
        f"Original title: {clip_title}\n"
        f"Streamer: {streamer_name}\n"
        f"Game: {game_name}"
    )

    client = OpenAI(api_key=api_key)
    for attempt in range(_LLM_MAX_ATTEMPTS):
        try:
            response = client.chat.completions.create(
                model=_LLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
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
            return None

    return None


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

    if not os.environ.get("OPENAI_API_KEY"):
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


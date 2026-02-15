"""AI-powered Twitch clip title optimization for YouTube Shorts."""

# Dependency: anthropic>=0.30.0 or openai>=1.0.0

import hashlib
import logging
import os
import time

try:
    import anthropic  # type: ignore[import-not-found]
except ImportError:
    anthropic = None

try:
    from openai import OpenAI  # type: ignore[import-not-found]
except ImportError:
    OpenAI = None

log = logging.getLogger(__name__)

_ANTHROPIC_MODEL = "claude-opus-4-0-20250514"
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
    """Rewrite a clip title using Claude Opus (preferred) or OpenAI fallback."""
    _SYSTEM_PROMPT = (
        "You rewrite Twitch clip titles for YouTube Shorts to maximize click-through rate.\n"
        "Analytics show these patterns WIN:\n"
        "- Funny quotes or memorable moments from the clip (highest CTR)\n"
        "- Curiosity hooks that make viewers NEED to click\n"
        "- Including the game name naturally (helps YouTube categorize)\n"
        "- Conversational tone, like telling a friend about a funny moment\n"
        "Analytics show these patterns LOSE:\n"
        "- ALL CAPS titles without substance\n"
        "- Generic descriptions (e.g. '300 to 100')\n"
        "- Vague one-word titles\n"
        "Rules:\n"
        "- Keep it under 80 characters\n"
        "- No emoji (they don't help CTR on Shorts)\n"
        "- Do not invent content not in the original title\n"
        "- Include '| GameName' at the end if a game is provided\n"
        "- Return ONLY the title text, nothing else"
    )
    user_prompt = (
        f"Original title: {clip_title}\n"
        f"Streamer: {streamer_name}\n"
        f"Game: {game_name}"
    )

    # Try Anthropic (Claude Opus) first — skip if local LLM is available (free)
    local_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("LOCAL_LLM_URL")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")  # Always try Claude first — titles are too important for a local model
    if anthropic_key and anthropic is not None:
        for attempt in range(_LLM_MAX_ATTEMPTS):
            try:
                client = anthropic.Anthropic(api_key=anthropic_key)
                response = client.messages.create(
                    model=_ANTHROPIC_MODEL,
                    max_tokens=100,
                    system=_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                content = response.content[0].text if response.content else ""
                rewritten = content.strip().splitlines()[0].strip().strip("\"'")
                if rewritten:
                    log.info("Claude Opus title: '%s' -> '%s'", clip_title, rewritten)
                    return _truncate_title(rewritten, _LLM_MAX_TITLE_LEN)
                log.warning("Claude returned empty title for '%s'", clip_title)
                return None
            except Exception:
                log.warning("Claude title rewrite attempt %d failed for '%s'", attempt + 1, clip_title, exc_info=True)
                if attempt < _LLM_MAX_ATTEMPTS - 1:
                    time.sleep(_LLM_RETRY_BACKOFF_SECONDS)
        log.warning("All Claude attempts failed, falling back to OpenAI")

    # Fallback to OpenAI-compatible API
    base_url = os.environ.get("LLM_BASE_URL")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not base_url and not api_key:
        return None
    if OpenAI is None:
        log.warning("openai package not installed, skipping title rewrite")
        return None

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

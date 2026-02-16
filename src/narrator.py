"""Narration generation and TTS audio mixing for short-form gaming clips."""

import asyncio
import logging
import os
import subprocess
import time
import uuid

from src.media_utils import FFMPEG, FFPROBE, is_valid_video, safe_remove

try:
    import edge_tts  # type: ignore[import-not-found]
except ImportError:
    edge_tts = None

try:
    from openai import OpenAI  # type: ignore[import-not-found]
except ImportError:
    OpenAI = None

log = logging.getLogger(__name__)

_LLM_MODEL = "gpt-4o-mini"
_LLM_TIMEOUT_SECONDS = 8
_LLM_MAX_ATTEMPTS = 2
_LLM_RETRY_BACKOFF_SECONDS = 2
_MAX_NARRATION_LEN = 96
_DEFAULT_DUCK_SECONDS = 3.0


def _truncate_text(text: str, max_len: int = _MAX_NARRATION_LEN) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= max_len:
        return clean
    if max_len <= 3:
        return clean[:max_len]
    return clean[: max_len - 3].rstrip() + "..."


def _normalize_narration_text(text: str) -> str:
    if not text:
        return ""

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    candidate = lines[0] if lines else text.strip()

    for prefix in ("narration:", "voiceover:", "line:"):
        if candidate.lower().startswith(prefix):
            candidate = candidate[len(prefix):].strip()
            break

    candidate = candidate.strip().strip('"\'')
    return _truncate_text(candidate)


def _template_fallback_narration(clip_title: str, game_name: str, streamer_name: str) -> str:
    game = (game_name or "").strip()
    streamer = (streamer_name or "").strip()
    title = (clip_title or "").strip()

    if game:
        if title and any(token in title.lower() for token in ("clutch", "ace", "insane", "crazy", "1v", "highlight")):
            return _truncate_text(f"{game} moment you need to see")
        return _truncate_text(f"Check out this {game} clip")

    if streamer:
        return _truncate_text(f"You have to see this play from {streamer}")

    if title:
        return _truncate_text(f"Watch this clip: {title}")

    return "Gaming moment you need to see"


def _call_claude_cli(system_prompt: str, user_prompt: str) -> str | None:
    full_prompt = f"{system_prompt}\n\n{user_prompt}"

    for attempt in range(_LLM_MAX_ATTEMPTS):
        try:
            result = subprocess.run(
                ["claude", "-p", "--model", "sonnet"],
                input=full_prompt,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                narration = _normalize_narration_text(result.stdout)
                if narration:
                    return narration
            log.warning("Claude CLI narration returned empty/error (rc=%d)", result.returncode)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            log.warning("Claude CLI narration attempt %d failed", attempt + 1, exc_info=True)

        if attempt < _LLM_MAX_ATTEMPTS - 1:
            time.sleep(_LLM_RETRY_BACKOFF_SECONDS)

    return None


def _call_openai(system_prompt: str, user_prompt: str) -> str | None:
    base_url = os.environ.get("LLM_BASE_URL")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not base_url and not api_key:
        return None
    if OpenAI is None:
        log.warning("openai package not installed, skipping OpenAI narration fallback")
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
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                timeout=_LLM_TIMEOUT_SECONDS,
            )
            content = ""
            if response.choices:
                content = response.choices[0].message.content or ""
            narration = _normalize_narration_text(content)
            if narration:
                return narration
            log.warning("OpenAI narration response was empty")
            return None
        except Exception as err:
            if attempt < _LLM_MAX_ATTEMPTS - 1:
                log.warning(
                    "OpenAI narration attempt %d/%d failed: %s (retrying in %ds)",
                    attempt + 1,
                    _LLM_MAX_ATTEMPTS,
                    err,
                    _LLM_RETRY_BACKOFF_SECONDS,
                )
                time.sleep(_LLM_RETRY_BACKOFF_SECONDS)
                continue
            log.warning("OpenAI narration failed: %s", err)

    return None


def generate_narration_text(clip_title: str, game_name: str, streamer_name: str) -> str:
    """Generate narration text via LLM with template fallback."""
    system_prompt = (
        "You write short spoken intros for gaming clips used as 2-3 second voiceovers.\n"
        "Write ONE punchy line that sounds natural when spoken out loud.\n"
        "Rules:\n"
        "- 6 to 14 words\n"
        "- under 90 characters\n"
        "- mention the game and/or streamer when possible\n"
        "- no hashtags, no markdown, no labels\n"
        "- return only the line"
    )
    user_prompt = (
        f"Clip title: {clip_title}\n"
        f"Game: {game_name}\n"
        f"Streamer: {streamer_name}"
    )

    narration = _call_claude_cli(system_prompt, user_prompt)
    if narration:
        return _truncate_text(narration)

    narration = _call_openai(system_prompt, user_prompt)
    if narration:
        return _truncate_text(narration)

    return _template_fallback_narration(clip_title, game_name, streamer_name)


async def _synthesize_tts_async(text: str, voice: str, output_path: str):
    if edge_tts is None:
        raise RuntimeError("edge-tts not installed")
    communicator = edge_tts.Communicate(text=text, voice=voice)
    await communicator.save(output_path)


def _synthesize_tts_to_file(text: str, voice: str, output_path: str) -> bool:
    if edge_tts is None:
        log.warning("Narration skipped: edge-tts is not installed")
        return False

    try:
        asyncio.run(_synthesize_tts_async(text, voice, output_path))
    except RuntimeError as err:
        if "asyncio.run() cannot be called from a running event loop" not in str(err):
            log.warning("Narration TTS synthesis failed: %s", err)
            return False
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(_synthesize_tts_async(text, voice, output_path))
            loop.close()
        except Exception as nested_err:
            log.warning("Narration TTS synthesis failed in fallback loop: %s", nested_err)
            return False
    except Exception as err:
        log.warning("Narration TTS synthesis failed: %s", err)
        return False

    return os.path.exists(output_path) and os.path.getsize(output_path) > 0


def _probe_audio_duration(audio_path: str) -> float | None:
    try:
        result = subprocess.run(
            [
                FFPROBE,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                audio_path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        return float((result.stdout or "").strip())
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


def _video_has_audio(video_path: str) -> bool:
    try:
        result = subprocess.run(
            [
                FFPROBE,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "csv=p=0",
                video_path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    return result.returncode == 0 and "audio" in (result.stdout or "").lower()


def _mix_narration_audio(
    video_path: str,
    narration_audio_path: str,
    output_path: str,
    duck_seconds: float,
) -> bool:
    duck_seconds = max(0.5, min(duck_seconds, 4.0))

    if _video_has_audio(video_path):
        filter_complex = (
            f"[0:a]volume='if(lt(t,{duck_seconds:.3f}),0.3,1.0)'[game];"
            "[1:a]adelay=0|0[narr];"
            "[game][narr]amix=inputs=2:duration=first:dropout_transition=0[mix]"
        )
        cmd = [
            FFMPEG,
            "-y",
            "-i",
            video_path,
            "-i",
            narration_audio_path,
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v:0",
            "-map",
            "[mix]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            "-shortest",
            output_path,
        ]
    else:
        # If source has no audio stream, keep video and use narration track as audio.
        cmd = [
            FFMPEG,
            "-y",
            "-i",
            video_path,
            "-i",
            narration_audio_path,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            "-shortest",
            output_path,
        ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (subprocess.TimeoutExpired, OSError, subprocess.SubprocessError) as err:
        log.warning("Narration ffmpeg mix failed: %s", err)
        return False

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        log.warning("Narration ffmpeg mix returned rc=%d: %s", result.returncode, stderr[-400:])
        return False

    return os.path.exists(output_path) and os.path.getsize(output_path) > 0


def add_narration(
    video_path: str,
    output_dir: str,
    clip_title: str,
    game_name: str,
    streamer_name: str,
    voice: str = "en-US-GuyNeural",
) -> str | None:
    """Add TTS narration intro to a video. Returns path to narrated video, or None on failure."""
    if not video_path or not str(video_path).strip():
        return None

    if edge_tts is None:
        log.warning("Narration disabled because edge-tts is unavailable")
        return None

    if not os.path.exists(video_path):
        log.warning("Narration skipped; video does not exist: %s", video_path)
        return None

    if not is_valid_video(video_path):
        log.warning("Narration skipped; invalid video: %s", video_path)
        return None

    try:
        os.makedirs(output_dir, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(video_path))[0]
        token = uuid.uuid4().hex[:8]
        narration_audio_path = os.path.join(output_dir, f"{base_name}_narration_{token}.mp3")
        narrated_video_path = os.path.join(output_dir, f"{base_name}_narrated_{token}.mp4")

        narration_text = generate_narration_text(clip_title, game_name, streamer_name)
        if not narration_text:
            narration_text = _template_fallback_narration(clip_title, game_name, streamer_name)

        if not _synthesize_tts_to_file(narration_text, voice, narration_audio_path):
            return None

        duration = _probe_audio_duration(narration_audio_path) or _DEFAULT_DUCK_SECONDS
        if not _mix_narration_audio(video_path, narration_audio_path, narrated_video_path, duration):
            safe_remove(narrated_video_path, log=log)
            return None

        if not is_valid_video(narrated_video_path):
            safe_remove(narrated_video_path, log=log)
            return None

        return narrated_video_path
    except Exception:
        log.warning("Narration failed for %s", video_path, exc_info=True)
        return None
    finally:
        safe_remove(locals().get("narration_audio_path"), log=log)

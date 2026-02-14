"""Burned-in caption generation via Deepgram or Whisper STT."""

import logging
import os
import time

from src.media_utils import extract_audio, safe_remove
from src.models import CaptionWord

try:
    from deepgram import DeepgramClient, FileSource, PrerecordedOptions  # type: ignore[import-not-found]
except ImportError:
    DeepgramClient = None
    FileSource = None
    PrerecordedOptions = None

try:
    # Optional dependency: openai-whisper (`pip install openai-whisper`)
    import whisper  # type: ignore[import-not-found]
except ImportError:
    whisper = None

log = logging.getLogger(__name__)

# Max audio file size (50 MB) to prevent OOM when reading into memory
_MAX_AUDIO_BYTES = 50_000_000

# Retry config for Deepgram API
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 2  # seconds

_ASS_HEADER = (
    "[Script Info]\n"
    "ScriptType: v4.00+\n"
    "PlayResX: 1080\n"
    "PlayResY: 1920\n"
    "WrapStyle: 0\n"
    "\n"
    "[V4+ Styles]\n"
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
    "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
    "Alignment, MarginL, MarginR, MarginV, Encoding\n"
    "Style: Default,Arial,72,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
    "1,0,0,0,100,100,0,0,1,4,0,2,20,20,400,1\n"
    "\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
)

_VALID_CAPTION_BACKENDS = {"auto", "deepgram", "whisper"}


def transcribe_clip(video_path: str, tmp_dir: str, client=None):
    """Transcribe audio from a video clip using Deepgram Nova-2.

    Args:
        video_path: Path to the video file.
        tmp_dir: Temporary directory for intermediate files.
        client: Optional DeepgramClient instance (singleton reuse).

    Returns a list of CaptionWord or None on failure.
    """
    if DeepgramClient is None:
        log.warning("deepgram-sdk not installed — skipping transcription")
        return None

    api_key = os.environ.get("DEEPGRAM_API_KEY")
    if not api_key:
        log.warning("DEEPGRAM_API_KEY not set — skipping transcription")
        return None

    clip_id = os.path.splitext(os.path.basename(video_path))[0]
    audio_path = os.path.join(tmp_dir, f"{clip_id}_audio.flac")

    try:
        extract_audio(video_path, audio_path)
    except Exception as e:
        log.warning("Audio extraction failed for %s: %s", clip_id, e)
        safe_remove(audio_path, log=log)
        return None

    try:
        # Check file size before reading into memory
        audio_size = os.path.getsize(audio_path)
        if audio_size > _MAX_AUDIO_BYTES:
            log.warning("Audio file too large (%d bytes), skipping captions", audio_size)
            return None

        if client is None:
            client = DeepgramClient(api_key)

        with open(audio_path, "rb") as f:
            buffer_data = f.read()

        payload: FileSource = {"buffer": buffer_data}
        options = PrerecordedOptions(
            model="nova-2",
            smart_format=True,
            utterances=False,
            punctuate=True,
            language="en",
        )

        # Retry with exponential backoff for transient failures
        response = None
        for attempt in range(_MAX_RETRIES):
            try:
                response = client.listen.rest.v("1").transcribe_file(
                    payload, options, timeout=30
                )
                break
            except Exception as e:
                if attempt < _MAX_RETRIES - 1:
                    wait = _RETRY_BACKOFF_BASE ** attempt
                    log.warning(
                        "Deepgram attempt %d/%d failed for %s: %s (retrying in %ds)",
                        attempt + 1, _MAX_RETRIES, clip_id, e, wait,
                    )
                    time.sleep(wait)
                else:
                    raise

        # Validate response structure before accessing nested fields
        if not response or not response.results or not response.results.channels:
            log.warning("Deepgram returned empty results for %s", clip_id)
            return None
        alternatives = response.results.channels[0].alternatives
        if not alternatives or not alternatives[0].words:
            log.warning("Deepgram returned no words for %s", clip_id)
            return None

        words_data = alternatives[0].words

        words = []
        for w in words_data:
            words.append(CaptionWord(
                word=w.punctuated_word or w.word,
                start=w.start,
                end=w.end,
                confidence=w.confidence,
            ))

        if not words:
            log.warning("Deepgram returned empty transcript for %s", clip_id)
            return None

        log.info("Transcribed %d words for %s", len(words), clip_id)
        return words

    except Exception as e:
        log.warning("Deepgram transcription failed for %s: %s", clip_id, e)
        return None
    finally:
        safe_remove(audio_path, log=log)


def _resolve_caption_backend() -> str:
    """Resolve caption backend from environment with safe fallback."""
    backend = os.environ.get("CAPTION_BACKEND", "auto").strip().lower()
    if backend not in _VALID_CAPTION_BACKENDS:
        log.warning("Invalid CAPTION_BACKEND=%r, defaulting to auto", backend)
        return "auto"
    return backend


def _transcribe_whisper(audio_path: str) -> list[dict]:
    """Transcribe an audio file with local Whisper and return segment timestamps."""
    if whisper is None:
        log.warning("openai-whisper not installed — skipping transcription")
        return []

    try:
        model = whisper.load_model("base")
        result = model.transcribe(audio_path, language="en")
        raw_segments = result.get("segments") or []

        segments = []
        for segment in raw_segments:
            text = str(segment.get("text", "")).strip()
            if not text:
                continue

            start = float(segment.get("start", 0.0))
            end = float(segment.get("end", start))
            if end <= start:
                continue

            segments.append({"start": start, "end": end, "text": text})

        return segments
    except Exception as e:
        log.warning("Whisper transcription failed for %s: %s", audio_path, e)
        return []


def _write_ass_file(lines: list[str], output_path: str) -> str:
    """Write ASS subtitle header and dialogue lines to disk."""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(_ASS_HEADER)
        for line in lines:
            f.write(line)
    return output_path


def _segments_to_ass(segments: list[dict], output_path: str) -> str:
    """Convert Whisper segments into an ASS subtitle file."""
    lines = []
    for segment in segments:
        text = str(segment.get("text", "")).strip()
        if not text:
            continue

        start = float(segment.get("start", 0.0))
        end = float(segment.get("end", start))
        if end <= start:
            continue

        start_ass = _format_ass_time(start)
        end_ass = _format_ass_time(end)
        text = text.upper()
        text = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        lines.append(f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{text}\n")

    return _write_ass_file(lines, output_path)


def _offset_segments(segments: list[dict], silence_offset: float) -> list[dict]:
    """Apply leading-silence offset to segment timestamps."""
    if silence_offset <= 0:
        return segments

    adjusted = []
    for segment in segments:
        start = float(segment.get("start", 0.0))
        end = float(segment.get("end", start))
        text = str(segment.get("text", ""))
        new_start = max(0, start - silence_offset)
        new_end = max(0, end - silence_offset)
        if new_end <= 0 or not text.strip():
            continue
        adjusted.append({"start": new_start, "end": new_end, "text": text})

    return adjusted


def _format_ass_time(seconds: float) -> str:
    """Convert seconds to ASS time format: H:MM:SS.cc (centiseconds).

    Uses total centiseconds approach to avoid floating-point rounding issues.
    """
    if seconds < 0:
        seconds = 0.0
    total_cs = round(seconds * 100)
    cs = total_cs % 100
    total_s = total_cs // 100
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _group_words(words) -> list:
    """Group words into caption lines.

    Rules:
    - Max 3 words per line
    - Max 2 seconds duration per line
    - Break on gaps > 0.3 seconds between words
    - Break after sentence-ending punctuation (. ! ? ,)
    """
    if not words:
        return []

    groups = []
    current_group = [words[0]]

    for i in range(1, len(words)):
        prev = words[i - 1]
        curr = words[i]

        gap = curr.start - prev.end
        group_duration = curr.end - current_group[0].start

        # Check if previous word ends with punctuation
        prev_text = prev.word.rstrip()
        punctuation_break = prev_text and prev_text[-1] in ".!?,"

        if (len(current_group) >= 3 or gap > 0.3
                or group_duration > 2.0 or punctuation_break):
            groups.append(current_group)
            current_group = [curr]
        else:
            current_group.append(curr)

    if current_group:
        groups.append(current_group)

    return groups


def generate_ass_subtitles(words, output_path: str, silence_offset: float = 0.0) -> str:
    """Generate an ASS subtitle file from word-level timestamps.

    Args:
        words: List of CaptionWord with timing info.
        output_path: Path to write the .ass file.
        silence_offset: Seconds to subtract from timestamps (for leading silence trim).

    Returns:
        Path to the generated ASS file.
    """
    # Adjust timestamps for silence offset (caption-silence desync fix)
    if silence_offset > 0:
        adjusted = []
        for w in words:
            new_start = max(0, w.start - silence_offset)
            new_end = max(0, w.end - silence_offset)
            if new_end > 0:  # skip words entirely before trim point
                adjusted.append(CaptionWord(word=w.word, start=new_start, end=new_end,
                                            confidence=getattr(w, 'confidence', 0.0)))
        words = adjusted

    groups = _group_words(words)

    lines = []
    for group in groups:
        start = _format_ass_time(group[0].start)
        end = _format_ass_time(group[-1].end)
        text = " ".join(w.word for w in group)
        # Uppercase for readability (spec requirement)
        text = text.upper()
        # Escape ASS special characters
        text = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n")

    return _write_ass_file(lines, output_path)


def generate_captions(video_path: str, tmp_dir: str, silence_offset: float = 0.0) -> str | None:
    """Generate burned-in caption file for a video clip.

    Orchestrates: transcribe audio -> generate ASS subtitles.
    Returns path to ASS file, or None on any failure (graceful degradation).
    """
    clip_id = os.path.splitext(os.path.basename(video_path))[0]
    subtitle_path = os.path.join(tmp_dir, f"{clip_id}_captions.ass")

    backend = _resolve_caption_backend()

    if backend in {"auto", "deepgram"}:
        log.info("Using %s for captions", "deepgram")
        words = transcribe_clip(video_path, tmp_dir)
        if words:
            try:
                return generate_ass_subtitles(words, subtitle_path, silence_offset=silence_offset)
            except Exception as e:
                log.warning("ASS subtitle generation failed for %s: %s", clip_id, e)
                safe_remove(subtitle_path, log=log)
                return None

        if backend == "deepgram":
            return None

    log.info("Using %s for captions", "whisper")
    audio_path = os.path.join(tmp_dir, f"{clip_id}_audio_whisper.flac")
    try:
        extract_audio(video_path, audio_path)
    except Exception as e:
        log.warning("Audio extraction failed for %s: %s", clip_id, e)
        safe_remove(audio_path, log=log)
        return None

    try:
        segments = _transcribe_whisper(audio_path)
        segments = _offset_segments(segments, silence_offset)
        if not segments:
            return None
        return _segments_to_ass(segments, subtitle_path)
    except Exception as e:
        log.warning("ASS subtitle generation failed for %s: %s", clip_id, e)
        safe_remove(subtitle_path, log=log)
        return None
    finally:
        safe_remove(audio_path, log=log)

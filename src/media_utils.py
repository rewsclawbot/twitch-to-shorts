import logging
import os
import shutil
import subprocess

log = logging.getLogger(__name__)

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


def safe_remove(path, log=None):
    """Best-effort file removal. Returns True if removed."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
            return True
    except OSError as e:
        if log:
            log.debug("Failed to remove %s: %s", path, e)
    return False


def is_valid_video(path: str) -> bool:
    """Validate a video file using ffprobe."""
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0 and "video" in result.stdout
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as e:
        log.warning("Video validation failed for %s: %s", path, e)
        return False


def extract_audio(video_path: str, output_path: str, sample_rate: int = 16000) -> str:
    """Extract audio from video as mono FLAC for speech-to-text.

    Uses FLAC (lossless compression) instead of WAV to reduce file size ~3x.
    Caps extraction at 65 seconds to prevent oversized audio files.
    """
    try:
        subprocess.run(
            [FFMPEG, "-y", "-i", video_path,
             "-vn", "-acodec", "flac", "-ar", str(sample_rate), "-ac", "1",
             "-t", "65",
             output_path],
            capture_output=True, check=True, timeout=60,
        )
    except subprocess.CalledProcessError as e:
        safe_remove(output_path, log=log)
        stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
        raise RuntimeError(
            f"Audio extraction failed for {video_path}: {stderr}"
        ) from e

    if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        safe_remove(output_path, log=log)
        raise RuntimeError(f"Audio extraction produced no output for {video_path}")

    return output_path

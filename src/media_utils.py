import logging
import shutil
import subprocess

log = logging.getLogger(__name__)

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


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

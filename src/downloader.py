import logging
import os
import shutil
import subprocess

from src.models import Clip
from src.video_processor import FFPROBE

log = logging.getLogger(__name__)

YT_DLP = shutil.which("yt-dlp") or "yt-dlp"


def download_clip(clip: Clip, tmp_dir: str) -> str | None:
    """Download a Twitch clip using yt-dlp. Returns path on success, None on failure."""
    os.makedirs(tmp_dir, exist_ok=True)
    output_path = os.path.join(tmp_dir, f"{clip.id}.mp4")
    tmp_path = output_path + ".part"

    if os.path.exists(output_path) and _is_valid_video(output_path):
        log.info("Clip already downloaded: %s", clip.id)
        return output_path
    if os.path.exists(output_path):
        os.remove(output_path)
    if os.path.exists(tmp_path):
        os.remove(tmp_path)

    clip_url = clip.url
    log.info("Downloading clip %s via yt-dlp from %s", clip.id, clip_url)

    try:
        subprocess.run(
            [YT_DLP, "-o", tmp_path, "--no-part", "--no-warnings", "-q", clip_url],
            check=True, capture_output=True, timeout=120,
        )
    except subprocess.CalledProcessError as e:
        log.error("yt-dlp failed for %s: %s", clip.id, e.stderr.decode(errors="replace"))
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return None
    except subprocess.TimeoutExpired:
        log.error("yt-dlp timed out for %s", clip.id)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return None
    except FileNotFoundError:
        log.error("yt-dlp not found. Install with: pip install yt-dlp")
        return None

    # yt-dlp may remux (e.g. TS→MP4) and write to output_path instead of tmp_path
    if os.path.exists(tmp_path):
        actual_path = tmp_path
    elif os.path.exists(output_path):
        actual_path = output_path
    else:
        log.error("Download produced no file: %s", clip.id)
        return None

    if not _is_valid_video(actual_path):
        log.error("Download produced invalid file: %s", clip.id)
        os.remove(actual_path)
        return None

    if actual_path != output_path:
        try:
            os.replace(actual_path, output_path)
        except OSError as e:
            log.error("Failed to finalize download for %s: %s", clip.id, e)
            os.remove(actual_path)
            return None

    log.info("Downloaded clip %s (%d bytes)", clip.id, os.path.getsize(output_path))
    return output_path


def _is_valid_video(path: str) -> bool:
    """Validate a video file using ffprobe."""
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0 and "video" in result.stdout
    except Exception as e:
        log.warning("Video validation failed for %s: %s", path, e)
        return False

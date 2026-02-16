"""Smart hook editor: recuts clips with weak openings for better retention.

When the hook detector scores a clip below the recut threshold, this module
attempts to start the clip at a more engaging moment by finding the peak
action timestamp and trimming to start just before it.
"""

import logging
import os
import subprocess

from src.media_utils import FFMPEG, is_valid_video
from src.video_processor import find_peak_action_timestamp

log = logging.getLogger(__name__)

# If a clip's hook_score is below this, attempt a recut
DEFAULT_RECUT_THRESHOLD = 0.4

# Start this many seconds before the peak action
LEAD_IN_SECONDS = 2.0

# Minimum output duration after recut (don't create tiny clips)
MIN_OUTPUT_DURATION = 8.0


def _get_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return float(result.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return 0.0


def recut_for_hook(
    video_path: str,
    output_dir: str,
    hook_score: float,
    recut_threshold: float = DEFAULT_RECUT_THRESHOLD,
) -> str | None:
    """Recut a video to start at a more engaging moment if the hook is weak.

    If hook_score >= recut_threshold, returns None (no recut needed).
    If recut succeeds, returns the path to the new file.
    If recut fails or would produce too short a clip, returns None.

    Args:
        video_path: Path to the input video file
        output_dir: Directory for temporary output files
        hook_score: The hook strength score (0.0-1.0) from hook_detector
        recut_threshold: Only recut if hook_score is below this value

    Returns:
        Path to recut video, or None if no recut was needed/possible
    """
    if hook_score >= recut_threshold:
        return None

    duration = _get_duration(video_path)
    if duration <= MIN_OUTPUT_DURATION:
        log.debug("Clip too short for recut (%.1fs <= %.1fs min)", duration, MIN_OUTPUT_DURATION)
        return None

    # Find the peak action moment in the clip
    peak_ts = find_peak_action_timestamp(video_path)
    if peak_ts <= 0:
        log.debug("No peak action found for recut")
        return None

    # Calculate new start time: LEAD_IN_SECONDS before peak, but not negative
    new_start = max(0.0, peak_ts - LEAD_IN_SECONDS)

    # If the new start is within the first 1 second, the hook is already at the start
    if new_start < 1.0:
        log.debug("Peak action already near start (%.1fs), no recut needed", peak_ts)
        return None

    # Check that remaining duration after recut is long enough
    remaining = duration - new_start
    if remaining < MIN_OUTPUT_DURATION:
        log.debug(
            "Recut would leave only %.1fs (min %.1fs), skipping",
            remaining, MIN_OUTPUT_DURATION,
        )
        return None

    # Perform the recut using ffmpeg
    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    output_path = os.path.join(output_dir, f"{base_name}_recut.mp4")

    cmd = [
        FFMPEG, "-y",
        "-ss", f"{new_start:.2f}",
        "-i", video_path,
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        output_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            log.warning("Hook recut ffmpeg failed (rc=%d): %s", result.returncode, result.stderr[:200])
            return None

        if not is_valid_video(output_path):
            log.warning("Hook recut produced invalid video")
            try:
                os.remove(output_path)
            except OSError:
                pass
            return None

        log.info(
            "Hook recut: score=%.3f, peak=%.1fs, new_start=%.1fs, "
            "duration %.1fs -> %.1fs",
            hook_score, peak_ts, new_start, duration, remaining,
        )
        return output_path

    except subprocess.TimeoutExpired:
        log.warning("Hook recut timed out for %s", video_path)
        return None
    except Exception as err:
        log.warning("Hook recut failed for %s: %s", video_path, err)
        return None

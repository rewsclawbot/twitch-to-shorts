import contextlib
import json
import logging
import math
import os
import re
import subprocess
import sys

from src.media_utils import FFMPEG, FFPROBE, is_valid_video, safe_remove
from src.models import FacecamConfig

log = logging.getLogger(__name__)

_CONTEXT_KEYWORD_PATTERNS = [
    # Multi-kill / clutch patterns (highest impact)
    (r"\b1\s*V\s*5\b", "1V5"),
    (r"\b1\s*V\s*4\b", "1V4"),
    (r"\b1\s*V\s*3\b", "1V3"),
    (r"\b1\s*V\s*2\b", "1V2"),
    (r"\bCLUTCH\b", "CLUTCH"),
    (r"\bACE\b", "ACE"),
    (r"\bPENTAKILL\b", "PENTAKILL"),
    (r"\bTEAMWIPE\b", "TEAMWIPE"),
    # Skill shots
    (r"\bHEADSHOT\b", "HEADSHOT"),
    (r"\bNOSCOPE\b", "NOSCOPE"),
    (r"\bFLICK\b", "FLICK"),
    (r"\bWALLBANG\b", "WALLBANG"),
    (r"\bCOLLAT(ERAL)?\b", "COLLATERAL"),
    # Emotional / hype
    (r"\bINSANE\b", "INSANE"),
    (r"\bCRAZY\b", "CRAZY"),
    (r"\bUNREAL\b", "UNREAL"),
    (r"\bSAVAGE\b", "SAVAGE"),
    (r"\bEPIC\b", "EPIC"),
    (r"\bWILD\b", "WILD"),
    (r"\bINCREDIBLE\b", "INCREDIBLE"),
    (r"\bDESTROYED?\b", "DESTROYED"),
    (r"\bWIPED?\b", "WIPED"),
    (r"\bCHOKE[DS]?\b", "CHOKE"),
    (r"\bRIPPED?\b", "RIP"),
    # Funny / fail
    (r"\bFAIL\b", "FAIL"),
    (r"\bRIP\b", "RIP"),
    (r"\bBROKEN\b", "BROKEN"),
    (r"\bGLITCH\b", "GLITCH"),
    (r"\bBUG\b", "BUG"),
    # Win/loss
    (r"\bWIN\b", "WIN"),
    (r"\bDUB\b", "DUB"),
    (r"\bCHAMPION\b", "CHAMPION"),
    (r"\bVICTORY\b", "VICTORY"),
    # Suspense / anticipation hooks
    (r"\bWAIT\b", "WAIT FOR IT..."),
    (r"\bWATCH\s*(THIS|TILL)\b", "WATCH THIS"),
]

_CONTEXT_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


def _escape_drawtext_text(text: str) -> str:
    escaped = " ".join(str(text).split())
    escaped = escaped.replace("\\", r"\\\\")
    escaped = escaped.replace(":", r"\:")
    escaped = escaped.replace("'", r"\'")
    escaped = escaped.replace("%", r"\%")
    escaped = escaped.replace(",", r"\,")
    escaped = escaped.replace("[", r"\[").replace("]", r"\]")
    return escaped


def _escape_drawtext_path(path: str) -> str:
    escaped = path.replace("\\", "/")
    escaped = escaped.replace(":", r"\:")
    escaped = escaped.replace("'", r"\'")
    escaped = escaped.replace("[", r"\[").replace("]", r"\]")
    return escaped


def _find_context_fontfile() -> str | None:
    for candidate in _CONTEXT_FONT_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


def _extract_context_keywords(title: str, max_keywords: int = 2) -> list[str]:
    title_upper = (title or "").upper()
    matches: list[str] = []
    for pattern, label in _CONTEXT_KEYWORD_PATTERNS:
        if re.search(pattern, title_upper):
            matches.append(label)
            if len(matches) >= max_keywords:
                break
    return matches


def _probe_video_info(path: str) -> tuple[float | None, tuple[int, int] | None]:
    """Probe duration and dimensions in a single ffprobe call.

    Returns (duration, (width, height)). Either may be None on failure.
    """
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", "-select_streams", "v:0", path],
            capture_output=True, text=True, timeout=15,
        )
        info = json.loads(result.stdout)
    except Exception as e:
        log.warning("Failed to probe video info for %s: %s", path, e)
        return None, None

    duration = None
    dims = None

    # Duration: prefer format.duration, fall back to stream.duration
    try:
        duration = float(info.get("format", {}).get("duration", 0))
        if not duration:
            duration = float(info["streams"][0].get("duration", 0)) or None
    except (ValueError, TypeError, KeyError, IndexError):
        pass

    try:
        stream = info["streams"][0]
        dims = (int(stream["width"]), int(stream["height"]))
    except (KeyError, IndexError, ValueError, TypeError):
        pass

    return duration, dims


def _get_duration(path: str) -> float | None:
    """Get video duration. Used by extract_thumbnail which doesn't need dimensions."""
    duration, _ = _probe_video_info(path)
    return duration


def _batch_sample_ydif(video_path: str, timestamps: list[float]) -> list[float]:
    """Sample YDIF at multiple timestamps using batched ffmpeg filter_complex calls.

    Returns a list of max-YDIF values, one per timestamp (0.0 on failure).
    Batches into groups of 8 to avoid opening too many decode pipelines.
    """
    if not timestamps:
        return []

    BATCH_SIZE = 8
    all_scores: list[float] = []

    for batch_start in range(0, len(timestamps), BATCH_SIZE):
        batch_ts = timestamps[batch_start:batch_start + BATCH_SIZE]
        n = len(batch_ts)
        cmd = [FFMPEG]
        for ts in batch_ts:
            cmd += ["-ss", f"{ts:.2f}", "-i", video_path]

        # Each input: extract 1 frame through signalstats, then concat all
        filters = []
        for i in range(n):
            filters.append(f"[{i}:v]signalstats,metadata=print,trim=end_frame=1[v{i}]")
        concat_inputs = "".join(f"[v{i}]" for i in range(n))
        filters.append(f"{concat_inputs}concat=n={n}:v=1:a=0[out]")
        cmd += ["-filter_complex", ";".join(filters), "-map", "[out]", "-f", "null", "-"]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            all_ydif: list[float] = []
            for line in result.stderr.splitlines():
                if "signalstats.YDIF=" in line:
                    with contextlib.suppress(ValueError, IndexError):
                        all_ydif.append(float(line.split("YDIF=")[1]))
            for i in range(n):
                all_scores.append(all_ydif[i] if i < len(all_ydif) else 0.0)
        except Exception as e:
            log.warning("Batch YDIF sampling failed: %s", e)
            all_scores.extend([0.0] * n)

    return all_scores


def _extract_signalstats_metric_values(stderr: str, metric: str) -> list[float]:
    pattern = re.compile(
        rf"signalstats\.{re.escape(metric)}=([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)"
    )
    values: list[float] = []
    for match in pattern.finditer(stderr):
        with contextlib.suppress(ValueError):
            values.append(float(match.group(1)))
    return values


def _batch_sample_sobel_edge_density(video_path: str, timestamps: list[float]) -> list[float]:
    """Sample normalized Sobel edge density [0,1] at given timestamps.

    Batches into groups of 8 to avoid opening too many decode pipelines.
    """
    if not timestamps:
        return []

    BATCH_SIZE = 8
    all_scores: list[float] = []

    for batch_start in range(0, len(timestamps), BATCH_SIZE):
        batch_ts = timestamps[batch_start:batch_start + BATCH_SIZE]
        n = len(batch_ts)
        cmd = [FFMPEG]
        for ts in batch_ts:
            cmd += ["-ss", f"{ts:.2f}", "-i", video_path]

        filters = []
        for i in range(n):
            filters.append(f"[{i}:v]format=gray,sobel,signalstats,metadata=print,trim=end_frame=1[v{i}]")
        concat_inputs = "".join(f"[v{i}]" for i in range(n))
        filters.append(f"{concat_inputs}concat=n={n}:v=1:a=0[out]")
        cmd += ["-filter_complex", ";".join(filters), "-map", "[out]", "-f", "null", "-"]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
            yavg_values = _extract_signalstats_metric_values(result.stderr, "YAVG")
            for i in range(n):
                raw = yavg_values[i] if i < len(yavg_values) else 0.0
                all_scores.append(max(0.0, min(raw / 255.0, 1.0)))
        except Exception as e:
            log.warning("Batch Sobel edge sampling failed: %s", e)
            all_scores.extend([0.0] * n)

    return all_scores


def _batch_sample_color_variance(video_path: str, timestamps: list[float]) -> list[float]:
    """Sample normalized chroma variance proxy [0,1] at given timestamps.

    Batches into groups of 8 to avoid opening too many decode pipelines.
    """
    if not timestamps:
        return []

    BATCH_SIZE = 8
    all_scores: list[float] = []

    for batch_start in range(0, len(timestamps), BATCH_SIZE):
        batch_ts = timestamps[batch_start:batch_start + BATCH_SIZE]
        n = len(batch_ts)
        cmd = [FFMPEG]
        for ts in batch_ts:
            cmd += ["-ss", f"{ts:.2f}", "-i", video_path]

        filters = []
        for i in range(n):
            filters.append(f"[{i}:v]signalstats,metadata=print,trim=end_frame=1[v{i}]")
        concat_inputs = "".join(f"[v{i}]" for i in range(n))
        filters.append(f"{concat_inputs}concat=n={n}:v=1:a=0[out]")
        cmd += ["-filter_complex", ";".join(filters), "-map", "[out]", "-f", "null", "-"]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
            umin_values = _extract_signalstats_metric_values(result.stderr, "UMIN")
            umax_values = _extract_signalstats_metric_values(result.stderr, "UMAX")
            vmin_values = _extract_signalstats_metric_values(result.stderr, "VMIN")
            vmax_values = _extract_signalstats_metric_values(result.stderr, "VMAX")

            for i in range(n):
                if i >= len(umin_values) or i >= len(umax_values) or i >= len(vmin_values) or i >= len(vmax_values):
                    all_scores.append(0.0)
                    continue
                u_range = max(0.0, umax_values[i] - umin_values[i])
                v_range = max(0.0, vmax_values[i] - vmin_values[i])
                variance_norm = ((u_range * u_range) + (v_range * v_range)) / (2.0 * 255.0 * 255.0)
                all_scores.append(max(0.0, min(variance_norm, 1.0)))
        except Exception as e:
            log.warning("Batch color variance sampling failed: %s", e)
            all_scores.extend([0.0] * n)

    return all_scores


def score_visual_quality(video_path: str, samples: int = 10) -> float:
    """Score visual richness in [0,1] using Sobel edge density and color variance."""
    if not os.path.exists(video_path):
        return 0.0
    if samples <= 0:
        return 0.0

    duration = _get_duration(video_path)
    if not duration or duration <= 0:
        return 0.0

    step = duration / (samples + 1)
    timestamps = [max(0.1, min(duration - 0.1, step * (i + 1))) for i in range(samples)]

    edge_scores = _batch_sample_sobel_edge_density(video_path, timestamps)
    color_scores = _batch_sample_color_variance(video_path, timestamps)

    if not edge_scores and not color_scores:
        return 0.0

    n = len(timestamps)
    if len(edge_scores) < n:
        edge_scores = edge_scores + [0.0] * (n - len(edge_scores))
    if len(color_scores) < n:
        color_scores = color_scores + [0.0] * (n - len(color_scores))

    edge_avg = sum(edge_scores[:n]) / n
    color_avg = sum(color_scores[:n]) / n
    score = 0.6 * edge_avg + 0.4 * color_avg
    return max(0.0, min(score, 1.0))


def extract_thumbnail(
    input_path: str,
    tmp_dir: str,
    samples: int = 8,
    width: int = 1280,
    duration: float | None = None,
) -> str | None:
    """Extract a thumbnail from the most active frame in the clip."""
    os.makedirs(tmp_dir, exist_ok=True)
    clip_id = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(tmp_dir, f"{clip_id}_thumb.jpg")

    if duration is None:
        duration = _get_duration(input_path)
    if not duration or duration <= 0:
        return None
    if samples <= 0:
        return None

    step = duration / (samples + 1)
    timestamps = [max(0.1, min(duration - 0.1, step * (i + 1))) for i in range(samples)]

    scores = _batch_sample_ydif(input_path, timestamps)
    best_ts = timestamps[0]
    best_score = -1.0
    for ts, score in zip(timestamps, scores, strict=False):
        if score > best_score:
            best_score = score
            best_ts = ts

    cmd = [
        FFMPEG, "-ss", f"{best_ts:.2f}", "-i", input_path,
        "-frames:v", "1",
        "-vf", f"scale={width}:-2",
        "-q:v", "2",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
    except Exception as e:
        log.warning("Thumbnail extraction failed for %s: %s", clip_id, e)
        safe_remove(output_path)
        return None

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        safe_remove(output_path)
        return None
    return output_path


def burn_context_overlay(video_path: str, output_path: str, game_name: str, title: str) -> bool:
    """Burn lightweight context text overlays into a clip using ffmpeg drawtext."""
    if not os.path.exists(video_path):
        return False

    game_label = (game_name or "GAMEPLAY").strip().upper()
    if not game_label:
        game_label = "GAMEPLAY"
    game_label = _escape_drawtext_text(game_label[:48])

    emphasis_words = _extract_context_keywords(title)
    emphasis_label = _escape_drawtext_text(" | ".join(emphasis_words)) if emphasis_words else ""

    fontfile = _find_context_fontfile()
    font_prefix = f"fontfile='{_escape_drawtext_path(fontfile)}':" if fontfile else ""

    filter_parts = [
        "drawbox=x=0:y=0:w=iw:h=92:color=black@0.45:t=fill",
        (
            f"drawtext={font_prefix}text='{game_label}':x=(w-text_w)/2:y=26"
            ":fontsize=40:fontcolor=white:borderw=3:bordercolor=black@0.95"
        ),
    ]
    if emphasis_label:
        filter_parts.append(
            (
                f"drawtext={font_prefix}text='{emphasis_label}':x=(w-text_w)/2:y=(h-text_h)/2"
                ":fontsize=132:fontcolor=white:borderw=6:bordercolor=black@0.95"
                ":enable='lt(t,2)'"
            )
        )
    vf = ",".join(filter_parts)

    tmp_output = output_path + ".ctx.tmp.mp4"
    cmd = [
        FFMPEG,
        "-y",
        "-i", video_path,
        "-vf", vf,
        "-map", "0:v:0",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-crf", "20",
        "-preset", "fast",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        tmp_output,
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
    except subprocess.CalledProcessError as e:
        err = (e.stderr or "")[-500:]
        log.warning("Context overlay ffmpeg failed for %s: %s", video_path, err)
        safe_remove(tmp_output)
        return False
    except Exception as e:
        log.warning("Context overlay failed for %s: %s", video_path, e)
        safe_remove(tmp_output)
        return False

    if not os.path.exists(tmp_output) or os.path.getsize(tmp_output) == 0:
        safe_remove(tmp_output)
        return False

    os.replace(tmp_output, output_path)
    return True


def detect_leading_silence(input_path: str, threshold_db: float = -30, min_duration: float = 0.5) -> float:
    """Return duration of leading silence in seconds (0.0 if none). Capped at 5s."""
    cmd = [
        FFMPEG, "-t", "6", "-i", input_path,
        "-af", f"silencedetect=noise={threshold_db}dB:d={min_duration}",
        "-vn", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        stderr = result.stderr
        # Look for first silence block starting at 0
        start_match = re.search(r"silence_start:\s*(-?[\d.]+)", stderr)
        if not start_match or float(start_match.group(1)) > 0.01:
            return 0.0
        end_match = re.search(r"silence_end:\s*([\d.]+)", stderr)
        if end_match:
            return min(float(end_match.group(1)), 5.0)
    except Exception as e:
        log.warning("Leading silence detection failed: %s", e)
    return 0.0


def detect_visual_dead_frames(input_path: str, start_offset: float = 0.0, ydif_threshold: float = 0.5, max_trim: float = 3.0) -> float:
    """Detect and return duration of visual dead frames (static/loading screens) at the start.

    Samples YDIF at 0.5s intervals from start_offset. Returns the number of seconds
    to trim when YDIF < threshold (indicating static content). Capped at max_trim.

    Args:
        input_path: Path to video file
        start_offset: Starting point in seconds (e.g., after audio silence trim)
        ydif_threshold: YDIF threshold below which frames are considered static (default 0.5)
        max_trim: Maximum seconds to trim to avoid cutting real content (default 3.0s)

    Returns:
        Duration in seconds to trim (0.0 if no dead frames detected)
    """
    sample_interval = 0.5
    max_samples = int(max_trim / sample_interval)

    # Sample at 0.5s intervals from start_offset
    timestamps = [start_offset + (i * sample_interval) for i in range(max_samples)]

    try:
        scores = _batch_sample_ydif(input_path, timestamps)

        # Find first frame with motion (YDIF >= threshold)
        for i, score in enumerate(scores):
            if score >= ydif_threshold:
                trim_duration = i * sample_interval
                if trim_duration > 0:
                    log.info("Detected %.2fs of visual dead frames (YDIF < %.1f)", trim_duration, ydif_threshold)
                return trim_duration

        # All samples are static - return max_trim
        log.info("All sampled frames are static (YDIF < %.1f), trimming %.2fs", ydif_threshold, max_trim)
        return max_trim

    except Exception as e:
        log.warning("Visual dead frame detection failed: %s", e)
        return 0.0


def find_peak_action_timestamp(
    video_path: str,
    start_offset: float = 0.0,
    sample_interval: float = 0.5,
    duration: float | None = None,
    check_exists: bool = True,
) -> float:
    """Find timestamp of peak visual activity using YDIF sampled across the clip.

    Activity is scored in 1-second windows (2 samples at 0.5s interval).
    Returns start_offset on failure.
    """
    if check_exists and not os.path.exists(video_path):
        return max(0.0, start_offset)

    if duration is None:
        duration = _get_duration(video_path)
    if not duration or duration <= 0:
        return max(0.0, start_offset)

    start = max(0.0, start_offset)
    if start >= duration:
        return start

    # Ensure at least one timestamp and avoid sampling exactly at clip EOF.
    timestamps: list[float] = []
    ts = min(start + 0.1, max(duration - 0.1, start))
    while ts < duration:
        timestamps.append(ts)
        ts += max(sample_interval, 0.1)
    if not timestamps:
        return start

    scores = _batch_sample_ydif(video_path, timestamps)
    if not scores:
        return start

    window_size = max(int(round(1.0 / max(sample_interval, 0.1))), 1)
    if len(scores) < window_size:
        best_idx = max(range(len(scores)), key=lambda i: scores[i])
        return timestamps[best_idx]

    best_idx = 0
    best_score = -1.0
    for i in range(0, len(scores) - window_size + 1):
        window = scores[i:i + window_size]
        window_score = sum(window) / window_size
        if window_score > best_score:
            best_score = window_score
            best_idx = i
    # Return center timestamp of the best 1s window.
    center_offset = (window_size - 1) * sample_interval / 2
    return timestamps[best_idx] + center_offset


def trim_to_optimal_length(video_path: str, output_path: str, target_duration: int = 15) -> str | None:
    """Trim a clip to the densest activity window based on YDIF heatmap sampling.

    Returns output_path on success, original path if no trim is needed, or None on failure.
    """
    if target_duration <= 0:
        log.warning("Smart trim target_duration must be > 0, got %s", target_duration)
        return None

    if not os.path.exists(video_path):
        return None

    duration = _get_duration(video_path)
    if not duration or duration <= 0:
        return None

    target = float(target_duration)
    if duration <= target + 0.01:
        return video_path

    sample_interval = 0.5
    timestamps: list[float] = []
    max_ts = max(duration - 0.1, 0.0)
    ts = 0.0
    while ts < duration:
        timestamps.append(min(ts, max_ts))
        ts += sample_interval
    if not timestamps:
        return None

    scores = _batch_sample_ydif(video_path, timestamps)
    if not scores:
        return None

    window_size = max(int(round(target / sample_interval)), 1)
    if len(scores) <= window_size:
        best_idx = 0
        best_total = sum(scores)
    else:
        running_total = sum(scores[:window_size])
        best_total = running_total
        best_idx = 0
        for i in range(window_size, len(scores)):
            running_total += scores[i] - scores[i - window_size]
            start_idx = i - window_size + 1
            if running_total > best_total:
                best_total = running_total
                best_idx = start_idx

    max_start = max(duration - target, 0.0)
    trim_start = max(0.0, min(timestamps[best_idx], max_start))

    tmp_output = output_path + ".tmp"
    cmd = [
        FFMPEG,
        "-y",
        "-ss", f"{trim_start:.2f}",
        "-i", video_path,
        "-t", f"{target:.2f}",
        "-map", "0:v:0",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-crf", "20",
        "-preset", "fast",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        tmp_output,
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)
    except subprocess.CalledProcessError as e:
        log.warning(
            "Smart trim ffmpeg failed for %s: %s",
            video_path,
            (e.stderr or "")[-500:],
        )
        safe_remove(tmp_output)
        return None
    except Exception as e:
        log.warning("Smart trim failed for %s: %s", video_path, e)
        safe_remove(tmp_output)
        return None

    if not os.path.exists(tmp_output) or os.path.getsize(tmp_output) == 0:
        safe_remove(tmp_output)
        return None

    os.replace(tmp_output, output_path)
    log.info(
        "Smart trimmed %s to %.2fs window starting at %.2fs (activity score %.2f)",
        os.path.basename(video_path),
        target,
        trim_start,
        best_total,
    )
    return output_path


def check_loop_compatibility(
    video_path: str,
    ydif_threshold: float = 8.0,
    duration: float | None = None,
) -> bool:
    """Check if first/last 0.5s are visually compatible for seamless looping.

    Returns True when loop is already smooth, False when a transition is recommended.
    """
    if duration is None:
        if not os.path.exists(video_path):
            return True
        duration = _get_duration(video_path)
    if not duration or duration < 1.0:
        return True

    cmd = [
        FFMPEG,
        "-ss", "0",
        "-i", video_path,
        "-sseof", "-0.5",
        "-i", video_path,
        "-filter_complex",
        "[0:v]trim=end_frame=1,setpts=PTS-STARTPTS[first];"
        "[1:v]trim=end_frame=1,setpts=PTS-STARTPTS[last];"
        "[first][last]blend=all_mode=difference,signalstats,metadata=print,trim=end_frame=1[out]",
        "-map", "[out]",
        "-f", "null",
        "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        ydif_values: list[float] = []
        for line in result.stderr.splitlines():
            if "signalstats.YDIF=" in line:
                with contextlib.suppress(ValueError, IndexError):
                    ydif_values.append(float(line.split("YDIF=")[1]))
        if not ydif_values:
            return True
        return ydif_values[-1] <= ydif_threshold
    except Exception as e:
        log.warning("Loop compatibility check failed for %s: %s", video_path, e)
        return True


def _apply_loop_crossfade(video_path: str, crossfade_duration: float = 0.3) -> bool:
    """Apply a short end->start crossfade to smooth looping."""
    duration = _get_duration(video_path)
    if not duration or duration <= crossfade_duration:
        return False

    offset = max(duration - crossfade_duration, 0.0)
    tmp_output = video_path + ".loop.tmp.mp4"
    cmd = [
        FFMPEG,
        "-y",
        "-i", video_path,
        "-i", video_path,
        "-filter_complex",
        (
            f"[1:v]trim=duration={crossfade_duration:.3f},setpts=PTS-STARTPTS[head];"
            f"[0:v][head]xfade=transition=fade:duration={crossfade_duration:.3f}:offset={offset:.3f}[v]"
        ),
        "-map", "[v]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-crf", "20",
        "-preset", "fast",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        tmp_output,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
        if not os.path.exists(tmp_output) or os.path.getsize(tmp_output) == 0:
            safe_remove(tmp_output)
            return False
        os.replace(tmp_output, video_path)
        return True
    except Exception as e:
        log.warning("Loop crossfade application failed for %s: %s", video_path, e)
        safe_remove(tmp_output)
        return False


def apply_loop_crossfade(video_path: str, crossfade_duration: float = 0.3) -> bool:
    """Public wrapper for applying a short end->start crossfade."""
    return _apply_loop_crossfade(video_path, crossfade_duration=crossfade_duration)


def crop_to_vertical(input_path: str, tmp_dir: str, max_duration: int = 60,
                     facecam: FacecamConfig | None = None,
                     facecam_mode: str = "auto",
                     subtitle_path: str | None = None,
                     silence_offset: float | None = None,
                     peak_action_trim: bool = True,
                     loop_optimize: bool = True) -> str | None:
    """Crop a 16:9 video to 9:16 vertical (1080x1920) with facecam+gameplay layout.

    If facecam config is provided, output is split: top 20% facecam, bottom 80% gameplay.
    Otherwise falls back to simple center-crop.
    """
    clip_id = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(tmp_dir, f"{clip_id}_vertical.mp4")

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        if is_valid_video(output_path):
            log.info("Vertical clip already exists: %s", output_path)
            return output_path
        else:
            log.warning("Cached output %s is invalid, re-processing", output_path)

    # Single probe for both duration and dimensions
    duration, dims = _probe_video_info(input_path)

    # Allow slight overage (60.5s) since YouTube Shorts limit is ~60s
    if duration is not None and duration > max_duration + 0.5:
        log.info("Skipping clip %s: duration %.1fs exceeds %ds limit", clip_id, duration, max_duration)
        return None

    trim_start = silence_offset if silence_offset is not None else detect_leading_silence(input_path)
    if trim_start > 0:
        log.info("Trimming %.2fs leading silence from %s", trim_start, clip_id)

    # After audio silence trim, check for visual dead frames (static/loading screens)
    visual_trim = detect_visual_dead_frames(input_path, start_offset=trim_start)
    if visual_trim > 0:
        log.info("Trimming additional %.2fs visual dead frames from %s", visual_trim, clip_id)
        trim_start += visual_trim

    if peak_action_trim:
        peak_ts = find_peak_action_timestamp(
            input_path,
            start_offset=trim_start,
            duration=duration,
            check_exists=False,
        )
        if peak_ts - trim_start > 3.0:
            new_trim_start = max(trim_start, peak_ts - 2.0)
            log.info("Peak action at %.2fs for %s, moving start to %.2fs", peak_ts, clip_id, new_trim_start)
            trim_start = new_trim_start

    mode = (facecam_mode or "auto").lower()
    if mode not in ("auto", "always", "off"):
        log.warning("Unknown facecam_mode '%s', defaulting to 'auto'", facecam_mode)
        mode = "auto"

    if not facecam or mode == "off":
        use_facecam = False
    elif mode == "always":
        use_facecam = True
    else:
        use_facecam = _has_facecam(input_path, facecam, clip_id, duration=duration)

    if dims is None:
        log.warning("Could not probe dimensions for %s, assuming 16:9", clip_id)
    source_ratio = (dims[0] / dims[1]) if dims else (16 / 9)

    if use_facecam and abs(source_ratio - 16 / 9) < 0.1:
        assert facecam is not None
        vf = _build_composite_filter(facecam)
    elif use_facecam:
        log.info("Skipping facecam overlay for %s: non-16:9 source (%.2f)", clip_id, source_ratio)
        vf = "crop=ih*9/16:ih,scale=1080:1920"
    elif source_ratio < 9 / 16:
        # Source is narrower than 9:16 — just scale, no crop
        vf = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2"
    else:
        vf = "crop=ih*9/16:ih,scale=1080:1920"

    # Measure loudness once, reuse across GPU/CPU attempts
    loudness = _measure_loudness(input_path)

    # Skip GPU if DISABLE_GPU_ENCODE is set (e.g., GitHub Actions has no CUDA)
    skip_gpu = os.environ.get("DISABLE_GPU_ENCODE", "").lower() in ("1", "true", "yes")

    # Try GPU encode first (if not disabled), fall back to CPU
    encoded = False
    if not skip_gpu and _run_ffmpeg(input_path, output_path, vf, clip_id, gpu=True, ss=trim_start, loudness=loudness, subtitle_path=subtitle_path):
        encoded = True
    elif _run_ffmpeg(input_path, output_path, vf, clip_id, gpu=False, ss=trim_start, loudness=loudness, subtitle_path=subtitle_path):
        encoded = True

    if encoded:
        if loop_optimize and not check_loop_compatibility(output_path, duration=duration):
            if _apply_loop_crossfade(output_path, crossfade_duration=0.3):
                log.info("Applied 0.3s loop crossfade for %s", clip_id)
        return output_path

    return None


def _has_facecam(input_path: str, facecam: FacecamConfig, clip_id: str, duration: float | None = None) -> bool:
    """Check if the facecam region contains an actual camera feed vs static UI.

    Uses a single ffmpeg invocation with 3 input seeks (25%, 50%, 75% of duration)
    to measure pixel variance in the facecam region. Low variance = static UI.
    """
    fx = facecam.x
    fy = facecam.y
    fw = facecam.w
    fh = facecam.h

    if duration is None:
        duration = _get_duration(input_path)

    # Build a single ffmpeg command with 3 seeks into the same file
    seek_times = []
    for pct in [0.25, 0.50, 0.75]:
        seek_times.append(str(max(1, int(duration * pct))) if duration else "1")

    crop_stats = f"crop=iw*{fw}:ih*{fh}:iw*{fx}:ih*{fy},signalstats,metadata=print"
    cmd = [FFMPEG]
    for st in seek_times:
        cmd += ["-ss", st, "-i", input_path]
    # Build filter_complex: each input gets 5 frames through crop+signalstats
    filters = []
    for i in range(len(seek_times)):
        filters.append(f"[{i}:v]{crop_stats},trim=end_frame=5[v{i}]")
    filters.append("[v0][v1][v2]concat=n=3:v=1:a=0[out]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "[out]", "-f", "null", "-"]

    ydif_values = []
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        for line in result.stderr.splitlines():
            if "signalstats.YDIF=" in line:
                with contextlib.suppress(ValueError, IndexError):
                    ydif_values.append(float(line.split("YDIF=")[1]))
    except subprocess.TimeoutExpired:
        log.warning("Facecam detection timed out for %s", clip_id)
    except Exception as e:
        log.warning("Facecam detection failed for %s: %s", clip_id, e)

    if ydif_values:
        avg_ydif = sum(ydif_values) / len(ydif_values)
        has_cam = avg_ydif > 1.0  # threshold: >1.0 = real motion
        log.info("Facecam check for %s: avg YDIF=%.2f (%d samples) -> %s",
                 clip_id, avg_ydif, len(ydif_values),
                 "facecam detected" if has_cam else "static UI, skipping overlay")
        return has_cam
    return False  # default to no overlay if detection fails


def _measure_loudness(input_path: str) -> dict | None:
    """Run a loudnorm first pass to measure audio loudness stats. Returns dict or None on failure."""
    cmd = [
        FFMPEG, "-i", input_path,
        "-af", "loudnorm=I=-14:LRA=11:TP=-1.5:print_format=json",
        "-vn", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        stderr = result.stderr
        matches = list(re.finditer(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr, re.DOTALL))
        if not matches:
            return None
        data = json.loads(matches[-1].group(0))
        keys = ["input_i", "input_tp", "input_lra", "input_thresh", "target_offset"]
        if all(k in data for k in keys):
            return {k: data[k] for k in keys}
        return None
    except Exception as e:
        log.warning("Loudness measurement failed for %s: %s", input_path, e)
        return None


def _build_composite_filter(facecam: FacecamConfig) -> str:
    """Build ffmpeg filtergraph for facecam (top 20%) + gameplay (bottom 80%)."""
    fx = facecam.x
    fy = facecam.y
    fw = facecam.w
    fh = facecam.h

    # Gameplay: full-height 1080x1920 center-crop
    game_crop = "crop=ih*9/16:ih:(iw-ih*9/16)/2:0"
    game = f"[0:v]{game_crop},scale=1080:1920[game]"

    # Facecam: crop from source, scale to ~25% width, preserve aspect, overlay top-center
    cam_crop = f"crop=iw*{fw}:ih*{fh}:iw*{fx}:ih*{fy}"
    cam_w = facecam.output_w
    cam_w = cam_w + (cam_w % 2)  # Ensure even width for encoder compatibility
    cam = f"[0:v]{cam_crop},scale={cam_w}:-2[cam]"

    return f"{game};{cam};[game][cam]overlay=(W-w)/2:0[out]"


def _escape_subtitle_path(path: str) -> str:
    """Escape a file path for ffmpeg's subtitles/ass filter (Windows-compatible)."""
    escaped = str(path).replace("\\", "/").replace(":", "\\:")
    escaped = escaped.replace("'", "'\\\\\\''")  # escape single quotes for ffmpeg filter
    escaped = escaped.replace(";", "\\;")
    escaped = escaped.replace("[", "\\[").replace("]", "\\]")
    return escaped


def _run_ffmpeg(input_path: str, output_path: str, vf: str,
                clip_id: str, gpu: bool, ss: float = 0.0,
                loudness: dict | None = None,
                subtitle_path: str | None = None) -> bool:
    """Run ffmpeg with given filter. Returns True on success.

    Writes to a temp file and atomically renames on success to prevent
    partial/corrupt outputs from being used.
    """
    tmp_output = output_path + ".tmp"
    cmd = [FFMPEG, "-y"]

    if ss > 0:
        cmd += ["-ss", str(ss)]

    use_videotoolbox = gpu and sys.platform == "darwin"

    if gpu and not use_videotoolbox:
        cmd += ["-hwaccel", "cuda"]

    cmd += ["-i", input_path]

    # Use -filter_complex with [out] map if composite, else -vf
    if "[out]" in vf:
        cmd += ["-filter_complex", vf, "-map", "[out]", "-map", "0:a?"]
    else:
        cmd += ["-vf", vf]

    # Inject subtitle filter if captions are provided
    if subtitle_path:
        escaped = _escape_subtitle_path(subtitle_path)
        if "[out]" in vf:
            # Composite mode: rename [out] to [tmp], append subtitle filter
            idx = cmd.index("-filter_complex")
            cmd[idx + 1] = cmd[idx + 1].replace("[out]", "[tmp]") + f";[tmp]ass={escaped}[out]"
        else:
            # Simple mode: append to -vf value
            idx = cmd.index("-vf")
            cmd[idx + 1] = cmd[idx + 1] + f",ass={escaped}"

    if gpu:
        if use_videotoolbox:
            cmd += [
                "-c:v", "h264_videotoolbox",
                "-b:v", "5M",
                "-maxrate", "6M",
                "-bufsize", "10M",
            ]
        else:
            cmd += ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23"]
    else:
        cpu_preset = "fast"
        cmd += ["-c:v", "libx264", "-crf", "20", "-preset", cpu_preset]

    # Two-pass loudnorm: use measured stats if they are all finite floats,
    # else fall back to single-pass normalization.
    normalized_loudness = _normalize_loudness_stats(loudness)
    if normalized_loudness:
        af = (
            f"loudnorm=I=-14:LRA=11:TP=-1.5"
            f":measured_I={normalized_loudness['input_i']}"
            f":measured_TP={normalized_loudness['input_tp']}"
            f":measured_LRA={normalized_loudness['input_lra']}"
            f":measured_thresh={normalized_loudness['input_thresh']}"
            f":offset={normalized_loudness['target_offset']}"
            f":linear=true"
        )
    else:
        af = "loudnorm=I=-14:TP=-1.5:LRA=11"
    cmd += ["-af", af, "-c:a", "aac", "-b:a", "192k", "-f", "mp4", tmp_output]

    label = "GPU" if gpu else "CPU"
    log.info("Processing %s -> vertical (%s)", clip_id, label)

    proc = None
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            _, stderr_bytes = proc.communicate(timeout=300)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            log.error("FFmpeg %s timed out for %s", label, clip_id)
            safe_remove(tmp_output)
            return False

        if proc.returncode != 0:
            log.warning("FFmpeg %s failed for %s: %s", label, clip_id,
                        stderr_bytes.decode(errors="replace")[-500:])
            safe_remove(tmp_output)
            return False
    except Exception as e:
        log.error("FFmpeg %s error for %s: %s", label, clip_id, e)
        if proc and proc.poll() is None:
            proc.kill()
            proc.wait()
        safe_remove(tmp_output)
        return False

    if not os.path.exists(tmp_output) or os.path.getsize(tmp_output) == 0:
        log.error("FFmpeg %s produced empty output for %s", label, clip_id)
        safe_remove(tmp_output)
        return False

    os.replace(tmp_output, output_path)
    log.info("Processed vertical clip (%s): %s", label, output_path)
    return True


def _normalize_loudness_stats(loudness: dict | None) -> dict[str, float] | None:
    if not isinstance(loudness, dict):
        return None
    keys = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    normalized: dict[str, float] = {}
    for key in keys:
        raw = loudness.get(key)
        if raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        normalized[key] = value
    return normalized

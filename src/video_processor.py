import contextlib
import json
import logging
import math
import os
import re
import subprocess

from src.media_utils import FFMPEG, FFPROBE, is_valid_video, safe_remove
from src.models import FacecamConfig

log = logging.getLogger(__name__)


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
    """Sample YDIF at multiple timestamps using a single ffmpeg filter_complex call.

    Returns a list of max-YDIF values, one per timestamp (0.0 on failure).
    """
    if not timestamps:
        return []

    n = len(timestamps)
    cmd = [FFMPEG]
    for ts in timestamps:
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
        # Parse all YDIF values from stderr
        all_ydif: list[float] = []
        for line in result.stderr.splitlines():
            if "signalstats.YDIF=" in line:
                with contextlib.suppress(ValueError, IndexError):
                    all_ydif.append(float(line.split("YDIF=")[1]))
        # With 1 frame per input, we expect 1 YDIF per timestamp
        # If we got fewer, pad with 0.0; if more (shouldn't happen), take first n
        scores = []
        for i in range(n):
            scores.append(all_ydif[i] if i < len(all_ydif) else 0.0)
        return scores
    except Exception as e:
        log.warning("Batch YDIF sampling failed: %s", e)
        return [0.0] * n


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


def crop_to_vertical(input_path: str, tmp_dir: str, max_duration: int = 60,
                     facecam: FacecamConfig | None = None,
                     facecam_mode: str = "auto",
                     subtitle_path: str | None = None,
                     silence_offset: float | None = None) -> str | None:
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
    if not skip_gpu and _run_ffmpeg(input_path, output_path, vf, clip_id, gpu=True, ss=trim_start, loudness=loudness, subtitle_path=subtitle_path):
        return output_path
    if _run_ffmpeg(input_path, output_path, vf, clip_id, gpu=False, ss=trim_start, loudness=loudness, subtitle_path=subtitle_path):
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

    if gpu:
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
            cmd[idx + 1] = cmd[idx + 1].replace("[out]", "[tmp]") + f";[tmp]ass='{escaped}'[out]"
        else:
            # Simple mode: append to -vf value
            idx = cmd.index("-vf")
            cmd[idx + 1] = cmd[idx + 1] + f",ass='{escaped}'"

    if gpu:
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
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        normalized[key] = value
    return normalized

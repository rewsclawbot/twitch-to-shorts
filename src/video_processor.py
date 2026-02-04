import json
import logging
import os
import re
import shutil
import subprocess

from src.models import FacecamConfig

log = logging.getLogger(__name__)

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


def _remove_file(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


def _get_duration(path: str) -> float | None:
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=15,
        )
        return float(result.stdout.strip())
    except Exception as e:
        log.warning("Failed to get duration for %s: %s", path, e)
        return None


def _get_dimensions(path: str) -> tuple[int, int] | None:
    """Probe source video dimensions (width, height)."""
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "json", path],
            capture_output=True, text=True, timeout=15,
        )
        info = json.loads(result.stdout)
        stream = info["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except Exception as e:
        log.warning("Failed to get dimensions for %s: %s", path, e)
        return None


def _sample_ydif(input_path: str, timestamp: float) -> float:
    cmd = [
        FFMPEG, "-ss", f"{timestamp:.2f}", "-i", input_path,
        "-vf", "signalstats,metadata=print",
        "-frames:v", "1",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        values: list[float] = []
        for line in result.stderr.splitlines():
            if "signalstats.YDIF=" in line:
                try:
                    values.append(float(line.split("YDIF=")[1]))
                except (ValueError, IndexError):
                    pass
        return max(values) if values else 0.0
    except Exception as e:
        log.warning("Thumbnail motion sampling failed at %.2fs: %s", timestamp, e)
        return 0.0


def extract_thumbnail(
    input_path: str,
    tmp_dir: str,
    samples: int = 8,
    width: int = 1280,
) -> str | None:
    """Extract a thumbnail from the most active frame in the clip."""
    os.makedirs(tmp_dir, exist_ok=True)
    clip_id = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(tmp_dir, f"{clip_id}_thumb.jpg")

    duration = _get_duration(input_path)
    if not duration or duration <= 0:
        return None
    if samples <= 0:
        return None

    step = duration / (samples + 1)
    timestamps = [max(0.1, min(duration - 0.1, step * (i + 1))) for i in range(samples)]

    best_ts = timestamps[0]
    best_score = -1.0
    for ts in timestamps:
        score = _sample_ydif(input_path, ts)
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
        _remove_file(output_path)
        return None

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        _remove_file(output_path)
        return None
    return output_path


def _detect_leading_silence(input_path: str, threshold_db: float = -30, min_duration: float = 0.5) -> float:
    """Return duration of leading silence in seconds (0.0 if none). Capped at 5s."""
    cmd = [
        FFMPEG, "-i", input_path,
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
                     facecam_mode: str = "auto") -> str | None:
    """Crop a 16:9 video to 9:16 vertical (1080x1920) with facecam+gameplay layout.

    If facecam config is provided, output is split: top 20% facecam, bottom 80% gameplay.
    Otherwise falls back to simple center-crop.
    """
    clip_id = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(tmp_dir, f"{clip_id}_vertical.mp4")

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        log.info("Vertical clip already exists: %s", output_path)
        return output_path

    duration = _get_duration(input_path)
    # Allow slight overage (60.5s) since YouTube Shorts limit is ~60s
    if duration is not None and duration > max_duration + 0.5:
        log.info("Skipping clip %s: duration %.1fs exceeds %ds limit", clip_id, duration, max_duration)
        return None

    silence_offset = _detect_leading_silence(input_path)
    if silence_offset > 0:
        log.info("Trimming %.2fs leading silence from %s", silence_offset, clip_id)

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

    # Probe source dimensions for non-16:9 handling
    dims = _get_dimensions(input_path)
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
    if not skip_gpu and _run_ffmpeg(input_path, output_path, vf, clip_id, gpu=True, ss=silence_offset, loudness=loudness):
        return output_path
    if _run_ffmpeg(input_path, output_path, vf, clip_id, gpu=False, ss=silence_offset, loudness=loudness):
        return output_path

    return None


def _has_facecam(input_path: str, facecam: FacecamConfig, clip_id: str, duration: float | None = None) -> bool:
    """Check if the facecam region contains an actual camera feed vs static UI.

    Samples frames at 25% of duration and measures pixel variance in the expected
    facecam region. Low variance = static UI element, not a real facecam.
    """
    fx = facecam.x
    fy = facecam.y
    fw = facecam.w
    fh = facecam.h

    # Multi-point sampling: 25%, 50%, 75% of duration for robust detection
    if duration is None:
        duration = _get_duration(input_path)

    crop_filter = f"crop=iw*{fw}:ih*{fh}:iw*{fx}:ih*{fy},signalstats,metadata=print"
    ydif_values = []

    for pct in [0.25, 0.50, 0.75]:
        seek_time = str(max(1, int(duration * pct))) if duration else "1"
        cmd = [
            FFMPEG, "-ss", seek_time, "-i", input_path,
            "-vf", crop_filter,
            "-frames:v", "5",
            "-f", "null", "-",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            for line in result.stderr.splitlines():
                if "signalstats.YDIF=" in line:
                    try:
                        ydif_values.append(float(line.split("YDIF=")[1]))
                    except (ValueError, IndexError):
                        pass
        except subprocess.TimeoutExpired:
            log.warning("Facecam detection timed out at %.0f%% for %s", pct * 100, clip_id)
        except Exception as e:
            log.warning("Facecam detection failed at %.0f%% for %s: %s", pct * 100, clip_id, e)

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


def _run_ffmpeg(input_path: str, output_path: str, vf: str,
                clip_id: str, gpu: bool, ss: float = 0.0,
                loudness: dict | None = None) -> bool:
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

    if gpu:
        cmd += ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23"]
    else:
        cmd += ["-c:v", "libx264", "-crf", "20", "-preset", "medium"]

    # Two-pass loudnorm: use measured stats if available, else fall back to single-pass
    if loudness:
        af = (
            f"loudnorm=I=-14:LRA=11:TP=-1.5"
            f":measured_I={loudness['input_i']}"
            f":measured_TP={loudness['input_tp']}"
            f":measured_LRA={loudness['input_lra']}"
            f":measured_thresh={loudness['input_thresh']}"
            f":offset={loudness['target_offset']}"
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
            _remove_file(tmp_output)
            return False

        if proc.returncode != 0:
            log.warning("FFmpeg %s failed for %s: %s", label, clip_id,
                        stderr_bytes.decode(errors="replace")[-500:])
            _remove_file(tmp_output)
            return False
    except Exception as e:
        log.error("FFmpeg %s error for %s: %s", label, clip_id, e)
        if proc and proc.poll() is None:
            proc.kill()
            proc.wait()
        _remove_file(tmp_output)
        return False

    if not os.path.exists(tmp_output) or os.path.getsize(tmp_output) == 0:
        log.error("FFmpeg %s produced empty output for %s", label, clip_id)
        _remove_file(tmp_output)
        return False

    os.replace(tmp_output, output_path)
    log.info("Processed vertical clip (%s): %s", label, output_path)
    return True

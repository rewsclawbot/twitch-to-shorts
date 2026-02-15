"""Hook strength detector for video clips.

Analyzes the first 3 seconds of a clip to score how likely viewers are to keep watching.
Combines visual activity, audio energy, and title excitement into a single hook score.
"""

import logging
import re
import subprocess
from typing import Optional

from src.media_utils import FFMPEG
from src.video_processor import _batch_sample_ydif

log = logging.getLogger(__name__)


def _title_quality(title: str) -> float:
    """Score title excitement based on markers like caps, punctuation, etc.
    
    This is duplicated from clip_filter.py to avoid circular imports.
    """
    if not title:
        return 0.0
    text = title.strip()
    if not text:
        return 0.0
    score = 0.0
    if any(ch in text for ch in "!?"):
        score += 0.25
    alpha = [c for c in text if c.isalpha()]
    if alpha:
        upper_ratio = sum(c.isupper() for c in alpha) / len(alpha)
        if upper_ratio >= 0.6:
            score += 0.25
    length = len(text)
    if 10 <= length <= 80:
        score += 0.25
    if any(ch.isdigit() for ch in text):
        score += 0.25
    return min(score, 1.0)


def _analyze_audio_loudness(video_path: str, start: float = 0.0, duration: float = 3.0) -> float:
    """Analyze audio loudness in a time segment using ffmpeg astats filter.
    
    Returns a normalized score (0.0-1.0) based on RMS level and peak detection.
    Higher values indicate more audio energy/excitement.
    
    Args:
        video_path: Path to video file
        start: Start time in seconds
        duration: Duration to analyze in seconds
        
    Returns:
        Audio energy score (0.0-1.0)
    """
    cmd = [
        FFMPEG,
        "-ss", f"{start:.2f}",
        "-t", f"{duration:.2f}",
        "-i", video_path,
        "-vn",
        "-af", "astats=metadata=1:reset=1",
        "-f", "null",
        "-"
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        stderr = result.stderr
        
        # Extract RMS level (Overall RMS level in dB, typically -60 to 0)
        rms_match = re.search(r"Overall RMS level dB:\s*([-\d.]+)", stderr)
        # Extract peak level
        peak_match = re.search(r"Peak level dB:\s*([-\d.]+)", stderr)
        
        if not rms_match:
            log.debug("Could not extract RMS level from audio analysis")
            return 0.0
            
        rms_db = float(rms_match.group(1))
        peak_db = float(peak_match.group(1)) if peak_match else rms_db
        
        # Normalize RMS: -60dB (very quiet) -> 0.0, -10dB (loud) -> 1.0
        # Most gaming content is in -40 to -15 range
        rms_normalized = max(0.0, min(1.0, (rms_db + 60) / 50))
        
        # Normalize peak: -30dB -> 0.0, 0dB -> 1.0
        peak_normalized = max(0.0, min(1.0, (peak_db + 30) / 30))
        
        # Combine RMS (70%) and peak (30%) - RMS is more reliable
        audio_score = 0.7 * rms_normalized + 0.3 * peak_normalized
        
        log.debug(
            "Audio analysis: RMS=%.1fdB (%.2f), Peak=%.1fdB (%.2f), Score=%.3f",
            rms_db, rms_normalized, peak_db, peak_normalized, audio_score
        )
        
        return audio_score
        
    except subprocess.TimeoutExpired:
        log.warning("Audio analysis timeout for %s", video_path)
        return 0.0
    except Exception as e:
        log.warning("Audio analysis failed for %s: %s", video_path, e)
        return 0.0


def _analyze_visual_activity(video_path: str, duration: float, hook_window: float = 3.0) -> float:
    """Analyze visual activity in the first hook_window seconds vs the rest of the clip.
    
    Uses YDIF (frame difference) sampling to measure visual motion/action.
    Returns a score (0.0-1.0) where higher values indicate more action in the hook.
    
    Args:
        video_path: Path to video file
        duration: Total clip duration in seconds
        hook_window: Duration of hook window to analyze (default 3.0s)
        
    Returns:
        Visual activity score (0.0-1.0)
    """
    if duration <= hook_window:
        # Entire clip is the hook - give it a neutral score
        return 0.5
        
    # Sample at 0.5s intervals
    sample_interval = 0.5
    hook_samples = int(hook_window / sample_interval)
    rest_samples = min(int((duration - hook_window) / sample_interval), hook_samples * 2)
    
    # Sample timestamps: hook period + comparison period from the rest
    hook_timestamps = [i * sample_interval for i in range(hook_samples)]
    rest_timestamps = [hook_window + i * sample_interval for i in range(rest_samples)]
    all_timestamps = hook_timestamps + rest_timestamps
    
    try:
        scores = _batch_sample_ydif(video_path, all_timestamps)
        
        if not scores or len(scores) < len(all_timestamps):
            log.debug("Insufficient YDIF samples for visual activity analysis")
            return 0.5
            
        hook_scores = scores[:hook_samples]
        rest_scores = scores[hook_samples:]
        
        # Average YDIF for each period
        avg_hook = sum(hook_scores) / len(hook_scores) if hook_scores else 0.0
        avg_rest = sum(rest_scores) / len(rest_scores) if rest_scores else 0.0
        
        # Normalize based on typical YDIF ranges (0-100+ range, but usually 0-20)
        # High YDIF = lots of motion
        hook_normalized = min(1.0, avg_hook / 20.0)
        
        # Compare hook to rest of clip
        if avg_rest > 0:
            ratio = avg_hook / avg_rest
            # If hook has 1.5x+ more action than rest, boost score
            # If hook has less action, penalize
            ratio_bonus = max(0.0, min(0.3, (ratio - 1.0) * 0.5))
            visual_score = min(1.0, hook_normalized + ratio_bonus)
        else:
            visual_score = hook_normalized
            
        log.debug(
            "Visual activity: hook=%.2f, rest=%.2f, score=%.3f",
            avg_hook, avg_rest, visual_score
        )
        
        return visual_score
        
    except Exception as e:
        log.warning("Visual activity analysis failed for %s: %s", video_path, e)
        return 0.5


def score_hook_strength(
    video_path: str,
    clip_title: str,
    duration: float,
    hook_window: float = 3.0
) -> float:
    """Score 0.0-1.0 how strong the first 3 seconds hook is.
    
    Combines three signals:
    - Visual activity (YDIF score in first 3s vs rest of clip) — 50% weight
    - Audio energy/loudness in first 3s — 30% weight
    - Title excitement (caps, punctuation, etc.) — 20% weight
    
    Args:
        video_path: Path to downloaded video file
        clip_title: Clip title for excitement scoring
        duration: Total clip duration in seconds
        hook_window: Duration of hook window to analyze (default 3.0s)
        
    Returns:
        Hook strength score (0.0-1.0)
    """
    try:
        # Visual activity: 50% weight
        visual_score = _analyze_visual_activity(video_path, duration, hook_window)
        
        # Audio energy: 30% weight
        audio_score = _analyze_audio_loudness(video_path, start=0.0, duration=hook_window)
        
        # Title excitement: 20% weight
        title_score = _title_quality(clip_title)
        
        # Weighted combination
        hook_score = (
            0.5 * visual_score +
            0.3 * audio_score +
            0.2 * title_score
        )
        
        log.info(
            "Hook strength: visual=%.3f, audio=%.3f, title=%.3f -> %.3f",
            visual_score, audio_score, title_score, hook_score
        )
        
        return hook_score
        
    except Exception as e:
        log.error("Hook strength scoring failed for %s: %s", video_path, e)
        return 0.0

"""Audio excitement scoring for viral clip prediction.

Analyzes audio energy, volume spikes, and speech density to estimate
how exciting a clip sounds. Higher scores indicate more engaging audio.
"""

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

# Normalization constants (tuned empirically)
_RMS_ENERGY_SCALE = 0.05  # typical range 0-0.2, scale to ~0-1
_SPIKE_COUNT_SCALE = 10.0  # ~5-15 spikes in 15s clip -> 0.5-1.5
_VARIANCE_SCALE = 0.001  # variance range ~0-0.01


def _extract_audio_stats(video_path: str, tmp_dir: str) -> dict | None:
    """Extract audio statistics using ffmpeg volumedetect and astats filters.
    
    Returns dict with:
        - mean_volume: RMS energy level (dB)
        - max_volume: peak volume (dB)  
        - volume_variance: audio energy variance
    """
    try:
        # Run ffmpeg with volumedetect and astats filters
        # volumedetect gives us mean/max volume in dB
        # astats gives us RMS level and variance
        cmd = [
            "ffmpeg",
            "-i", video_path,
            "-filter_complex",
            "[0:a]volumedetect,astats=metadata=1:reset=1[out]",
            "-map", "[out]",
            "-f", "null",
            "-"
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        # Parse volumedetect output from stderr
        stderr = result.stderr
        mean_volume = None
        max_volume = None
        rms_level = None
        
        for line in stderr.split('\n'):
            if 'mean_volume:' in line:
                try:
                    mean_volume = float(line.split('mean_volume:')[1].split('dB')[0].strip())
                except (IndexError, ValueError):
                    pass
            elif 'max_volume:' in line:
                try:
                    max_volume = float(line.split('max_volume:')[1].split('dB')[0].strip())
                except (IndexError, ValueError):
                    pass
            elif 'RMS level dB:' in line:
                try:
                    rms_level = float(line.split('RMS level dB:')[1].strip())
                except (IndexError, ValueError):
                    pass
        
        if mean_volume is None or max_volume is None:
            log.warning("Could not parse volume stats from ffmpeg output")
            return None
            
        # Calculate variance from multiple measurements
        # We'll use a simpler approach: extract audio segments and compute variance
        variance = _compute_audio_variance(video_path, tmp_dir)
        
        return {
            'mean_volume': mean_volume,
            'max_volume': max_volume,
            'volume_variance': variance,
        }
        
    except subprocess.TimeoutExpired:
        log.warning("ffmpeg audio analysis timed out for %s", video_path)
        return None
    except Exception as e:
        log.warning("Audio stats extraction failed for %s: %s", video_path, e)
        return None


def _compute_audio_variance(video_path: str, tmp_dir: str) -> float:
    """Compute audio energy variance using frame-level RMS values.
    
    Higher variance = more dynamic audio (more exciting).
    """
    try:
        # Extract RMS energy per 100ms window
        cmd = [
            "ffmpeg",
            "-i", video_path,
            "-filter_complex",
            "[0:a]asplit=2[a1][a2];"
            "[a1]astats=metadata=1:reset=1:length=0.1[out1];"
            "[a2]showvolume=w=1:h=1:r=10[out2]",
            "-map", "[out1]",
            "-f", "null",
            "-"
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        # Parse RMS values from metadata
        rms_values = []
        for line in result.stderr.split('\n'):
            if 'lavfi.astats.Overall.RMS_level' in line:
                try:
                    # Convert dB to linear scale for variance calculation
                    db_value = float(line.split('=')[1].strip())
                    # Convert dB to linear: 10^(dB/20)
                    linear_value = 10 ** (db_value / 20)
                    rms_values.append(linear_value)
                except (IndexError, ValueError):
                    pass
        
        if len(rms_values) < 2:
            return 0.0
        
        # Calculate variance manually (avoid numpy dependency if possible)
        mean = sum(rms_values) / len(rms_values)
        variance = sum((x - mean) ** 2 for x in rms_values) / len(rms_values)
        
        return variance
        
    except Exception as e:
        log.debug("Variance computation failed: %s", e)
        return 0.0


def _detect_volume_spikes(video_path: str, spike_threshold_db: float = -10.0) -> int:
    """Count number of volume spikes above threshold.
    
    A spike is a sudden increase in volume (> spike_threshold_db).
    More spikes = more exciting moments.
    
    Args:
        video_path: Path to video file
        spike_threshold_db: dB threshold for detecting spikes (default: -10 dB)
    
    Returns:
        Number of detected spikes
    """
    try:
        # Use ffmpeg silencedetect in reverse (detect loud parts)
        # We'll extract volume levels and count spikes manually
        cmd = [
            "ffmpeg",
            "-i", video_path,
            "-filter:a",
            f"astats=metadata=1:reset=0.1,ametadata=print:file=-:key=lavfi.astats.Overall.Peak_level",
            "-f", "null",
            "-"
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        # Parse peak levels and count spikes
        peak_levels = []
        for line in result.stderr.split('\n'):
            if 'lavfi.astats.Overall.Peak_level=' in line:
                try:
                    db_value = float(line.split('=')[1].strip())
                    peak_levels.append(db_value)
                except (IndexError, ValueError):
                    pass
        
        if not peak_levels:
            return 0
        
        # Count spikes: peaks that exceed threshold
        spike_count = sum(1 for level in peak_levels if level > spike_threshold_db)
        
        return spike_count
        
    except Exception as e:
        log.debug("Spike detection failed: %s", e)
        return 0


def _estimate_speech_density(video_path: str, tmp_dir: str) -> float:
    """Estimate speech density using Whisper segment count.
    
    More segments = more talking = more engaging content.
    Falls back to silence detection if Whisper is unavailable.
    
    Args:
        video_path: Path to video file
        tmp_dir: Temporary directory for audio extraction
        
    Returns:
        Speech density ratio (0-1), normalized by clip duration
    """
    try:
        # Try using Whisper if available
        try:
            import whisper
            
            # Extract audio for Whisper
            clip_id = os.path.splitext(os.path.basename(video_path))[0]
            audio_path = os.path.join(tmp_dir, f"{clip_id}_speech_audio.wav")
            
            extract_cmd = [
                "ffmpeg",
                "-i", video_path,
                "-vn",  # no video
                "-acodec", "pcm_s16le",
                "-ar", "16000",  # Whisper prefers 16kHz
                "-ac", "1",  # mono
                "-y",
                audio_path
            ]
            
            subprocess.run(extract_cmd, capture_output=True, timeout=30, check=True)
            
            # Run Whisper transcription
            model = whisper.load_model("base")
            result = model.transcribe(audio_path, language="en")
            
            # Clean up audio file
            try:
                os.remove(audio_path)
            except:
                pass
            
            # Count segments and calculate density
            segments = result.get("segments", [])
            if not segments:
                return 0.0
            
            # Get video duration
            duration = _get_video_duration(video_path)
            if duration <= 0:
                return 0.0
            
            # Calculate speech coverage ratio
            total_speech_time = sum(
                seg.get('end', 0) - seg.get('start', 0)
                for seg in segments
            )
            
            density = min(total_speech_time / duration, 1.0)
            
            log.debug(
                "Speech density for %s: %d segments, %.1f%% coverage",
                os.path.basename(video_path),
                len(segments),
                density * 100
            )
            
            return density
            
        except ImportError:
            log.debug("Whisper not available, using silence detection fallback")
        except Exception as e:
            log.debug("Whisper analysis failed: %s, falling back to silence detection", e)
        
        # Fallback: use silence detection (inverse of silence = speech)
        duration = _get_video_duration(video_path)
        if duration <= 0:
            return 0.0
        
        silence_duration = _detect_total_silence(video_path)
        speech_duration = max(0, duration - silence_duration)
        density = min(speech_duration / duration, 1.0)
        
        return density
        
    except Exception as e:
        log.debug("Speech density estimation failed: %s", e)
        return 0.0


def _get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            video_path
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        data = json.loads(result.stdout)
        duration = float(data['format']['duration'])
        return duration
        
    except Exception as e:
        log.debug("Duration detection failed: %s", e)
        return 0.0


def _detect_total_silence(video_path: str, silence_threshold_db: float = -40.0) -> float:
    """Detect total duration of silence in video.
    
    Args:
        video_path: Path to video file
        silence_threshold_db: dB threshold for silence detection
        
    Returns:
        Total silence duration in seconds
    """
    try:
        cmd = [
            "ffmpeg",
            "-i", video_path,
            "-af", f"silencedetect=n={silence_threshold_db}dB:d=0.3",
            "-f", "null",
            "-"
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        # Parse silence periods from stderr
        silence_durations = []
        for line in result.stderr.split('\n'):
            if 'silence_duration:' in line:
                try:
                    duration = float(line.split('silence_duration:')[1].strip())
                    silence_durations.append(duration)
                except (IndexError, ValueError):
                    pass
        
        total_silence = sum(silence_durations)
        return total_silence
        
    except Exception as e:
        log.debug("Silence detection failed: %s", e)
        return 0.0


def score_audio_excitement(video_path: str, tmp_dir: str) -> float:
    """Score a video clip's audio excitement level.
    
    Combines multiple audio features:
    - RMS energy level (overall loudness)
    - Volume spikes (exciting moments)
    - Speech density (engagement through talking)
    - Audio variance (dynamic vs flat audio)
    
    Args:
        video_path: Path to video file to analyze
        tmp_dir: Temporary directory for intermediate files
        
    Returns:
        Normalized excitement score from 0.0 (boring) to 1.0 (very exciting)
    """
    if not os.path.exists(video_path):
        log.warning("Video file not found: %s", video_path)
        return 0.0
    
    # Create tmp_dir if it doesn't exist
    os.makedirs(tmp_dir, exist_ok=True)
    
    log.info("Analyzing audio excitement for %s", os.path.basename(video_path))
    
    # Extract basic audio stats
    stats = _extract_audio_stats(video_path, tmp_dir)
    if not stats:
        log.warning("Could not extract audio stats, returning baseline score")
        return 0.3  # baseline score for failed analysis
    
    # Detect volume spikes
    spike_count = _detect_volume_spikes(video_path)
    
    # Estimate speech density
    speech_density = _estimate_speech_density(video_path, tmp_dir)
    
    # Normalize features to 0-1 range
    # RMS energy: convert from dB (-60 to 0) to 0-1
    # Higher (closer to 0) = louder = more exciting
    mean_volume_db = stats['mean_volume']
    energy_score = min(max((mean_volume_db + 60) / 60, 0.0), 1.0)
    
    # Volume variance: more variance = more exciting
    variance = stats['volume_variance']
    variance_score = min(variance * _VARIANCE_SCALE * 100, 1.0)
    
    # Spike count: normalize by video duration
    duration = _get_video_duration(video_path)
    if duration > 0:
        spikes_per_second = spike_count / duration
        spike_score = min(spikes_per_second * _SPIKE_COUNT_SCALE, 1.0)
    else:
        spike_score = 0.0
    
    # Speech density is already 0-1
    speech_score = speech_density
    
    # Weighted combination of features
    # Energy and spikes are most important for "excitement"
    # Speech density indicates engagement
    # Variance indicates dynamic content
    final_score = (
        energy_score * 0.30 +      # Overall loudness
        spike_score * 0.35 +       # Exciting moments
        speech_score * 0.20 +      # Engagement through talking
        variance_score * 0.15      # Dynamic audio
    )
    
    # Ensure score is in valid range
    final_score = min(max(final_score, 0.0), 1.0)
    
    log.info(
        "Audio excitement for %s: %.3f (energy=%.2f, spikes=%.2f, speech=%.2f, variance=%.2f)",
        os.path.basename(video_path),
        final_score,
        energy_score,
        spike_score,
        speech_score,
        variance_score
    )
    
    return final_score

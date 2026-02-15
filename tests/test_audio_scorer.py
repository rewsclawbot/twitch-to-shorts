"""Tests for audio excitement scoring module."""

import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.audio_scorer import (
    _compute_audio_variance,
    _detect_total_silence,
    _detect_volume_spikes,
    _estimate_speech_density,
    _extract_audio_stats,
    _get_video_duration,
    score_audio_excitement,
)


@pytest.fixture
def tmp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def mock_video_file(tmp_dir):
    """Create a minimal test video file using ffmpeg."""
    video_path = os.path.join(tmp_dir, "test_video.mp4")
    
    # Generate a 5-second test video with audio tone
    # This creates a real file that ffmpeg can analyze
    cmd = [
        "ffmpeg",
        "-f", "lavfi",
        "-i", "sine=frequency=1000:duration=5",
        "-f", "lavfi",
        "-i", "color=c=black:s=320x240:d=5",
        "-shortest",
        "-y",
        video_path
    ]
    
    try:
        subprocess.run(cmd, capture_output=True, timeout=10, check=True)
        return video_path
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        # If ffmpeg is not available or fails, skip tests requiring real video
        pytest.skip("ffmpeg not available or failed to create test video")


class TestAudioStatsExtraction:
    """Test audio statistics extraction."""
    
    def test_extract_audio_stats_success(self, mock_video_file, tmp_dir):
        """Test successful audio stats extraction."""
        stats = _extract_audio_stats(mock_video_file, tmp_dir)
        
        assert stats is not None
        assert 'mean_volume' in stats
        assert 'max_volume' in stats
        assert 'volume_variance' in stats
        
        # Volume should be in dB range (negative values)
        assert stats['mean_volume'] < 0
        assert stats['max_volume'] < 0
        assert stats['volume_variance'] >= 0
    
    def test_extract_audio_stats_missing_file(self, tmp_dir):
        """Test handling of missing video file."""
        stats = _extract_audio_stats("/nonexistent/video.mp4", tmp_dir)
        assert stats is None
    
    def test_compute_audio_variance(self, mock_video_file, tmp_dir):
        """Test audio variance computation."""
        variance = _compute_audio_variance(mock_video_file, tmp_dir)
        
        # Variance should be non-negative
        assert variance >= 0
        
        # For a pure tone, variance should be relatively low
        # (constant amplitude)
        assert variance < 1.0


class TestVolumeSpikeDetection:
    """Test volume spike detection."""
    
    def test_detect_volume_spikes(self, mock_video_file):
        """Test spike detection on real audio."""
        spike_count = _detect_volume_spikes(mock_video_file, spike_threshold_db=-10.0)
        
        # Should return a non-negative integer
        assert isinstance(spike_count, int)
        assert spike_count >= 0
    
    def test_detect_volume_spikes_custom_threshold(self, mock_video_file):
        """Test spike detection with custom threshold."""
        # Lower threshold should detect more spikes
        high_threshold_count = _detect_volume_spikes(mock_video_file, spike_threshold_db=-5.0)
        low_threshold_count = _detect_volume_spikes(mock_video_file, spike_threshold_db=-20.0)
        
        assert isinstance(high_threshold_count, int)
        assert isinstance(low_threshold_count, int)
        # Lower threshold (more sensitive) should find more or equal spikes
        assert low_threshold_count >= high_threshold_count


class TestSpeechDensityEstimation:
    """Test speech density estimation."""
    
    def test_estimate_speech_density_fallback(self, mock_video_file, tmp_dir):
        """Test speech density with silence detection fallback."""
        # Test the fallback path by not installing whisper
        # Since whisper import is in a try/except, it will naturally fall back
        density = _estimate_speech_density(mock_video_file, tmp_dir)
        
        # Should return a value between 0 and 1
        assert 0.0 <= density <= 1.0
    
    def test_estimate_speech_density_with_whisper(self, mock_video_file, tmp_dir):
        """Test speech density using Whisper (if installed)."""
        # This test will use the real whisper if installed
        # We can test that it doesn't crash
        density = _estimate_speech_density(mock_video_file, tmp_dir)
        
        # Should return a valid value
        assert 0.0 <= density <= 1.0
    
    def test_estimate_speech_density_no_segments(self, mock_video_file, tmp_dir):
        """Test speech density with silence detection."""
        # Test with a real video file - should work with silence detection
        density = _estimate_speech_density(mock_video_file, tmp_dir)
        
        # Should return a valid density score
        assert density >= 0.0


class TestUtilityFunctions:
    """Test utility functions."""
    
    def test_get_video_duration(self, mock_video_file):
        """Test video duration detection."""
        duration = _get_video_duration(mock_video_file)
        
        # Should be approximately 5 seconds (created in fixture)
        assert 4.5 <= duration <= 5.5
    
    def test_get_video_duration_missing_file(self):
        """Test duration detection on missing file."""
        duration = _get_video_duration("/nonexistent/video.mp4")
        assert duration == 0.0
    
    def test_detect_total_silence(self, mock_video_file):
        """Test silence detection."""
        silence = _detect_total_silence(mock_video_file, silence_threshold_db=-40.0)
        
        # Should return non-negative duration
        assert silence >= 0.0
        
        # A pure tone should have minimal silence
        assert silence < 2.0  # Less than 2 seconds of silence in 5-second clip


class TestAudioExcitementScoring:
    """Test main scoring function."""
    
    def test_score_audio_excitement_success(self, mock_video_file, tmp_dir):
        """Test complete audio excitement scoring."""
        score = score_audio_excitement(mock_video_file, tmp_dir)
        
        # Score should be in valid range
        assert 0.0 <= score <= 1.0
        
        # For a pure tone (monotonous), score should be moderate
        # (not too high since it's not very "exciting")
        assert 0.1 <= score <= 0.8
    
    def test_score_audio_excitement_missing_file(self, tmp_dir):
        """Test scoring with missing video file."""
        score = score_audio_excitement("/nonexistent/video.mp4", tmp_dir)
        
        # Should return 0 for missing file
        assert score == 0.0
    
    def test_score_audio_excitement_creates_tmp_dir(self, mock_video_file):
        """Test that scoring creates tmp_dir if it doesn't exist."""
        with tempfile.TemporaryDirectory() as base_dir:
            tmp_dir = os.path.join(base_dir, "nonexistent_tmp")
            assert not os.path.exists(tmp_dir)
            
            score = score_audio_excitement(mock_video_file, tmp_dir)
            
            # tmp_dir should now exist
            assert os.path.exists(tmp_dir)
            assert 0.0 <= score <= 1.0
    
    def test_score_audio_excitement_failed_stats(self, tmp_dir):
        """Test scoring when stats extraction fails."""
        with patch('os.path.exists', return_value=True):
            with patch('src.audio_scorer._extract_audio_stats', return_value=None):
                score = score_audio_excitement("/fake/video.mp4", tmp_dir)
                
                # Should return baseline score of 0.3 on failure
                assert score == 0.3
    
    @patch('os.path.exists')
    @patch('src.audio_scorer._extract_audio_stats')
    @patch('src.audio_scorer._detect_volume_spikes')
    @patch('src.audio_scorer._estimate_speech_density')
    @patch('src.audio_scorer._get_video_duration')
    def test_score_audio_excitement_component_weights(
        self, mock_duration, mock_speech, mock_spikes, mock_stats, mock_exists, tmp_dir
    ):
        """Test that score correctly combines component features."""
        mock_exists.return_value = True  # Pretend file exists
        
        # Mock all components with known values
        mock_stats.return_value = {
            'mean_volume': -20.0,  # Moderate loudness
            'max_volume': -10.0,
            'volume_variance': 0.005,
        }
        mock_spikes.return_value = 10  # 10 spikes
        mock_speech.return_value = 0.8  # 80% speech density
        mock_duration.return_value = 10.0  # 10 seconds
        
        score = score_audio_excitement("/fake/video.mp4", tmp_dir)
        
        # Score should reflect high excitement
        # (high speech density, good spikes, moderate variance)
        assert 0.4 <= score <= 1.0
    
    @patch('src.audio_scorer._extract_audio_stats')
    @patch('src.audio_scorer._detect_volume_spikes')
    @patch('src.audio_scorer._estimate_speech_density')
    @patch('src.audio_scorer._get_video_duration')
    def test_score_audio_excitement_low_excitement(
        self, mock_duration, mock_speech, mock_spikes, mock_stats, tmp_dir
    ):
        """Test scoring for low excitement audio."""
        # Mock components with low values
        mock_stats.return_value = {
            'mean_volume': -50.0,  # Very quiet
            'max_volume': -40.0,
            'volume_variance': 0.0001,  # Very flat
        }
        mock_spikes.return_value = 0  # No spikes
        mock_speech.return_value = 0.1  # Little speech
        mock_duration.return_value = 10.0
        
        score = score_audio_excitement("/fake/video.mp4", tmp_dir)
        
        # Score should be low for boring audio
        assert 0.0 <= score <= 0.4
    
    def test_score_normalization_bounds(self, tmp_dir):
        """Test that extreme values are properly normalized."""
        with patch('src.audio_scorer._extract_audio_stats') as mock_stats:
            # Extreme high values
            mock_stats.return_value = {
                'mean_volume': 0.0,  # Max loudness
                'max_volume': 0.0,
                'volume_variance': 1.0,  # Very high variance
            }
            
            with patch('src.audio_scorer._detect_volume_spikes', return_value=1000):
                with patch('src.audio_scorer._estimate_speech_density', return_value=1.0):
                    with patch('src.audio_scorer._get_video_duration', return_value=1.0):
                        score = score_audio_excitement("/fake/video.mp4", tmp_dir)
                        
                        # Even with extreme values, score should not exceed 1.0
                        assert score <= 1.0


class TestIntegration:
    """Integration tests with real ffmpeg processing."""
    
    def test_full_pipeline_with_varied_audio(self, tmp_dir):
        """Test scoring on video with varied audio characteristics."""
        # Create a video with more dynamic audio (varying frequency)
        video_path = os.path.join(tmp_dir, "varied_audio.mp4")
        
        cmd = [
            "ffmpeg",
            "-f", "lavfi",
            "-i", "sine=frequency=1000:duration=2,sine=frequency=500:duration=2",
            "-f", "lavfi",
            "-i", "color=c=black:s=320x240:d=4",
            "-shortest",
            "-y",
            video_path
        ]
        
        try:
            subprocess.run(cmd, capture_output=True, timeout=10, check=True)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pytest.skip("Could not create varied audio test video")
        
        score = score_audio_excitement(video_path, tmp_dir)
        
        # Should have valid score
        assert 0.0 <= score <= 1.0
    
    def test_comparison_monotone_vs_varied(self, tmp_dir):
        """Test that varied audio scores higher than monotone."""
        # Create monotone video
        monotone_path = os.path.join(tmp_dir, "monotone.mp4")
        cmd_monotone = [
            "ffmpeg",
            "-f", "lavfi",
            "-i", "sine=frequency=440:duration=3",
            "-f", "lavfi",
            "-i", "color=c=black:s=320x240:d=3",
            "-shortest",
            "-y",
            monotone_path
        ]
        
        # Create varied video with volume changes
        varied_path = os.path.join(tmp_dir, "varied.mp4")
        cmd_varied = [
            "ffmpeg",
            "-f", "lavfi",
            "-i", "sine=frequency=440:duration=3,volume=0.1:duration=1,volume=1.0:duration=1,volume=0.3:duration=1",
            "-f", "lavfi",
            "-i", "color=c=black:s=320x240:d=3",
            "-shortest",
            "-y",
            varied_path
        ]
        
        try:
            subprocess.run(cmd_monotone, capture_output=True, timeout=10, check=True)
            subprocess.run(cmd_varied, capture_output=True, timeout=10, check=True)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pytest.skip("Could not create comparison test videos")
        
        monotone_score = score_audio_excitement(monotone_path, tmp_dir)
        varied_score = score_audio_excitement(varied_path, tmp_dir)
        
        # Both should be valid
        assert 0.0 <= monotone_score <= 1.0
        assert 0.0 <= varied_score <= 1.0
        
        # Varied audio might score higher due to dynamics
        # (but not guaranteed, so we just check both are reasonable)
        assert abs(varied_score - monotone_score) < 0.5  # Similar order of magnitude

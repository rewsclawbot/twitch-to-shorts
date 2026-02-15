"""Tests for hook strength detector."""

import os
import subprocess
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from src.hook_detector import (
    _analyze_audio_loudness,
    _analyze_visual_activity,
    _title_quality,
    score_hook_strength,
)


class TestTitleQuality:
    """Test title excitement scoring."""

    def test_empty_title(self):
        assert _title_quality("") == 0.0
        assert _title_quality(None) == 0.0

    def test_perfect_title(self):
        # Has exclamation, caps, good length, digits
        score = _title_quality("INSANE 1V5 CLUTCH!")
        assert score >= 0.75

    def test_partial_title(self):
        # Has some features but not all
        score = _title_quality("Nice play here")
        assert 0.0 < score < 1.0

    def test_long_title_penalty(self):
        # Too long
        score = _title_quality("a" * 100)
        assert score < 1.0


class TestAudioLoudness:
    """Test audio loudness analysis."""

    @patch("src.hook_detector.subprocess.run")
    def test_normal_audio(self, mock_run):
        # Mock ffmpeg output with typical gaming audio
        mock_result = MagicMock()
        mock_result.stderr = """
        [Parsed_astats_0 @ 0x123] Overall RMS level dB: -25.5
        [Parsed_astats_0 @ 0x123] Peak level dB: -12.3
        """
        mock_run.return_value = mock_result

        score = _analyze_audio_loudness("test.mp4", 0.0, 3.0)
        assert 0.0 <= score <= 1.0
        assert score > 0.5  # Should be reasonably high for -25dB

    @patch("src.hook_detector.subprocess.run")
    def test_quiet_audio(self, mock_run):
        # Very quiet audio
        mock_result = MagicMock()
        mock_result.stderr = """
        [Parsed_astats_0 @ 0x123] Overall RMS level dB: -55.0
        [Parsed_astats_0 @ 0x123] Peak level dB: -45.0
        """
        mock_run.return_value = mock_result

        score = _analyze_audio_loudness("test.mp4", 0.0, 3.0)
        assert 0.0 <= score <= 1.0
        assert score < 0.3  # Should be low for quiet audio

    @patch("src.hook_detector.subprocess.run")
    def test_loud_audio(self, mock_run):
        # Very loud audio
        mock_result = MagicMock()
        mock_result.stderr = """
        [Parsed_astats_0 @ 0x123] Overall RMS level dB: -10.0
        [Parsed_astats_0 @ 0x123] Peak level dB: -3.0
        """
        mock_run.return_value = mock_result

        score = _analyze_audio_loudness("test.mp4", 0.0, 3.0)
        assert 0.0 <= score <= 1.0
        assert score > 0.8  # Should be very high for loud audio

    @patch("src.hook_detector.subprocess.run")
    def test_audio_analysis_failure(self, mock_run):
        # Simulate failure
        mock_run.side_effect = subprocess.TimeoutExpired("ffmpeg", 30)

        score = _analyze_audio_loudness("test.mp4", 0.0, 3.0)
        assert score == 0.0


class TestVisualActivity:
    """Test visual activity analysis."""

    @patch("src.hook_detector._batch_sample_ydif")
    def test_high_hook_activity(self, mock_ydif):
        # Hook has lots of action, rest is calm
        hook_scores = [15.0, 18.0, 20.0, 16.0, 17.0, 19.0]  # 6 samples @ 0.5s = 3s
        rest_scores = [5.0, 6.0, 4.0, 5.5, 6.5, 5.0, 4.5, 5.5, 6.0, 5.5, 5.0, 4.5]
        mock_ydif.return_value = hook_scores + rest_scores

        score = _analyze_visual_activity("test.mp4", duration=10.0, hook_window=3.0)
        assert 0.0 <= score <= 1.0
        assert score > 0.7  # High activity in hook

    @patch("src.hook_detector._batch_sample_ydif")
    def test_low_hook_activity(self, mock_ydif):
        # Hook is static, rest has action
        hook_scores = [2.0, 1.5, 2.5, 2.0, 1.8, 2.2]
        rest_scores = [15.0, 18.0, 16.0, 17.0, 19.0, 18.5, 17.5, 16.5, 18.0, 17.0, 16.0, 15.5]
        mock_ydif.return_value = hook_scores + rest_scores

        score = _analyze_visual_activity("test.mp4", duration=10.0, hook_window=3.0)
        assert 0.0 <= score <= 1.0
        assert score < 0.3  # Low activity in hook

    @patch("src.hook_detector._batch_sample_ydif")
    def test_short_clip(self, mock_ydif):
        # Clip shorter than hook window
        score = _analyze_visual_activity("test.mp4", duration=2.0, hook_window=3.0)
        assert score == 0.5  # Neutral score for short clips

    @patch("src.hook_detector._batch_sample_ydif")
    def test_visual_analysis_failure(self, mock_ydif):
        # Simulate failure
        mock_ydif.side_effect = Exception("YDIF failed")

        score = _analyze_visual_activity("test.mp4", duration=10.0, hook_window=3.0)
        assert score == 0.5  # Fallback to neutral


class TestHookStrength:
    """Test complete hook strength scoring."""

    @patch("src.hook_detector._analyze_visual_activity")
    @patch("src.hook_detector._analyze_audio_loudness")
    @patch("src.hook_detector._title_quality")
    def test_strong_hook(self, mock_title, mock_audio, mock_visual):
        # All signals strong
        mock_visual.return_value = 0.9
        mock_audio.return_value = 0.8
        mock_title.return_value = 0.85

        score = score_hook_strength("test.mp4", "INSANE PLAY!", 15.0)
        # 0.5*0.9 + 0.3*0.8 + 0.2*0.85 = 0.45 + 0.24 + 0.17 = 0.86
        assert 0.85 <= score <= 0.87

    @patch("src.hook_detector._analyze_visual_activity")
    @patch("src.hook_detector._analyze_audio_loudness")
    @patch("src.hook_detector._title_quality")
    def test_weak_hook(self, mock_title, mock_audio, mock_visual):
        # All signals weak
        mock_visual.return_value = 0.2
        mock_audio.return_value = 0.15
        mock_title.return_value = 0.1

        score = score_hook_strength("test.mp4", "boring", 15.0)
        # 0.5*0.2 + 0.3*0.15 + 0.2*0.1 = 0.1 + 0.045 + 0.02 = 0.165
        assert 0.16 <= score <= 0.17

    @patch("src.hook_detector._analyze_visual_activity")
    @patch("src.hook_detector._analyze_audio_loudness")
    @patch("src.hook_detector._title_quality")
    def test_mixed_hook(self, mock_title, mock_audio, mock_visual):
        # Mixed signals
        mock_visual.return_value = 0.8  # Strong visual
        mock_audio.return_value = 0.3   # Weak audio
        mock_title.return_value = 0.6   # Medium title

        score = score_hook_strength("test.mp4", "Nice moment", 15.0)
        # 0.5*0.8 + 0.3*0.3 + 0.2*0.6 = 0.4 + 0.09 + 0.12 = 0.61
        assert 0.60 <= score <= 0.62

    @patch("src.hook_detector._analyze_visual_activity")
    @patch("src.hook_detector._analyze_audio_loudness")
    @patch("src.hook_detector._title_quality")
    def test_hook_failure_handling(self, mock_title, mock_audio, mock_visual):
        # Simulate exception
        mock_visual.side_effect = Exception("Failed")

        score = score_hook_strength("test.mp4", "title", 15.0)
        assert score == 0.0  # Returns 0 on failure

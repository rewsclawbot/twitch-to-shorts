"""Tests for the hook editor module."""
import os
from unittest.mock import MagicMock, patch

import pytest

from src.hook_editor import (
    DEFAULT_RECUT_THRESHOLD,
    MIN_OUTPUT_DURATION,
    recut_for_hook,
)


class TestRecutForHook:
    """Tests for the recut_for_hook function."""

    def test_returns_none_when_hook_score_above_threshold(self):
        """No recut needed when hook is strong enough."""
        result = recut_for_hook("/fake/video.mp4", "/tmp", hook_score=0.5)
        assert result is None

    def test_returns_none_when_hook_at_threshold(self):
        """No recut at exactly the threshold."""
        result = recut_for_hook("/fake/video.mp4", "/tmp", hook_score=DEFAULT_RECUT_THRESHOLD)
        assert result is None

    @patch("src.hook_editor._get_duration", return_value=5.0)
    def test_returns_none_when_clip_too_short(self, _mock_dur):
        """Clips shorter than MIN_OUTPUT_DURATION can't be recut."""
        result = recut_for_hook("/fake/video.mp4", "/tmp", hook_score=0.2)
        assert result is None

    @patch("src.hook_editor.find_peak_action_timestamp", return_value=0.5)
    @patch("src.hook_editor._get_duration", return_value=30.0)
    def test_returns_none_when_peak_already_near_start(self, _mock_dur, _mock_peak):
        """If peak action is within first second, no recut needed."""
        result = recut_for_hook("/fake/video.mp4", "/tmp", hook_score=0.2)
        assert result is None

    @patch("src.hook_editor.find_peak_action_timestamp", return_value=0.0)
    @patch("src.hook_editor._get_duration", return_value=30.0)
    def test_returns_none_when_no_peak_found(self, _mock_dur, _mock_peak):
        """No peak action means no recut possible."""
        result = recut_for_hook("/fake/video.mp4", "/tmp", hook_score=0.2)
        assert result is None

    @patch("src.hook_editor.find_peak_action_timestamp", return_value=25.0)
    @patch("src.hook_editor._get_duration", return_value=30.0)
    def test_returns_none_when_remaining_too_short(self, _mock_dur, _mock_peak):
        """If cutting to peak leaves less than MIN_OUTPUT_DURATION, skip."""
        result = recut_for_hook("/fake/video.mp4", "/tmp", hook_score=0.2)
        assert result is None

    @patch("src.hook_editor.is_valid_video", return_value=True)
    @patch("src.hook_editor.subprocess.run")
    @patch("src.hook_editor.find_peak_action_timestamp", return_value=10.0)
    @patch("src.hook_editor._get_duration", return_value=30.0)
    def test_successful_recut(self, _mock_dur, _mock_peak, mock_ffmpeg, _mock_valid, tmp_path):
        """Successful recut returns path to new video."""
        mock_ffmpeg.return_value = MagicMock(returncode=0)
        result = recut_for_hook("/fake/video.mp4", str(tmp_path), hook_score=0.2)
        assert result is not None
        assert result.endswith("_recut.mp4")
        mock_ffmpeg.assert_called_once()
        # Check that ffmpeg -ss is ~8.0 (10.0 - 2.0 lead-in)
        cmd = mock_ffmpeg.call_args[0][0]
        ss_idx = cmd.index("-ss")
        assert float(cmd[ss_idx + 1]) == pytest.approx(8.0, abs=0.1)

    @patch("src.hook_editor.subprocess.run")
    @patch("src.hook_editor.find_peak_action_timestamp", return_value=10.0)
    @patch("src.hook_editor._get_duration", return_value=30.0)
    def test_returns_none_on_ffmpeg_failure(self, _mock_dur, _mock_peak, mock_ffmpeg, tmp_path):
        """Failed ffmpeg returns None."""
        mock_ffmpeg.return_value = MagicMock(returncode=1, stderr="error")
        result = recut_for_hook("/fake/video.mp4", str(tmp_path), hook_score=0.2)
        assert result is None

    def test_custom_threshold(self):
        """Custom threshold is respected."""
        # Score 0.3 is above custom threshold of 0.2
        result = recut_for_hook("/fake/video.mp4", "/tmp", hook_score=0.3, recut_threshold=0.2)
        assert result is None

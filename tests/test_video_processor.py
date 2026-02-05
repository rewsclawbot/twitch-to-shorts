"""Tests for video_processor: filter building, silence detection, probe, GPU/CPU fallback."""

import json
from unittest.mock import patch, MagicMock

import pytest

from src.models import FacecamConfig
from src.video_processor import (
    _build_composite_filter,
    _detect_leading_silence,
    _probe_video_info,
)


class TestBuildCompositeFilter:
    def test_default_facecam_config(self):
        fc = FacecamConfig()  # x=0.0, y=0.75, w=0.25, h=0.25, output_w=420
        result = _build_composite_filter(fc)
        # Should contain game crop, cam crop, and overlay
        assert "[game]" in result
        assert "[cam]" in result
        assert "overlay" in result
        assert "[out]" in result

    def test_custom_facecam_values(self):
        fc = FacecamConfig(x=0.1, y=0.6, w=0.3, h=0.4, output_w=500)
        result = _build_composite_filter(fc)
        assert "crop=iw*0.3:ih*0.4:iw*0.1:ih*0.6" in result
        assert "scale=500:-2" in result

    def test_odd_output_width_made_even(self):
        fc = FacecamConfig(output_w=421)  # Odd number
        result = _build_composite_filter(fc)
        # 421 + (421 % 2) = 421 + 1 = 422
        assert "scale=422:-2" in result

    def test_even_output_width_unchanged(self):
        fc = FacecamConfig(output_w=420)
        result = _build_composite_filter(fc)
        assert "scale=420:-2" in result

    def test_gameplay_center_crop(self):
        fc = FacecamConfig()
        result = _build_composite_filter(fc)
        assert "crop=ih*9/16:ih:(iw-ih*9/16)/2:0" in result
        assert "scale=1080:1920" in result

    def test_overlay_centered_at_top(self):
        fc = FacecamConfig()
        result = _build_composite_filter(fc)
        assert "overlay=(W-w)/2:0" in result


class TestDetectLeadingSilence:
    @patch("src.video_processor.subprocess.run")
    def test_silence_at_start(self, mock_run):
        mock_run.return_value = MagicMock(
            stderr=(
                "[silencedetect @ 0x1234] silence_start: 0\n"
                "[silencedetect @ 0x1234] silence_end: 2.5 | silence_duration: 2.5\n"
            ),
            returncode=0,
        )
        result = _detect_leading_silence("test.mp4")
        assert result == 2.5

    @patch("src.video_processor.subprocess.run")
    def test_no_silence(self, mock_run):
        mock_run.return_value = MagicMock(stderr="", returncode=0)
        result = _detect_leading_silence("test.mp4")
        assert result == 0.0

    @patch("src.video_processor.subprocess.run")
    def test_silence_not_at_start(self, mock_run):
        mock_run.return_value = MagicMock(
            stderr=(
                "[silencedetect @ 0x1234] silence_start: 5.0\n"
                "[silencedetect @ 0x1234] silence_end: 7.0\n"
            ),
            returncode=0,
        )
        result = _detect_leading_silence("test.mp4")
        assert result == 0.0

    @patch("src.video_processor.subprocess.run")
    def test_silence_capped_at_5s(self, mock_run):
        mock_run.return_value = MagicMock(
            stderr=(
                "[silencedetect @ 0x1234] silence_start: 0\n"
                "[silencedetect @ 0x1234] silence_end: 10.0\n"
            ),
            returncode=0,
        )
        result = _detect_leading_silence("test.mp4")
        assert result == 5.0

    @patch("src.video_processor.subprocess.run")
    def test_exception_returns_zero(self, mock_run):
        mock_run.side_effect = Exception("ffmpeg not found")
        result = _detect_leading_silence("test.mp4")
        assert result == 0.0

    @patch("src.video_processor.subprocess.run")
    def test_silence_start_near_zero(self, mock_run):
        mock_run.return_value = MagicMock(
            stderr=(
                "[silencedetect @ 0x1234] silence_start: 0.005\n"
                "[silencedetect @ 0x1234] silence_end: 1.2\n"
            ),
            returncode=0,
        )
        # 0.005 <= 0.01, so treated as start
        result = _detect_leading_silence("test.mp4")
        assert result == 1.2


class TestProbeVideoInfo:
    @patch("src.video_processor.subprocess.run")
    def test_full_probe_result(self, mock_run):
        probe_output = {
            "format": {"duration": "45.5"},
            "streams": [{"width": 1920, "height": 1080, "duration": "45.5"}],
        }
        mock_run.return_value = MagicMock(
            stdout=json.dumps(probe_output),
            returncode=0,
        )
        duration, dims = _probe_video_info("test.mp4")
        assert duration == 45.5
        assert dims == (1920, 1080)

    @patch("src.video_processor.subprocess.run")
    def test_duration_from_stream_fallback(self, mock_run):
        probe_output = {
            "format": {},
            "streams": [{"width": 1280, "height": 720, "duration": "30.0"}],
        }
        mock_run.return_value = MagicMock(
            stdout=json.dumps(probe_output),
            returncode=0,
        )
        duration, dims = _probe_video_info("test.mp4")
        assert duration == 30.0
        assert dims == (1280, 720)

    @patch("src.video_processor.subprocess.run")
    def test_missing_streams(self, mock_run):
        probe_output = {"format": {"duration": "10.0"}, "streams": []}
        mock_run.return_value = MagicMock(
            stdout=json.dumps(probe_output),
            returncode=0,
        )
        duration, dims = _probe_video_info("test.mp4")
        assert duration == 10.0
        assert dims is None

    @patch("src.video_processor.subprocess.run")
    def test_exception_returns_none_none(self, mock_run):
        mock_run.side_effect = Exception("ffprobe crash")
        duration, dims = _probe_video_info("test.mp4")
        assert duration is None
        assert dims is None

    @patch("src.video_processor.subprocess.run")
    def test_invalid_json_returns_none_none(self, mock_run):
        mock_run.return_value = MagicMock(stdout="not json", returncode=0)
        duration, dims = _probe_video_info("test.mp4")
        assert duration is None
        assert dims is None


class TestGpuCpuFallback:
    """Verify GPU encode is attempted first (when not disabled), then CPU fallback."""

    @patch("src.video_processor.os.environ", {"DISABLE_GPU_ENCODE": "1"})
    @patch("src.video_processor._run_ffmpeg")
    @patch("src.video_processor._measure_loudness", return_value=None)
    @patch("src.video_processor._detect_leading_silence", return_value=0.0)
    @patch("src.video_processor._probe_video_info", return_value=(30.0, (1920, 1080)))
    @patch("src.video_processor.os.path.exists", return_value=False)
    def test_gpu_disabled_skips_gpu(self, mock_exists, mock_probe, mock_silence,
                                     mock_loudness, mock_ffmpeg):
        from src.video_processor import crop_to_vertical
        mock_ffmpeg.return_value = True

        crop_to_vertical("test.mp4", "/tmp/test", facecam_mode="off")

        # Should only call _run_ffmpeg once with gpu=False
        assert mock_ffmpeg.call_count == 1
        _, kwargs = mock_ffmpeg.call_args
        assert kwargs.get("gpu") is False or mock_ffmpeg.call_args[0][4] is False

    @patch.dict("os.environ", {"DISABLE_GPU_ENCODE": ""})
    @patch("src.video_processor._run_ffmpeg")
    @patch("src.video_processor._measure_loudness", return_value=None)
    @patch("src.video_processor._detect_leading_silence", return_value=0.0)
    @patch("src.video_processor._probe_video_info", return_value=(30.0, (1920, 1080)))
    @patch("src.video_processor.os.path.exists", return_value=False)
    def test_gpu_fails_falls_back_to_cpu(self, mock_exists, mock_probe, mock_silence,
                                          mock_loudness, mock_ffmpeg):
        from src.video_processor import crop_to_vertical
        # GPU fails, CPU succeeds
        mock_ffmpeg.side_effect = [False, True]

        result = crop_to_vertical("test.mp4", "/tmp/test", facecam_mode="off")

        assert mock_ffmpeg.call_count == 2
        # First call: gpu=True, second call: gpu=False
        first_call = mock_ffmpeg.call_args_list[0]
        second_call = mock_ffmpeg.call_args_list[1]
        assert first_call[1].get("gpu") is True or first_call[0][4] is True
        assert second_call[1].get("gpu") is False or second_call[0][4] is False

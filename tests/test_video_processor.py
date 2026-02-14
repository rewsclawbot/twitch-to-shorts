"""Tests for video_processor: filter building, silence detection, probe, GPU/CPU fallback."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.models import FacecamConfig
from src.video_processor import (
    _batch_sample_ydif,
    _build_composite_filter,
    _escape_subtitle_path,
    _probe_video_info,
    _run_ffmpeg,
    burn_context_overlay,
    check_loop_compatibility,
    crop_to_vertical,
    detect_leading_silence,
    detect_visual_dead_frames,
    extract_thumbnail,
    find_peak_action_timestamp,
    trim_to_optimal_length,
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
        result = detect_leading_silence("test.mp4")
        assert result == 2.5

    @patch("src.video_processor.subprocess.run")
    def test_no_silence(self, mock_run):
        mock_run.return_value = MagicMock(stderr="", returncode=0)
        result = detect_leading_silence("test.mp4")
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
        result = detect_leading_silence("test.mp4")
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
        result = detect_leading_silence("test.mp4")
        assert result == 5.0

    @patch("src.video_processor.subprocess.run")
    def test_exception_returns_zero(self, mock_run):
        mock_run.side_effect = Exception("ffmpeg not found")
        result = detect_leading_silence("test.mp4")
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
        result = detect_leading_silence("test.mp4")
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
    @patch("src.video_processor.detect_leading_silence", return_value=0.0)
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
    @patch("src.video_processor.detect_leading_silence", return_value=0.0)
    @patch("src.video_processor._probe_video_info", return_value=(30.0, (1920, 1080)))
    @patch("src.video_processor.os.path.exists", return_value=False)
    def test_gpu_fails_falls_back_to_cpu(self, mock_exists, mock_probe, mock_silence,
                                          mock_loudness, mock_ffmpeg):
        from src.video_processor import crop_to_vertical
        # GPU fails, CPU succeeds
        mock_ffmpeg.side_effect = [False, True]

        crop_to_vertical("test.mp4", "/tmp/test", facecam_mode="off")

        assert mock_ffmpeg.call_count == 2
        # First call: gpu=True, second call: gpu=False
        first_call = mock_ffmpeg.call_args_list[0]
        second_call = mock_ffmpeg.call_args_list[1]
        assert first_call[1].get("gpu") is True or first_call[0][4] is True
        assert second_call[1].get("gpu") is False or second_call[0][4] is False


class TestSilenceDetectionTimeLimit:
    """Verify that -t 6 appears in silence detection ffmpeg args."""

    @patch("src.video_processor.subprocess.run")
    def test_t_flag_in_silence_detection_args(self, mock_run):
        mock_run.return_value = MagicMock(stderr="", returncode=0)
        detect_leading_silence("test.mp4")
        args = mock_run.call_args[0][0]
        # -t and 6 should appear before -i
        assert "-t" in args
        assert "6" in args
        t_idx = args.index("-t")
        i_idx = args.index("-i")
        assert args[t_idx + 1] == "6"
        assert t_idx < i_idx


class TestBatchSampleYdif:
    """Verify batch YDIF uses a single ffmpeg call instead of N sequential ones."""

    @patch("src.video_processor.subprocess.run")
    def test_single_ffmpeg_call_for_multiple_timestamps(self, mock_run):
        stderr_lines = "\n".join(
            f"[Parsed_signalstats] YDIF signalstats.YDIF={1.5 + i}"
            for i in range(4)
        )
        mock_run.return_value = MagicMock(stderr=stderr_lines, returncode=0)

        timestamps = [5.0, 10.0, 15.0, 20.0]
        scores = _batch_sample_ydif("test.mp4", timestamps)

        # Only 1 subprocess call, not 4
        assert mock_run.call_count == 1
        assert len(scores) == 4
        assert scores[0] == pytest.approx(1.5)
        assert scores[3] == pytest.approx(4.5)

    @patch("src.video_processor.subprocess.run")
    def test_batch_uses_filter_complex(self, mock_run):
        mock_run.return_value = MagicMock(stderr="", returncode=0)
        _batch_sample_ydif("test.mp4", [1.0, 2.0, 3.0])
        args = mock_run.call_args[0][0]
        assert "-filter_complex" in args

    @patch("src.video_processor.subprocess.run")
    def test_batch_empty_timestamps(self, mock_run):
        result = _batch_sample_ydif("test.mp4", [])
        assert result == []
        mock_run.assert_not_called()

    @patch("src.video_processor.subprocess.run")
    def test_batch_failure_returns_zeros(self, mock_run):
        mock_run.side_effect = Exception("ffmpeg not found")
        scores = _batch_sample_ydif("test.mp4", [1.0, 2.0])
        assert scores == [0.0, 0.0]


class TestExtractThumbnailDurationParam:
    """Verify that passing duration to extract_thumbnail skips the ffprobe call."""

    @patch("src.video_processor.subprocess.run")
    @patch("src.video_processor.os.path.getsize", return_value=1024)
    @patch("src.video_processor.os.path.exists", return_value=True)
    @patch("src.video_processor._batch_sample_ydif", return_value=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    @patch("src.video_processor._get_duration")
    def test_duration_param_skips_ffprobe(self, mock_get_duration, mock_batch, mock_exists,
                                          mock_getsize, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        extract_thumbnail("test.mp4", "/tmp/thumbs", duration=30.0)
        # _get_duration should NOT be called when duration is passed
        mock_get_duration.assert_not_called()

    @patch("src.video_processor.subprocess.run")
    @patch("src.video_processor.os.path.getsize", return_value=1024)
    @patch("src.video_processor.os.path.exists", return_value=True)
    @patch("src.video_processor._batch_sample_ydif", return_value=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    @patch("src.video_processor._get_duration", return_value=30.0)
    def test_no_duration_param_calls_ffprobe(self, mock_get_duration, mock_batch, mock_exists,
                                              mock_getsize, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        extract_thumbnail("test.mp4", "/tmp/thumbs")
        # _get_duration SHOULD be called when duration is not passed
        mock_get_duration.assert_called_once()

    @patch("src.video_processor._batch_sample_ydif")
    @patch("src.video_processor._get_duration")
    def test_duration_zero_returns_none(self, mock_get_duration, mock_batch):
        result = extract_thumbnail("test.mp4", "/tmp/thumbs", duration=0)
        assert result is None
        mock_batch.assert_not_called()


class TestBurnContextOverlay:
    @patch("src.video_processor._find_context_fontfile", return_value=None)
    @patch("src.video_processor.os.replace")
    @patch("src.video_processor.os.path.getsize", return_value=2048)
    @patch("src.video_processor.os.path.exists")
    @patch("src.video_processor.subprocess.run")
    def test_overlay_includes_game_bar_and_keywords(
        self, mock_run, mock_exists, mock_getsize, mock_replace, mock_font
    ):
        mock_exists.side_effect = [True, True]
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")

        result = burn_context_overlay(
            "in.mp4",
            "out.mp4",
            "Valorant",
            "Insane 1v5 clutch to save the round",
        )

        assert result is True
        cmd = mock_run.call_args[0][0]
        assert isinstance(cmd, list)
        assert "-vf" in cmd
        vf = cmd[cmd.index("-vf") + 1]
        assert "drawbox=" in vf
        assert "VALORANT" in vf
        assert "1V5" in vf
        assert "enable='lt(t,2)'" in vf

    @patch("src.video_processor._find_context_fontfile", return_value=None)
    @patch("src.video_processor.os.replace")
    @patch("src.video_processor.os.path.getsize", return_value=2048)
    @patch("src.video_processor.os.path.exists")
    @patch("src.video_processor.subprocess.run")
    def test_overlay_omits_center_text_without_keywords(
        self, mock_run, mock_exists, mock_getsize, mock_replace, mock_font
    ):
        mock_exists.side_effect = [True, True]
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")

        result = burn_context_overlay(
            "in.mp4",
            "out.mp4",
            "Apex Legends",
            "clean rotation and final ring win",
        )

        assert result is True
        cmd = mock_run.call_args[0][0]
        vf = cmd[cmd.index("-vf") + 1]
        assert "APEX LEGENDS" in vf
        assert "enable='lt(t,2)'" not in vf

    @patch("src.video_processor.subprocess.run")
    @patch("src.video_processor.os.path.exists", return_value=False)
    def test_missing_input_returns_false(self, mock_exists, mock_run):
        result = burn_context_overlay("missing.mp4", "out.mp4", "Valorant", "CLUTCH")
        assert result is False
        mock_run.assert_not_called()

    @patch("src.video_processor.safe_remove")
    @patch("src.video_processor.subprocess.run", side_effect=Exception("ffmpeg error"))
    @patch("src.video_processor.os.path.exists", return_value=True)
    def test_failure_cleans_tmp_output(self, mock_exists, mock_run, mock_remove):
        result = burn_context_overlay("in.mp4", "out.mp4", "Valorant", "INSANE")
        assert result is False
        mock_remove.assert_called_once_with("out.mp4.ctx.tmp.mp4")


class TestSubtitleIntegration:
    """Verify subtitle filter injection in _run_ffmpeg and path escaping."""

    def test_escape_subtitle_path_windows(self):
        assert _escape_subtitle_path("C:\\Users\\test\\file.ass") == "C\\:/Users/test/file.ass"

    def test_escape_subtitle_path_unix(self):
        assert _escape_subtitle_path("/tmp/test/file.ass") == "/tmp/test/file.ass"

    def test_escape_subtitle_path_colon_only(self):
        assert _escape_subtitle_path("C:/Users/test.ass") == "C\\:/Users/test.ass"

    @patch("src.video_processor.os.replace")
    @patch("src.video_processor.os.path.getsize", return_value=1024)
    @patch("src.video_processor.os.path.exists", return_value=True)
    @patch("src.video_processor.subprocess.Popen")
    def test_composite_mode_subtitle_injection(self, mock_popen, mock_exists,
                                                mock_getsize, mock_replace):
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        vf = "[0:v]crop=ih*9/16:ih,scale=1080:1920[game];[0:v]crop=iw*0.25:ih*0.25,scale=420:-2[cam];[game][cam]overlay=(W-w)/2:0[out]"
        _run_ffmpeg("in.mp4", "out.mp4", vf, "test", gpu=False,
                    subtitle_path="/tmp/captions.ass")

        cmd = mock_popen.call_args[0][0]
        fc_idx = cmd.index("-filter_complex")
        fc_value = cmd[fc_idx + 1]
        # [out] should be replaced with [tmp] and ASS subtitle filter appended
        assert "[tmp]" in fc_value
        assert "ass=" in fc_value
        assert fc_value.endswith("[out]")

    @patch("src.video_processor.os.replace")
    @patch("src.video_processor.os.path.getsize", return_value=1024)
    @patch("src.video_processor.os.path.exists", return_value=True)
    @patch("src.video_processor.subprocess.Popen")
    def test_simple_mode_subtitle_injection(self, mock_popen, mock_exists,
                                             mock_getsize, mock_replace):
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        vf = "crop=ih*9/16:ih,scale=1080:1920"
        _run_ffmpeg("in.mp4", "out.mp4", vf, "test", gpu=False,
                    subtitle_path="/tmp/captions.ass")

        cmd = mock_popen.call_args[0][0]
        vf_idx = cmd.index("-vf")
        vf_value = cmd[vf_idx + 1]
        assert "ass=" in vf_value
        assert vf_value.startswith("crop=ih*9/16:ih,scale=1080:1920,ass=")

    @patch("src.video_processor.os.replace")
    @patch("src.video_processor.os.path.getsize", return_value=1024)
    @patch("src.video_processor.os.path.exists", return_value=True)
    @patch("src.video_processor.subprocess.Popen")
    def test_no_subtitle_path_unchanged(self, mock_popen, mock_exists,
                                         mock_getsize, mock_replace):
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        vf = "crop=ih*9/16:ih,scale=1080:1920"
        _run_ffmpeg("in.mp4", "out.mp4", vf, "test", gpu=False)

        cmd = mock_popen.call_args[0][0]
        vf_idx = cmd.index("-vf")
        vf_value = cmd[vf_idx + 1]
        assert vf_value == "crop=ih*9/16:ih,scale=1080:1920"
        assert "ass=" not in vf_value

    @patch("src.video_processor._run_ffmpeg")
    @patch("src.video_processor._measure_loudness", return_value=None)
    @patch("src.video_processor.detect_leading_silence", return_value=0.0)
    @patch("src.video_processor._probe_video_info", return_value=(30.0, (1920, 1080)))
    @patch("src.video_processor.os.path.exists", return_value=False)
    def test_crop_to_vertical_passes_subtitle_path(self, mock_exists, mock_probe,
                                                     mock_silence, mock_loudness,
                                                     mock_ffmpeg):
        mock_ffmpeg.return_value = True

        with patch.dict("os.environ", {"DISABLE_GPU_ENCODE": "1"}):
            crop_to_vertical("test.mp4", "/tmp/test", facecam_mode="off",
                           subtitle_path="/tmp/test.ass")

        _, kwargs = mock_ffmpeg.call_args
        assert kwargs.get("subtitle_path") == "/tmp/test.ass"


class TestPresetFast:
    """Verify CPU preset is always 'fast'."""

    @patch("src.video_processor.os.replace")
    @patch("src.video_processor.os.path.getsize", return_value=1024)
    @patch("src.video_processor.os.path.exists", return_value=True)
    @patch("src.video_processor.subprocess.Popen")
    def test_cpu_preset_is_fast(self, mock_popen, mock_exists,
                                 mock_getsize, mock_replace):
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        _run_ffmpeg("in.mp4", "out.mp4", "scale=1080:1920", "test", gpu=False)

        cmd = mock_popen.call_args[0][0]
        preset_idx = cmd.index("-preset")
        assert cmd[preset_idx + 1] == "fast"


class TestGpuPlatformEncoding:
    """Verify GPU backend switches by platform."""

    @patch("src.video_processor.sys.platform", "darwin")
    @patch("src.video_processor.os.replace")
    @patch("src.video_processor.os.path.getsize", return_value=1024)
    @patch("src.video_processor.os.path.exists", return_value=True)
    @patch("src.video_processor.subprocess.Popen")
    def test_gpu_uses_videotoolbox_on_macos(self, mock_popen, mock_exists,
                                            mock_getsize, mock_replace):
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        _run_ffmpeg("in.mp4", "out.mp4", "scale=1080:1920", "test", gpu=True)

        cmd = mock_popen.call_args[0][0]
        assert "-hwaccel" not in cmd
        vcodec_idx = cmd.index("-c:v")
        assert cmd[vcodec_idx + 1] == "h264_videotoolbox"
        assert "-b:v" in cmd
        assert "-maxrate" in cmd
        assert "-bufsize" in cmd

    @patch("src.video_processor.sys.platform", "linux")
    @patch("src.video_processor.os.replace")
    @patch("src.video_processor.os.path.getsize", return_value=1024)
    @patch("src.video_processor.os.path.exists", return_value=True)
    @patch("src.video_processor.subprocess.Popen")
    def test_gpu_uses_nvenc_on_linux(self, mock_popen, mock_exists,
                                     mock_getsize, mock_replace):
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        _run_ffmpeg("in.mp4", "out.mp4", "scale=1080:1920", "test", gpu=True)

        cmd = mock_popen.call_args[0][0]
        hwaccel_idx = cmd.index("-hwaccel")
        assert cmd[hwaccel_idx + 1] == "cuda"
        vcodec_idx = cmd.index("-c:v")
        assert cmd[vcodec_idx + 1] == "h264_nvenc"


class TestLoudnessValidation:
    @patch("src.video_processor.os.replace")
    @patch("src.video_processor.os.path.getsize", return_value=1024)
    @patch("src.video_processor.os.path.exists", return_value=True)
    @patch("src.video_processor.subprocess.Popen")
    def test_invalid_loudness_nan_falls_back_to_single_pass(self, mock_popen, mock_exists,
                                                             mock_getsize, mock_replace):
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        loudness = {
            "input_i": "nan",
            "input_tp": -1.0,
            "input_lra": 3.0,
            "input_thresh": -24.0,
            "target_offset": 0.1,
        }
        _run_ffmpeg("in.mp4", "out.mp4", "scale=1080:1920", "test", gpu=False, loudness=loudness)

        cmd = mock_popen.call_args[0][0]
        af_idx = cmd.index("-af")
        af_value = cmd[af_idx + 1]
        assert af_value == "loudnorm=I=-14:TP=-1.5:LRA=11"

    @patch("src.video_processor.os.replace")
    @patch("src.video_processor.os.path.getsize", return_value=1024)
    @patch("src.video_processor.os.path.exists", return_value=True)
    @patch("src.video_processor.subprocess.Popen")
    def test_invalid_loudness_missing_key_falls_back_to_single_pass(self, mock_popen, mock_exists,
                                                                     mock_getsize, mock_replace):
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        loudness = {
            "input_i": -18.2,
            "input_tp": -1.0,
            "input_lra": 3.0,
            "input_thresh": -24.0,
            # target_offset missing
        }
        _run_ffmpeg("in.mp4", "out.mp4", "scale=1080:1920", "test", gpu=False, loudness=loudness)

        cmd = mock_popen.call_args[0][0]
        af_idx = cmd.index("-af")
        af_value = cmd[af_idx + 1]
        assert af_value == "loudnorm=I=-14:TP=-1.5:LRA=11"

    @patch("src.video_processor.os.replace")
    @patch("src.video_processor.os.path.getsize", return_value=1024)
    @patch("src.video_processor.os.path.exists", return_value=True)
    @patch("src.video_processor.subprocess.Popen")
    def test_valid_loudness_uses_two_pass(self, mock_popen, mock_exists,
                                          mock_getsize, mock_replace):
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        loudness = {
            "input_i": -18.2,
            "input_tp": -1.0,
            "input_lra": 3.0,
            "input_thresh": -24.0,
            "target_offset": 0.1,
        }
        _run_ffmpeg("in.mp4", "out.mp4", "scale=1080:1920", "test", gpu=False, loudness=loudness)

        cmd = mock_popen.call_args[0][0]
        af_idx = cmd.index("-af")
        af_value = cmd[af_idx + 1]
        assert "measured_I=-18.2" in af_value
        assert "linear=true" in af_value


class TestDetectVisualDeadFrames:
    """Tests for visual dead frame detection (static/loading screens)."""

    @patch("src.video_processor._batch_sample_ydif")
    def test_no_dead_frames_motion_detected_immediately(self, mock_batch):
        """First sample has motion (YDIF >= 0.5), returns 0.0 trim."""
        mock_batch.return_value = [1.5, 2.0, 3.0, 2.5]  # All have motion
        result = detect_visual_dead_frames("test.mp4", start_offset=0.0)
        assert result == 0.0

    @patch("src.video_processor._batch_sample_ydif")
    def test_dead_frames_detected(self, mock_batch):
        """First 3 samples static (YDIF < 0.5), motion at 4th sample (1.5s)."""
        mock_batch.return_value = [0.2, 0.3, 0.1, 1.5, 2.0, 3.0]
        result = detect_visual_dead_frames("test.mp4", start_offset=0.0)
        # 3 samples * 0.5s = 1.5s trim
        assert result == 1.5

    @patch("src.video_processor._batch_sample_ydif")
    def test_all_frames_static_returns_max_trim(self, mock_batch):
        """All samples are static, returns max_trim (3.0s default)."""
        mock_batch.return_value = [0.1, 0.2, 0.15, 0.25, 0.3, 0.4]  # All < 0.5
        result = detect_visual_dead_frames("test.mp4", start_offset=0.0)
        assert result == 3.0

    @patch("src.video_processor._batch_sample_ydif")
    def test_custom_max_trim(self, mock_batch):
        """Custom max_trim is respected when all frames are static."""
        mock_batch.return_value = [0.1, 0.2, 0.15]  # All static
        result = detect_visual_dead_frames("test.mp4", start_offset=0.0, max_trim=1.5)
        assert result == 1.5

    @patch("src.video_processor._batch_sample_ydif")
    def test_custom_ydif_threshold(self, mock_batch):
        """Custom YDIF threshold affects what's considered static."""
        mock_batch.return_value = [0.6, 0.7, 1.5]
        # Default threshold=0.5: first two have motion
        result1 = detect_visual_dead_frames("test.mp4", start_offset=0.0, ydif_threshold=0.5)
        assert result1 == 0.0
        # Threshold=1.0: first two are now static
        result2 = detect_visual_dead_frames("test.mp4", start_offset=0.0, ydif_threshold=1.0)
        assert result2 == 1.0

    @patch("src.video_processor._batch_sample_ydif")
    def test_start_offset_applied(self, mock_batch):
        """start_offset is passed to sampling timestamps."""
        mock_batch.return_value = [1.5]
        detect_visual_dead_frames("test.mp4", start_offset=2.0)
        # Verify _batch_sample_ydif was called with timestamps starting at 2.0
        timestamps = mock_batch.call_args[0][1]
        assert timestamps[0] == 2.0

    @patch("src.video_processor._batch_sample_ydif")
    def test_samples_at_half_second_intervals(self, mock_batch):
        """Samples are taken at 0.5s intervals."""
        mock_batch.return_value = [0.1, 0.2, 1.5]
        detect_visual_dead_frames("test.mp4", start_offset=0.0)
        timestamps = mock_batch.call_args[0][1]
        # Should sample at 0.0, 0.5, 1.0, 1.5, 2.0, 2.5 (6 samples for 3s max_trim)
        assert len(timestamps) == 6
        assert timestamps[0] == 0.0
        assert timestamps[1] == 0.5
        assert timestamps[2] == 1.0

    @patch("src.video_processor._batch_sample_ydif")
    def test_exception_returns_zero(self, mock_batch):
        """If _batch_sample_ydif raises exception, returns 0.0."""
        mock_batch.side_effect = Exception("ffmpeg crash")
        result = detect_visual_dead_frames("test.mp4")
        assert result == 0.0

    @patch("src.video_processor._batch_sample_ydif")
    def test_boundary_value_exact_threshold(self, mock_batch):
        """YDIF exactly at threshold (0.5) is considered motion."""
        mock_batch.return_value = [0.5, 1.0]
        result = detect_visual_dead_frames("test.mp4", start_offset=0.0)
        # 0.5 >= 0.5, so motion detected immediately
        assert result == 0.0


class TestCropToVerticalVisualDeadFrames:
    """Integration test: crop_to_vertical should call detect_visual_dead_frames."""

    @patch("src.video_processor.detect_visual_dead_frames", return_value=1.5)
    @patch("src.video_processor._run_ffmpeg")
    @patch("src.video_processor._measure_loudness", return_value=None)
    @patch("src.video_processor.detect_leading_silence", return_value=2.0)
    @patch("src.video_processor._probe_video_info", return_value=(30.0, (1920, 1080)))
    @patch("src.video_processor.os.path.exists", return_value=False)
    def test_visual_trim_added_to_audio_trim(self, mock_exists, mock_probe, mock_silence,
                                              mock_loudness, mock_ffmpeg, mock_visual):
        """crop_to_vertical adds visual_trim to silence_trim."""
        mock_ffmpeg.return_value = True

        with patch.dict("os.environ", {"DISABLE_GPU_ENCODE": "1"}):
            crop_to_vertical("test.mp4", "/tmp/test", facecam_mode="off")

        # detect_visual_dead_frames should be called with start_offset=2.0 (audio silence)
        mock_visual.assert_called_once_with("test.mp4", start_offset=2.0)

        # _run_ffmpeg should be called with ss=3.5 (2.0 audio + 1.5 visual)
        _, kwargs = mock_ffmpeg.call_args
        assert kwargs.get("ss") == 3.5

    @patch("src.video_processor.detect_visual_dead_frames", return_value=0.0)
    @patch("src.video_processor._run_ffmpeg")
    @patch("src.video_processor._measure_loudness", return_value=None)
    @patch("src.video_processor.detect_leading_silence", return_value=0.0)
    @patch("src.video_processor._probe_video_info", return_value=(30.0, (1920, 1080)))
    @patch("src.video_processor.os.path.exists", return_value=False)
    def test_no_trim_when_no_dead_frames(self, mock_exists, mock_probe, mock_silence,
                                         mock_loudness, mock_ffmpeg, mock_visual):
        """No visual trim when detect_visual_dead_frames returns 0."""
        mock_ffmpeg.return_value = True

        with patch.dict("os.environ", {"DISABLE_GPU_ENCODE": "1"}):
            crop_to_vertical("test.mp4", "/tmp/test", facecam_mode="off")

        # _run_ffmpeg should be called with ss=0.0
        _, kwargs = mock_ffmpeg.call_args
        assert kwargs.get("ss") == 0.0


class TestFindPeakActionTimestamp:
    @patch("src.video_processor.os.path.exists", return_value=True)
    @patch("src.video_processor._batch_sample_ydif")
    def test_returns_peak_window_center(self, mock_batch, mock_exists):
        # 1s window score (2 samples) peaks around index 2 -> center near 1.35s
        mock_batch.return_value = [0.1, 0.2, 5.0, 6.0, 0.1, 0.2]
        result = find_peak_action_timestamp("test.mp4", duration=4.0)
        assert result == pytest.approx(1.35, abs=0.05)

    @patch("src.video_processor.os.path.exists", return_value=True)
    @patch("src.video_processor._batch_sample_ydif", return_value=[])
    def test_failure_returns_start_offset(self, mock_batch, mock_exists):
        result = find_peak_action_timestamp("test.mp4", start_offset=2.0, duration=8.0)
        assert result == 2.0

    @patch("src.video_processor.os.path.exists", return_value=False)
    def test_missing_file_returns_start_offset(self, mock_exists):
        # When duration is None, missing file triggers early return
        result = find_peak_action_timestamp("missing.mp4", start_offset=1.5)
        assert result == 1.5


class TestTrimToOptimalLength:
    @patch("src.video_processor.os.replace")
    @patch("src.video_processor.os.path.getsize", return_value=1024)
    @patch("src.video_processor.os.path.exists")
    @patch("src.video_processor.subprocess.run")
    @patch("src.video_processor._batch_sample_ydif")
    @patch("src.video_processor._get_duration", return_value=30.0)
    def test_selects_densest_target_window(
        self,
        mock_duration,
        mock_batch,
        mock_run,
        mock_exists,
        mock_getsize,
        mock_replace,
    ):
        # exists checks: input path, tmp output path
        mock_exists.side_effect = [True, True]
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
        scores = [0.1] * 60
        for i in range(20, 50):
            scores[i] = 8.0
        mock_batch.return_value = scores

        output = trim_to_optimal_length("input.mp4", "trimmed.mp4", target_duration=15)

        assert output == "trimmed.mp4"
        cmd = mock_run.call_args[0][0]
        assert isinstance(cmd, list)
        assert "-ss" in cmd and cmd[cmd.index("-ss") + 1] == "10.00"
        assert "-t" in cmd and cmd[cmd.index("-t") + 1] == "15.00"
        assert "input.mp4" in cmd

    @patch("src.video_processor.subprocess.run")
    @patch("src.video_processor._batch_sample_ydif")
    @patch("src.video_processor._get_duration", return_value=12.0)
    @patch("src.video_processor.os.path.exists", return_value=True)
    def test_short_clip_returns_original_path(
        self,
        mock_exists,
        mock_duration,
        mock_batch,
        mock_run,
    ):
        output = trim_to_optimal_length("input.mp4", "trimmed.mp4", target_duration=15)
        assert output == "input.mp4"
        mock_batch.assert_not_called()
        mock_run.assert_not_called()

    @patch("src.video_processor.safe_remove")
    @patch("src.video_processor.subprocess.run", side_effect=Exception("ffmpeg crash"))
    @patch("src.video_processor._batch_sample_ydif", return_value=[1.0] * 60)
    @patch("src.video_processor._get_duration", return_value=30.0)
    @patch("src.video_processor.os.path.exists", return_value=True)
    def test_failure_returns_none_and_cleans_tmp(
        self,
        mock_exists,
        mock_duration,
        mock_batch,
        mock_run,
        mock_remove,
    ):
        output = trim_to_optimal_length("input.mp4", "trimmed.mp4", target_duration=15)
        assert output is None
        mock_remove.assert_called_once_with("trimmed.mp4.tmp")


class TestLoopCompatibility:
    @patch("src.video_processor.os.path.exists", return_value=True)
    @patch("src.video_processor._get_duration", return_value=20.0)
    @patch("src.video_processor.subprocess.run")
    def test_low_ydif_is_compatible(self, mock_run, mock_duration, mock_exists):
        mock_run.return_value = MagicMock(
            stderr="[Parsed_signalstats] signalstats.YDIF=3.2",
            returncode=0,
        )
        assert check_loop_compatibility("test.mp4") is True

    @patch("src.video_processor.os.path.exists", return_value=True)
    @patch("src.video_processor._get_duration", return_value=20.0)
    @patch("src.video_processor.subprocess.run")
    def test_high_ydif_is_not_compatible(self, mock_run, mock_duration, mock_exists):
        mock_run.return_value = MagicMock(
            stderr="[Parsed_signalstats] signalstats.YDIF=15.0",
            returncode=0,
        )
        assert check_loop_compatibility("test.mp4") is False

    @patch("src.video_processor.os.path.exists", return_value=True)
    @patch("src.video_processor._get_duration", return_value=20.0)
    @patch("src.video_processor.subprocess.run")
    def test_missing_metric_defaults_compatible(self, mock_run, mock_duration, mock_exists):
        mock_run.return_value = MagicMock(stderr="no metrics", returncode=0)
        assert check_loop_compatibility("test.mp4") is True


class TestCropToVerticalPeakAndLoop:
    @patch("src.video_processor.check_loop_compatibility", return_value=True)
    @patch("src.video_processor.find_peak_action_timestamp", return_value=8.0)
    @patch("src.video_processor.detect_visual_dead_frames", return_value=0.0)
    @patch("src.video_processor._run_ffmpeg")
    @patch("src.video_processor._measure_loudness", return_value=None)
    @patch("src.video_processor.detect_leading_silence", return_value=0.0)
    @patch("src.video_processor._probe_video_info", return_value=(30.0, (1920, 1080)))
    @patch("src.video_processor.os.path.exists", return_value=False)
    def test_peak_action_shifts_trim_start(self, mock_exists, mock_probe, mock_silence,
                                           mock_loudness, mock_ffmpeg, mock_visual,
                                           mock_peak, mock_loop):
        mock_ffmpeg.return_value = True
        with patch.dict("os.environ", {"DISABLE_GPU_ENCODE": "1"}):
            crop_to_vertical("test.mp4", "/tmp/test", facecam_mode="off")
        _, kwargs = mock_ffmpeg.call_args
        assert kwargs.get("ss") == pytest.approx(6.0)

    @patch("src.video_processor._apply_loop_crossfade", return_value=True)
    @patch("src.video_processor.check_loop_compatibility", return_value=False)
    @patch("src.video_processor.find_peak_action_timestamp", return_value=0.0)
    @patch("src.video_processor.detect_visual_dead_frames", return_value=0.0)
    @patch("src.video_processor._run_ffmpeg")
    @patch("src.video_processor._measure_loudness", return_value=None)
    @patch("src.video_processor.detect_leading_silence", return_value=0.0)
    @patch("src.video_processor._probe_video_info", return_value=(30.0, (1920, 1080)))
    @patch("src.video_processor.os.path.exists", return_value=False)
    def test_incompatible_loop_applies_crossfade(self, mock_exists, mock_probe, mock_silence,
                                                 mock_loudness, mock_ffmpeg, mock_visual,
                                                 mock_peak, mock_loop, mock_crossfade):
        mock_ffmpeg.return_value = True
        with patch.dict("os.environ", {"DISABLE_GPU_ENCODE": "1"}):
            output = crop_to_vertical("test.mp4", "/tmp/test", facecam_mode="off")
        assert output is not None
        mock_crossfade.assert_called_once()
        args, kwargs = mock_crossfade.call_args
        assert args[0].endswith("_vertical.mp4")
        assert kwargs.get("crossfade_duration") == pytest.approx(0.3)

"""Tests for subprocess safety: verify no command injection via adversarial filenames."""

from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import make_clip

# Adversarial filenames containing shell metacharacters
ADVERSARIAL_FILENAMES = [
    "clip with spaces.mp4",
    "clip'with'quotes.mp4",
    'clip"with"doublequotes.mp4',
    "clip;rm -rf /.mp4",
    "clip|cat /etc/passwd.mp4",
    "clip`whoami`.mp4",
    "clip$(id).mp4",
    "clip&background.mp4",
    "clip\nnewline.mp4",
    "clip>redirect.mp4",
    "clip<input.mp4",
]


class TestDownloaderSubprocessSafety:
    """Verify download_clip passes filenames as list elements, not shell strings."""

    @pytest.mark.parametrize("clip_id", ADVERSARIAL_FILENAMES)
    @patch("src.downloader.is_valid_video", return_value=True)
    @patch("src.downloader.subprocess.run")
    @patch("src.downloader.os.path.exists", return_value=False)
    @patch("src.downloader.os.makedirs")
    @patch("src.downloader.os.path.getsize", return_value=1000)
    @patch("src.downloader.os.replace")
    def test_adversarial_clip_id_passed_as_list(
        self, mock_replace, mock_getsize, mock_makedirs, mock_exists,
        mock_run, mock_valid, clip_id
    ):
        from src.downloader import download_clip

        clip = make_clip(clip_id=clip_id)
        mock_run.return_value = MagicMock(returncode=0)

        # Make exists return: False (output_path check), False (output_path remove),
        # False (tmp_path remove), True (tmp_path after download), True (getsize)
        mock_exists.side_effect = [False, False, False, True, True]

        download_clip(clip, "/tmp/test")

        # Verify subprocess.run was called with a list (not shell=True)
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert isinstance(cmd, list), "subprocess.run must be called with a list, not a string"
        # The clip URL should be a single element, not interpolated into a shell command
        assert clip.url in cmd
        assert "--max-filesize" in cmd
        assert "250M" in cmd

    @patch("src.downloader.subprocess.run")
    @patch("src.downloader.os.path.exists", return_value=False)
    @patch("src.downloader.os.makedirs")
    def test_rejects_non_twitch_url(self, mock_makedirs, mock_exists, mock_run):
        from src.downloader import download_clip

        clip = make_clip()
        clip.url = "https://evil.example.com/not-twitch"
        result = download_clip(clip, "/tmp/test")

        assert result is None
        mock_run.assert_not_called()


class TestVideoProcessorSubprocessSafety:
    """Verify video_processor passes paths as list elements to subprocess."""

    @pytest.mark.parametrize("filename", ADVERSARIAL_FILENAMES)
    @patch("src.video_processor.subprocess.run")
    def testdetect_leading_silence_list_args(self, mock_run, filename):
        from src.video_processor import detect_leading_silence

        mock_run.return_value = MagicMock(
            stderr="[silencedetect] silence_start: 0\n[silencedetect] silence_end: 1.5",
            returncode=0,
        )

        detect_leading_silence(filename)

        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert isinstance(cmd, list), "subprocess must use list args"
        assert filename in cmd

    @pytest.mark.parametrize("filename", ADVERSARIAL_FILENAMES)
    @patch("src.video_processor.subprocess.run")
    def test_probe_video_info_list_args(self, mock_run, filename):
        from src.video_processor import _probe_video_info

        mock_run.return_value = MagicMock(
            stdout='{"format":{"duration":"30.0"},"streams":[{"width":1920,"height":1080}]}',
            returncode=0,
        )

        _probe_video_info(filename)

        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert isinstance(cmd, list), "subprocess must use list args"
        assert filename in cmd

    @pytest.mark.parametrize("filename", ADVERSARIAL_FILENAMES)
    @patch("src.video_processor.subprocess.run")
    @patch("src.video_processor._get_duration", return_value=30.0)
    @patch("src.video_processor.os.path.exists", return_value=True)
    def test_score_visual_quality_list_args(self, mock_exists, mock_duration, mock_run, filename):
        from src.video_processor import score_visual_quality

        edge_stderr = "\n".join("signalstats.YAVG=12.0" for _ in range(10))
        color_lines = []
        for _ in range(10):
            color_lines.extend([
                "signalstats.UMIN=90.0",
                "signalstats.UMAX=160.0",
                "signalstats.VMIN=85.0",
                "signalstats.VMAX=170.0",
            ])
        color_stderr = "\n".join(color_lines)
        mock_run.side_effect = [
            MagicMock(stderr=edge_stderr, returncode=0),
            MagicMock(stderr=color_stderr, returncode=0),
        ]

        score_visual_quality(filename)

        commands = [c[0][0] for c in mock_run.call_args_list if c[0]]
        assert len(commands) >= 2
        assert all(isinstance(cmd, list) for cmd in commands), "subprocess must use list args"
        assert all(filename in cmd for cmd in commands)

    @pytest.mark.parametrize("filename", ADVERSARIAL_FILENAMES)
    @patch("src.video_processor.subprocess.Popen")
    @patch("src.video_processor._measure_loudness", return_value=None)
    @patch("src.video_processor.detect_leading_silence", return_value=0.0)
    @patch("src.video_processor._probe_video_info", return_value=(30.0, (1920, 1080)))
    @patch("src.video_processor.os.path.exists")
    @patch("src.video_processor.os.path.getsize", return_value=1000)
    @patch("src.video_processor.os.replace")
    def test_run_ffmpeg_list_args(
        self, mock_replace, mock_getsize, mock_exists, mock_probe,
        mock_silence, mock_loudness, mock_popen, filename
    ):
        from src.video_processor import crop_to_vertical

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0
        mock_proc.poll.return_value = 0
        mock_popen.return_value = mock_proc
        # exists: output_path (False), tmp_output (True)
        mock_exists.side_effect = [False, True]

        crop_to_vertical(filename, "/tmp/test", facecam_mode="off")

        commands = [c[0][0] for c in mock_popen.call_args_list if c[0]]
        assert commands, "Expected at least one Popen invocation"
        assert all(isinstance(cmd, list) for cmd in commands), "Popen must use list args"
        assert any(filename in cmd for cmd in commands)

    @pytest.mark.parametrize("filename", ADVERSARIAL_FILENAMES)
    @patch("src.video_processor._find_context_fontfile", return_value=None)
    @patch("src.video_processor.os.replace")
    @patch("src.video_processor.os.path.getsize", return_value=1000)
    @patch("src.video_processor.os.path.exists")
    @patch("src.video_processor.subprocess.run")
    def test_burn_context_overlay_list_args(
        self, mock_run, mock_exists, mock_getsize, mock_replace, mock_font, filename
    ):
        from src.video_processor import burn_context_overlay

        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
        # exists checks: input path, tmp output path
        mock_exists.side_effect = [True, True]

        burn_context_overlay(filename, "/tmp/contexted.mp4", "Valorant", "INSANE 1v5 clutch")

        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert isinstance(cmd, list), "subprocess must use list args"
        assert filename in cmd

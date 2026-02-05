"""Integration tests for main.py pipeline orchestration.

Tests the extracted _process_single_clip, _process_streamer, and end-to-end
pipeline flow with all external services mocked.
"""

import sqlite3
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, ANY

import pytest

from src.db import init_schema, insert_clip, recent_upload_count
from src.models import Clip, StreamerConfig, PipelineConfig, FacecamConfig
from src.youtube_uploader import QuotaExhaustedError, ForbiddenError
from main import (
    _process_single_clip,
    _process_streamer,
    _run_pipeline_inner,
    validate_config,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    yield c
    c.close()


@pytest.fixture
def cfg():
    return PipelineConfig(
        max_clips_per_streamer=3,
        max_clip_duration_seconds=60,
        tmp_dir="/tmp/test",
        db_path=":memory:",
        upload_spacing_hours=2,
        max_uploads_per_window=1,
        analytics_enabled=False,
    )


@pytest.fixture
def streamer():
    return StreamerConfig(
        name="teststreamer",
        twitch_id="12345",
        youtube_credentials="creds/test.json",
    )


@pytest.fixture
def clip():
    return Clip(
        id="clip_1",
        url="https://clips.twitch.tv/clip_1",
        title="Amazing Play",
        view_count=1000,
        created_at="2026-01-15T12:00:00Z",
        duration=30,
        game_id="33214",
        streamer="teststreamer",
        game_name="Fortnite",
    )


@pytest.fixture
def log():
    return MagicMock()


@pytest.fixture
def yt_service():
    return MagicMock()


# ---- _process_single_clip tests ----

class TestProcessSingleClip:
    def _call(self, clip, yt_service, conn, cfg, streamer, log, dry_run=False):
        return _process_single_clip(
            clip, yt_service, conn, cfg, streamer, log, dry_run,
            title_template=None, title_templates=None,
            description_template=None, description_templates=None,
            extra_tags_global=[],
            thumbnail_enabled=False, thumbnail_samples=8, thumbnail_width=1280,
        )

    @patch("main.check_channel_for_duplicate", return_value=None)
    @patch("main.build_upload_title", return_value="Test Title")
    @patch("main.download_clip", return_value=None)
    def test_download_fail(self, mock_dl, mock_title, mock_dedup, clip, yt_service, conn, cfg, streamer, log):
        result, yt_id = self._call(clip, yt_service, conn, cfg, streamer, log)
        assert result == "downloaded_fail"
        assert yt_id is None

    @patch("main._cleanup_tmp_files")
    @patch("main.check_channel_for_duplicate", return_value=None)
    @patch("main.build_upload_title", return_value="Test Title")
    @patch("main.crop_to_vertical", return_value=None)
    @patch("main.download_clip", return_value="/tmp/test/clip_1.mp4")
    def test_process_fail(self, mock_dl, mock_crop, mock_title, mock_dedup, mock_clean, clip, yt_service, conn, cfg, streamer, log):
        result, yt_id = self._call(clip, yt_service, conn, cfg, streamer, log)
        assert result == "processed_fail"
        assert yt_id is None

    @patch("main._cleanup_tmp_files")
    @patch("main.crop_to_vertical", return_value="/tmp/test/clip_1_vertical.mp4")
    @patch("main.download_clip", return_value="/tmp/test/clip_1.mp4")
    def test_dry_run(self, mock_dl, mock_crop, mock_clean, clip, yt_service, conn, cfg, streamer, log):
        result, yt_id = self._call(clip, yt_service, conn, cfg, streamer, log, dry_run=True)
        assert result == "dry_run"
        assert yt_id is None

    @patch("main.check_channel_for_duplicate", return_value="existing_yt_id")
    @patch("main.build_upload_title", return_value="Test Title")
    def test_duplicate_detected(self, mock_title, mock_dedup,
                                 clip, yt_service, conn, cfg, streamer, log):
        result, yt_id = self._call(clip, yt_service, conn, cfg, streamer, log)
        assert result == "duplicate"
        assert clip.youtube_id == "existing_yt_id"

    @patch("main._cleanup_tmp_files")
    @patch("main.upload_short", side_effect=QuotaExhaustedError("quotaExceeded"))
    @patch("main.check_channel_for_duplicate", return_value=None)
    @patch("main.build_upload_title", return_value="Test Title")
    @patch("main.crop_to_vertical", return_value="/tmp/test/clip_1_vertical.mp4")
    @patch("main.download_clip", return_value="/tmp/test/clip_1.mp4")
    def test_quota_exhausted(self, mock_dl, mock_crop, mock_title, mock_dedup, mock_upload,
                              mock_clean, clip, yt_service, conn, cfg, streamer, log):
        result, yt_id = self._call(clip, yt_service, conn, cfg, streamer, log)
        assert result == "quota_exhausted"

    @patch("main._cleanup_tmp_files")
    @patch("main.upload_short", side_effect=ForbiddenError("unknown"))
    @patch("main.check_channel_for_duplicate", return_value=None)
    @patch("main.build_upload_title", return_value="Test Title")
    @patch("main.crop_to_vertical", return_value="/tmp/test/clip_1_vertical.mp4")
    @patch("main.download_clip", return_value="/tmp/test/clip_1.mp4")
    def test_forbidden(self, mock_dl, mock_crop, mock_title, mock_dedup, mock_upload,
                        mock_clean, clip, yt_service, conn, cfg, streamer, log):
        result, yt_id = self._call(clip, yt_service, conn, cfg, streamer, log)
        assert result == "forbidden"

    @patch("main._cleanup_tmp_files")
    @patch("main.upload_short", return_value=None)
    @patch("main.check_channel_for_duplicate", return_value=None)
    @patch("main.build_upload_title", return_value="Test Title")
    @patch("main.crop_to_vertical", return_value="/tmp/test/clip_1_vertical.mp4")
    @patch("main.download_clip", return_value="/tmp/test/clip_1.mp4")
    def test_upload_fail(self, mock_dl, mock_crop, mock_title, mock_dedup, mock_upload,
                          mock_clean, clip, yt_service, conn, cfg, streamer, log):
        result, yt_id = self._call(clip, yt_service, conn, cfg, streamer, log)
        assert result == "upload_fail"

    @patch("main._cleanup_tmp_files")
    @patch("main.verify_upload", return_value=True)
    @patch("main.upload_short", return_value="yt_abc123")
    @patch("main.check_channel_for_duplicate", return_value=None)
    @patch("main.build_upload_title", return_value="Test Title")
    @patch("main.crop_to_vertical", return_value="/tmp/test/clip_1_vertical.mp4")
    @patch("main.download_clip", return_value="/tmp/test/clip_1.mp4")
    def test_successful_upload(self, mock_dl, mock_crop, mock_title, mock_dedup, mock_upload,
                                mock_verify, mock_clean, clip, yt_service, conn, cfg, streamer, log):
        result, yt_id = self._call(clip, yt_service, conn, cfg, streamer, log)
        assert result == "uploaded"
        assert yt_id == "yt_abc123"
        # Verify clip was inserted into DB
        row = conn.execute("SELECT youtube_id FROM clips WHERE clip_id = ?", (clip.id,)).fetchone()
        assert row is not None
        assert row["youtube_id"] == "yt_abc123"

    @patch("main._cleanup_tmp_files")
    @patch("main.set_thumbnail", return_value=True)
    @patch("main.extract_thumbnail", return_value="/tmp/test/thumb.jpg")
    @patch("main.verify_upload", return_value=True)
    @patch("main.upload_short", return_value="yt_abc123")
    @patch("main.check_channel_for_duplicate", return_value=None)
    @patch("main.build_upload_title", return_value="Test Title")
    @patch("main.crop_to_vertical", return_value="/tmp/test/clip_1_vertical.mp4")
    @patch("main.download_clip", return_value="/tmp/test/clip_1.mp4")
    def test_thumbnail_extraction_on_success(self, mock_dl, mock_crop, mock_title, mock_dedup,
                                              mock_upload, mock_verify, mock_thumb, mock_set_thumb,
                                              mock_clean, clip, yt_service, conn, cfg, streamer, log):
        result, yt_id = _process_single_clip(
            clip, yt_service, conn, cfg, streamer, log, False,
            title_template=None, title_templates=None,
            description_template=None, description_templates=None,
            extra_tags_global=[],
            thumbnail_enabled=True, thumbnail_samples=8, thumbnail_width=1280,
        )
        assert result == "uploaded"
        mock_thumb.assert_called_once()
        mock_set_thumb.assert_called_once_with(yt_service, "yt_abc123", "/tmp/test/thumb.jpg")


# ---- _process_streamer tests ----

class TestProcessStreamer:
    def _make_raw_config(self):
        return {
            "youtube": {
                "client_secrets_file": "creds/secrets.json",
                "title_template": "{title} | {streamer}",
            },
        }

    @patch("main.update_streamer_stats")
    @patch("main.get_authenticated_service", return_value=MagicMock())
    @patch("main.recent_upload_count", return_value=0)
    @patch("main.filter_new_clips")
    @patch("main.filter_and_rank")
    def test_upload_spacing_enforced(self, mock_rank, mock_dedup, mock_recent,
                                      mock_auth, mock_stats, conn, cfg, streamer, log):
        """When recent_upload_count == max_uploads_per_window, no uploads happen."""
        mock_recent.return_value = 1  # Already at max (max_uploads_per_window=1)

        twitch = MagicMock()
        twitch.fetch_clips.return_value = [
            Clip(id="c1", url="u", title="T", view_count=100, created_at="2026-01-15T12:00:00Z", duration=30, streamer="teststreamer"),
        ]
        mock_rank.return_value = twitch.fetch_clips.return_value
        mock_dedup.return_value = twitch.fetch_clips.return_value

        result = _process_streamer(
            streamer, twitch, cfg, conn, log, False,
            "creds/secrets.json", None, None, None, None, [], False, 8, 1280,
        )
        fetched, filtered, downloaded, processed, uploaded, failed, quota_exhausted = result
        assert uploaded == 0  # No uploads due to spacing

    @patch("main.update_streamer_stats")
    @patch("main._process_single_clip")
    @patch("main.get_authenticated_service", return_value=MagicMock())
    @patch("main.recent_upload_count", return_value=0)
    @patch("main.filter_new_clips")
    @patch("main.filter_and_rank")
    def test_consecutive_403_circuit_breaker(self, mock_rank, mock_dedup, mock_recent,
                                              mock_auth, mock_process, mock_stats,
                                              conn, cfg, streamer, log):
        """After 3 consecutive ForbiddenErrors, remaining clips are skipped."""
        cfg.max_clips_per_streamer = 5
        cfg.max_uploads_per_window = 5
        clips = [
            Clip(id=f"c{i}", url="u", title="T", view_count=100,
                 created_at="2026-01-15T12:00:00Z", duration=30, streamer="teststreamer")
            for i in range(5)
        ]

        twitch = MagicMock()
        twitch.fetch_clips.return_value = clips
        twitch.get_game_names.return_value = {}
        mock_rank.return_value = clips
        mock_dedup.return_value = clips

        # 3 consecutive forbidden then "uploaded" (should never be reached)
        mock_process.side_effect = [
            ("forbidden", None),
            ("forbidden", None),
            ("forbidden", None),
            ("uploaded", "yt_id"),
            ("uploaded", "yt_id"),
        ]

        result = _process_streamer(
            streamer, twitch, cfg, conn, log, False,
            "creds/secrets.json", None, None, None, None, [], False, 8, 1280,
        )
        _, _, _, _, uploaded, failed, _ = result
        assert failed == 3
        assert uploaded == 0
        # Only 3 calls to _process_single_clip (4th and 5th skipped)
        assert mock_process.call_count == 3

    @patch("main.update_streamer_stats")
    @patch("main._process_single_clip")
    @patch("main.get_authenticated_service", return_value=MagicMock())
    @patch("main.recent_upload_count", return_value=0)
    @patch("main.filter_new_clips")
    @patch("main.filter_and_rank")
    def test_quota_exhausted_stops_processing(self, mock_rank, mock_dedup, mock_recent,
                                               mock_auth, mock_process, mock_stats,
                                               conn, cfg, streamer, log):
        cfg.max_clips_per_streamer = 3
        cfg.max_uploads_per_window = 3
        clips = [
            Clip(id=f"c{i}", url="u", title="T", view_count=100,
                 created_at="2026-01-15T12:00:00Z", duration=30, streamer="teststreamer")
            for i in range(3)
        ]

        twitch = MagicMock()
        twitch.fetch_clips.return_value = clips
        twitch.get_game_names.return_value = {}
        mock_rank.return_value = clips
        mock_dedup.return_value = clips

        mock_process.side_effect = [
            ("uploaded", "yt_1"),
            ("quota_exhausted", None),
            ("uploaded", "yt_3"),  # Should not be reached
        ]

        result = _process_streamer(
            streamer, twitch, cfg, conn, log, False,
            "creds/secrets.json", None, None, None, None, [], False, 8, 1280,
        )
        _, _, _, _, uploaded, _, quota_exhausted = result
        assert quota_exhausted is True
        assert uploaded == 1
        assert mock_process.call_count == 2

    @patch("main.update_streamer_stats")
    @patch("main._process_single_clip")
    @patch("main.recent_upload_count", return_value=0)
    @patch("main.filter_new_clips")
    @patch("main.filter_and_rank")
    def test_dry_run_skips_auth(self, mock_rank, mock_dedup, mock_recent,
                                 mock_process, mock_stats, conn, cfg, streamer, log):
        clips = [
            Clip(id="c1", url="u", title="T", view_count=100,
                 created_at="2026-01-15T12:00:00Z", duration=30, streamer="teststreamer"),
        ]

        twitch = MagicMock()
        twitch.fetch_clips.return_value = clips
        twitch.get_game_names.return_value = {}
        mock_rank.return_value = clips
        mock_dedup.return_value = clips
        mock_process.return_value = ("dry_run", None)

        result = _process_streamer(
            streamer, twitch, cfg, conn, log, True,
            None, None, None, None, None, [], False, 8, 1280,
        )
        _, _, _, _, uploaded, _, _ = result
        assert uploaded == 1

    @patch("main.update_streamer_stats")
    def test_fetch_failure_returns_zeros(self, mock_stats, conn, cfg, streamer, log):
        twitch = MagicMock()
        twitch.fetch_clips.side_effect = Exception("network error")

        result = _process_streamer(
            streamer, twitch, cfg, conn, log, False,
            "creds/secrets.json", None, None, None, None, [], False, 8, 1280,
        )
        fetched, filtered, downloaded, processed, uploaded, failed, quota = result
        assert fetched == 0
        assert uploaded == 0
        assert quota is False


# ---- _run_pipeline_inner tests ----

class TestRunPipelineInner:
    @patch.dict("os.environ", {"TWITCH_CLIENT_ID": "id", "TWITCH_CLIENT_SECRET": "secret"})
    @patch("main._process_streamer")
    @patch("main.TwitchClient")
    def test_happy_path_end_to_end(self, mock_twitch_cls, mock_process, conn, cfg):
        streamer = StreamerConfig(name="test", twitch_id="123", youtube_credentials="creds/t.json")
        raw_config = {"youtube": {"client_secrets_file": "creds/secrets.json"}}

        mock_process.return_value = (10, 3, 2, 2, 1, 0, False)

        log = MagicMock()
        _run_pipeline_inner(cfg, [streamer], raw_config, conn, log)

        mock_process.assert_called_once()
        # Verify the summary log was called
        log.info.assert_any_call(
            "Pipeline complete: fetched=%d filtered=%d downloaded=%d processed=%d uploaded=%d failed=%d",
            10, 3, 2, 2, 1, 0,
        )

    @patch.dict("os.environ", {"TWITCH_CLIENT_ID": "id", "TWITCH_CLIENT_SECRET": "secret"})
    @patch("main._process_streamer")
    @patch("main.TwitchClient")
    def test_quota_exhausted_stops_all_streamers(self, mock_twitch_cls, mock_process, conn, cfg):
        streamer1 = StreamerConfig(name="s1", twitch_id="1", youtube_credentials="creds/s1.json")
        streamer2 = StreamerConfig(name="s2", twitch_id="2", youtube_credentials="creds/s2.json")
        raw_config = {"youtube": {"client_secrets_file": "creds/secrets.json"}}

        # First streamer hits quota, second should be skipped
        mock_process.side_effect = [
            (5, 2, 1, 1, 0, 0, True),
            (5, 2, 1, 1, 1, 0, False),  # Should not be reached
        ]

        log = MagicMock()
        _run_pipeline_inner(cfg, [streamer1, streamer2], raw_config, conn, log)

        assert mock_process.call_count == 1

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_env_vars_raises(self, conn, cfg):
        raw_config = {"youtube": {"client_secrets_file": "creds/secrets.json"}}
        log = MagicMock()
        with pytest.raises(ValueError, match="TWITCH_CLIENT_ID"):
            _run_pipeline_inner(cfg, [], raw_config, conn, log)

    @patch.dict("os.environ", {"TWITCH_CLIENT_ID": "id", "TWITCH_CLIENT_SECRET": "secret"})
    def test_missing_client_secrets_file_raises(self, conn, cfg):
        raw_config = {"youtube": {}}
        log = MagicMock()
        with pytest.raises(ValueError, match="client_secrets_file"):
            _run_pipeline_inner(cfg, [], raw_config, conn, log)


# ---- validate_config tests ----

class TestValidateConfig:
    @patch.dict("os.environ", {"TWITCH_CLIENT_ID": "id", "TWITCH_CLIENT_SECRET": "secret"})
    def test_valid_config_passes(self):
        streamers = [StreamerConfig(name="s", twitch_id="1", youtube_credentials="creds.json")]
        raw = {"youtube": {"client_secrets_file": "secrets.json"}}
        validate_config(streamers, raw)

    @patch.dict("os.environ", {"TWITCH_CLIENT_ID": "id", "TWITCH_CLIENT_SECRET": "secret"})
    def test_no_streamers_raises(self):
        with pytest.raises(ValueError, match="No streamers"):
            validate_config([], {"youtube": {"client_secrets_file": "s.json"}})

    @patch.dict("os.environ", {"TWITCH_CLIENT_ID": "id", "TWITCH_CLIENT_SECRET": "secret"})
    def test_missing_twitch_id_raises(self):
        streamers = [StreamerConfig(name="s", twitch_id="", youtube_credentials="c.json")]
        with pytest.raises(ValueError, match="twitch_id"):
            validate_config(streamers, {"youtube": {"client_secrets_file": "s.json"}})

    @patch.dict("os.environ", {"TWITCH_CLIENT_ID": "id", "TWITCH_CLIENT_SECRET": "secret"})
    def test_dry_run_skips_youtube_validation(self):
        streamers = [StreamerConfig(name="s", twitch_id="1", youtube_credentials="")]
        raw = {"youtube": {}}
        # Should not raise in dry run mode
        validate_config(streamers, raw, dry_run=True)

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_twitch_env_raises(self):
        streamers = [StreamerConfig(name="s", twitch_id="1", youtube_credentials="c.json")]
        raw = {"youtube": {"client_secrets_file": "s.json"}}
        with pytest.raises(ValueError, match="TWITCH_CLIENT_ID"):
            validate_config(streamers, raw)

"""Integration tests for main.py pipeline orchestration.

Tests the extracted _process_single_clip, _process_streamer, and end-to-end
pipeline flow with all external services mocked.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from main import (
    _process_single_clip,
    _process_streamer,
    _run_pipeline_inner,
    _sync_streamer_metrics,
    validate_config,
    write_github_summary,
)
from src.db import finish_pipeline_run, init_schema, insert_pipeline_run
from src.instagram_uploader import InstagramAuthError, InstagramRateLimitError
from src.models import Clip, PipelineConfig, StreamerConfig
from src.youtube_uploader import AuthenticationError, ForbiddenError, QuotaExhaustedError


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
        result, _yt_id = self._call(clip, yt_service, conn, cfg, streamer, log)
        assert result == "duplicate"
        assert clip.youtube_id == "existing_yt_id"
        mock_dedup.assert_called_once_with(yt_service, "Test Title", cache_key="creds/test.json")

    @patch("main._cleanup_tmp_files")
    @patch("main.upload_short", side_effect=QuotaExhaustedError("quotaExceeded"))
    @patch("main.check_channel_for_duplicate", return_value=None)
    @patch("main.build_upload_title", return_value="Test Title")
    @patch("main.crop_to_vertical", return_value="/tmp/test/clip_1_vertical.mp4")
    @patch("main.download_clip", return_value="/tmp/test/clip_1.mp4")
    def test_quota_exhausted(self, mock_dl, mock_crop, mock_title, mock_dedup, mock_upload,
                              mock_clean, clip, yt_service, conn, cfg, streamer, log):
        result, _yt_id = self._call(clip, yt_service, conn, cfg, streamer, log)
        assert result == "quota_exhausted"

    @patch("main._cleanup_tmp_files")
    @patch("main.upload_short", side_effect=ForbiddenError("unknown"))
    @patch("main.check_channel_for_duplicate", return_value=None)
    @patch("main.build_upload_title", return_value="Test Title")
    @patch("main.crop_to_vertical", return_value="/tmp/test/clip_1_vertical.mp4")
    @patch("main.download_clip", return_value="/tmp/test/clip_1.mp4")
    def test_forbidden(self, mock_dl, mock_crop, mock_title, mock_dedup, mock_upload,
                        mock_clean, clip, yt_service, conn, cfg, streamer, log):
        result, _yt_id = self._call(clip, yt_service, conn, cfg, streamer, log)
        assert result == "forbidden"

    @patch("main._cleanup_tmp_files")
    @patch("main.upload_short", side_effect=AuthenticationError("RedirectMissingLocation"))
    @patch("main.check_channel_for_duplicate", return_value=None)
    @patch("main.build_upload_title", return_value="Test Title")
    @patch("main.crop_to_vertical", return_value="/tmp/test/clip_1_vertical.mp4")
    @patch("main.download_clip", return_value="/tmp/test/clip_1.mp4")
    def test_auth_error(self, mock_dl, mock_crop, mock_title, mock_dedup, mock_upload,
                         mock_clean, clip, yt_service, conn, cfg, streamer, log):
        result, yt_id = self._call(clip, yt_service, conn, cfg, streamer, log)
        assert result == "auth_error"
        assert yt_id is None

    @patch("main._cleanup_tmp_files")
    @patch("main.upload_short", return_value=None)
    @patch("main.check_channel_for_duplicate", return_value=None)
    @patch("main.build_upload_title", return_value="Test Title")
    @patch("main.crop_to_vertical", return_value="/tmp/test/clip_1_vertical.mp4")
    @patch("main.download_clip", return_value="/tmp/test/clip_1.mp4")
    def test_upload_fail(self, mock_dl, mock_crop, mock_title, mock_dedup, mock_upload,
                          mock_clean, clip, yt_service, conn, cfg, streamer, log):
        result, _yt_id = self._call(clip, yt_service, conn, cfg, streamer, log)
        assert result == "upload_fail"

    @patch("main._cleanup_tmp_files")
    @patch("main.upload_short", return_value="yt_abc123")
    @patch("main.check_channel_for_duplicate", return_value=None)
    @patch("main.build_upload_title", return_value="Test Title")
    @patch("main.crop_to_vertical", return_value="/tmp/test/clip_1_vertical.mp4")
    @patch("main.download_clip", return_value="/tmp/test/clip_1.mp4")
    def test_successful_upload(self, mock_dl, mock_crop, mock_title, mock_dedup, mock_upload,
                                mock_clean, clip, yt_service, conn, cfg, streamer, log):
        result, yt_id = self._call(clip, yt_service, conn, cfg, streamer, log)
        assert result == "uploaded"
        assert yt_id == "yt_abc123"
        # Verify clip was inserted into DB
        row = conn.execute("SELECT youtube_id FROM clips WHERE clip_id = ?", (clip.id,)).fetchone()
        assert row is not None
        assert row["youtube_id"] == "yt_abc123"

    def test_verify_upload_not_in_module(self, clip, yt_service, conn, cfg, streamer, log):
        """verify_upload() should not be imported in main.py (removed from hot path)."""
        import main
        assert not hasattr(main, "verify_upload"), "verify_upload should not be imported in main.py"

    @patch("main._cleanup_tmp_files")
    @patch("main.upload_short", return_value="yt_abc123")
    @patch("main.check_channel_for_duplicate", return_value=None)
    @patch("main.build_upload_title", return_value="Prebuilt Title")
    @patch("main.crop_to_vertical", return_value="/tmp/test/clip_1_vertical.mp4")
    @patch("main.download_clip", return_value="/tmp/test/clip_1.mp4")
    def test_prebuilt_title_passed_to_upload(self, mock_dl, mock_crop, mock_title, mock_dedup,
                                              mock_upload, mock_clean, clip, yt_service, conn, cfg, streamer, log):
        """prebuilt_title from build_upload_title should be passed through to upload_short."""
        self._call(clip, yt_service, conn, cfg, streamer, log)
        # upload_short should receive prebuilt_title="Prebuilt Title"
        _, kwargs = mock_upload.call_args
        assert kwargs["prebuilt_title"] == "Prebuilt Title"

    @patch("main._cleanup_tmp_files")
    @patch("main.upload_short", return_value="yt_abc123")
    @patch("main.check_channel_for_duplicate", return_value=None)
    @patch("main.optimize_title", return_value="Optimized Title")
    @patch("main.build_upload_title", return_value="Prebuilt Title")
    @patch("main.crop_to_vertical", return_value="/tmp/test/clip_1_vertical.mp4")
    @patch("main.download_clip", return_value="/tmp/test/clip_1.mp4")
    def test_title_optimizer_changes_prebuilt_title(self, mock_dl, mock_crop, mock_title, mock_optimize,
                                                    mock_dedup, mock_upload, mock_clean,
                                                    clip, yt_service, conn, cfg, streamer, log):
        with patch.dict("os.environ", {"TITLE_OPTIMIZER_ENABLED": "true"}):
            self._call(clip, yt_service, conn, cfg, streamer, log)

        mock_optimize.assert_called_once_with("Prebuilt Title", "teststreamer", "Fortnite", "clip_1")
        mock_dedup.assert_called_once_with(yt_service, "Optimized Title", cache_key="creds/test.json")
        _, kwargs = mock_upload.call_args
        assert kwargs["prebuilt_title"] == "Optimized Title"

    @patch("main._cleanup_tmp_files")
    @patch("main.set_thumbnail", return_value=True)
    @patch("main.extract_thumbnail", return_value="/tmp/test/thumb.jpg")
    @patch("main.upload_short", return_value="yt_abc123")
    @patch("main.check_channel_for_duplicate", return_value=None)
    @patch("main.build_upload_title", return_value="Test Title")
    @patch("main.crop_to_vertical", return_value="/tmp/test/clip_1_vertical.mp4")
    @patch("main.download_clip", return_value="/tmp/test/clip_1.mp4")
    def test_thumbnail_extraction_on_success(self, mock_dl, mock_crop, mock_title, mock_dedup,
                                              mock_upload, mock_thumb, mock_set_thumb,
                                              mock_clean, clip, yt_service, conn, cfg, streamer, log):
        result, _yt_id = _process_single_clip(
            clip, yt_service, conn, cfg, streamer, log, False,
            title_template=None, title_templates=None,
            description_template=None, description_templates=None,
            extra_tags_global=[],
            thumbnail_enabled=True, thumbnail_samples=8, thumbnail_width=1280,
        )
        assert result == "uploaded"
        mock_thumb.assert_called_once()
        mock_set_thumb.assert_called_once_with(yt_service, "yt_abc123", "/tmp/test/thumb.jpg")



class TestProcessSingleClipInstagram:
    """Tests for Instagram upload integration in _process_single_clip."""

    def _call(self, clip, yt_service, conn, cfg, streamer, log, dry_run=False,
              ig_credentials="creds/ig.json"):
        return _process_single_clip(
            clip, yt_service, conn, cfg, streamer, log, dry_run,
            title_template=None, title_templates=None,
            description_template=None, description_templates=None,
            extra_tags_global=[],
            thumbnail_enabled=False, thumbnail_samples=8, thumbnail_width=1280,
            ig_credentials=ig_credentials,
            ig_caption_template=None, ig_caption_templates=None, ig_hashtags=None,
        )

    @patch("main.update_instagram_id")
    @patch("main.upload_reel", return_value="ig_media_123")
    @patch("main._cleanup_tmp_files")
    @patch("main.upload_short", return_value="yt_abc123")
    @patch("main.check_channel_for_duplicate", return_value=None)
    @patch("main.build_upload_title", return_value="Test Title")
    @patch("main.crop_to_vertical", return_value="/tmp/test/clip_1_vertical.mp4")
    @patch("main.download_clip", return_value="/tmp/test/clip_1.mp4")
    def test_instagram_uploads_after_youtube(self, mock_dl, mock_crop, mock_title,
                                              mock_dedup, mock_upload, mock_clean,
                                              mock_ig_upload, mock_ig_update,
                                              clip, yt_service, conn, cfg, streamer, log):
        cfg.instagram_enabled = True
        result, yt_id = self._call(clip, yt_service, conn, cfg, streamer, log)
        assert result == "uploaded"
        assert yt_id == "yt_abc123"
        mock_ig_upload.assert_called_once()
        mock_ig_update.assert_called_once_with(conn, clip.id, "ig_media_123")

    @patch("main.upload_reel", side_effect=InstagramAuthError("bad token"))
    @patch("main._cleanup_tmp_files")
    @patch("main.upload_short", return_value="yt_abc123")
    @patch("main.check_channel_for_duplicate", return_value=None)
    @patch("main.build_upload_title", return_value="Test Title")
    @patch("main.crop_to_vertical", return_value="/tmp/test/clip_1_vertical.mp4")
    @patch("main.download_clip", return_value="/tmp/test/clip_1.mp4")
    def test_instagram_auth_error_doesnt_block_youtube(self, mock_dl, mock_crop, mock_title,
                                                        mock_dedup, mock_upload, mock_clean,
                                                        mock_ig_upload,
                                                        clip, yt_service, conn, cfg, streamer, log):
        cfg.instagram_enabled = True
        result, yt_id = self._call(clip, yt_service, conn, cfg, streamer, log)
        assert result == "uploaded"
        assert yt_id == "yt_abc123"

    @patch("main.upload_reel", side_effect=InstagramRateLimitError("429"))
    @patch("main._cleanup_tmp_files")
    @patch("main.upload_short", return_value="yt_abc123")
    @patch("main.check_channel_for_duplicate", return_value=None)
    @patch("main.build_upload_title", return_value="Test Title")
    @patch("main.crop_to_vertical", return_value="/tmp/test/clip_1_vertical.mp4")
    @patch("main.download_clip", return_value="/tmp/test/clip_1.mp4")
    def test_instagram_rate_limit_sets_state(self, mock_dl, mock_crop, mock_title,
                                              mock_dedup, mock_upload, mock_clean,
                                              mock_ig_upload,
                                              clip, yt_service, conn, cfg, streamer, log):
        cfg.instagram_enabled = True
        ig_state = [False]
        result, yt_id = _process_single_clip(
            clip, yt_service, conn, cfg, streamer, log, False,
            title_template=None, title_templates=None,
            description_template=None, description_templates=None,
            extra_tags_global=[],
            thumbnail_enabled=False, thumbnail_samples=8, thumbnail_width=1280,
            ig_credentials="creds/ig.json",
            ig_caption_template=None, ig_caption_templates=None, ig_hashtags=None,
            ig_rate_limited_state=ig_state,
        )
        assert result == "uploaded"
        assert yt_id == "yt_abc123"
        assert ig_state[0] is True

    @patch("main.upload_reel", side_effect=Exception("IG crash"))
    @patch("main._cleanup_tmp_files")
    @patch("main.upload_short", return_value="yt_abc123")
    @patch("main.check_channel_for_duplicate", return_value=None)
    @patch("main.build_upload_title", return_value="Test Title")
    @patch("main.crop_to_vertical", return_value="/tmp/test/clip_1_vertical.mp4")
    @patch("main.download_clip", return_value="/tmp/test/clip_1.mp4")
    def test_instagram_crash_doesnt_affect_youtube(self, mock_dl, mock_crop, mock_title,
                                                     mock_dedup, mock_upload, mock_clean,
                                                     mock_ig_upload,
                                                     clip, yt_service, conn, cfg, streamer, log):
        cfg.instagram_enabled = True
        result, yt_id = self._call(clip, yt_service, conn, cfg, streamer, log)
        assert result == "uploaded"
        assert yt_id == "yt_abc123"

    @patch("main.upload_reel")
    @patch("main._cleanup_tmp_files")
    @patch("main.upload_short", return_value="yt_abc123")
    @patch("main.check_channel_for_duplicate", return_value=None)
    @patch("main.build_upload_title", return_value="Test Title")
    @patch("main.crop_to_vertical", return_value="/tmp/test/clip_1_vertical.mp4")
    @patch("main.download_clip", return_value="/tmp/test/clip_1.mp4")
    def test_instagram_skipped_when_no_credentials(self, mock_dl, mock_crop, mock_title,
                                                     mock_dedup, mock_upload, mock_clean,
                                                     mock_ig_upload,
                                                     clip, yt_service, conn, cfg, streamer, log):
        cfg.instagram_enabled = True
        result, _yt_id = _process_single_clip(
            clip, yt_service, conn, cfg, streamer, log, False,
            title_template=None, title_templates=None,
            description_template=None, description_templates=None,
            extra_tags_global=[],
            thumbnail_enabled=False, thumbnail_samples=8, thumbnail_width=1280,
            ig_credentials=None,  # No credentials
        )
        assert result == "uploaded"
        mock_ig_upload.assert_not_called()

    @patch("main.upload_reel")
    @patch("main._cleanup_tmp_files")
    @patch("main.upload_short", return_value="yt_abc123")
    @patch("main.check_channel_for_duplicate", return_value=None)
    @patch("main.build_upload_title", return_value="Test Title")
    @patch("main.crop_to_vertical", return_value="/tmp/test/clip_1_vertical.mp4")
    @patch("main.download_clip", return_value="/tmp/test/clip_1.mp4")
    def test_instagram_skipped_when_disabled(self, mock_dl, mock_crop, mock_title,
                                               mock_dedup, mock_upload, mock_clean,
                                               mock_ig_upload,
                                               clip, yt_service, conn, cfg, streamer, log):
        cfg.instagram_enabled = False
        result, _yt_id = self._call(clip, yt_service, conn, cfg, streamer, log)
        assert result == "uploaded"
        mock_ig_upload.assert_not_called()


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
        _fetched, _filtered, _downloaded, _processed, uploaded, _failed, _quota_exhausted, _skip_reason = result
        assert uploaded == 0  # No uploads due to spacing

    @patch("main._sync_streamer_metrics", return_value=2)
    def test_analytics_sync_runs_when_no_clips(self, mock_sync, conn, cfg, streamer, log):
        cfg.analytics_enabled = True
        twitch = MagicMock()
        twitch.fetch_clips.return_value = []

        result = _process_streamer(
            streamer, twitch, cfg, conn, log, False,
            "creds/secrets.json", None, None, None, None, [], False, 8, 1280,
        )
        _fetched, _filtered, _downloaded, _processed, uploaded, _failed, quota, _skip_reason = result
        assert uploaded == 0
        assert quota is False
        mock_sync.assert_called_once()

    @patch("main._sync_streamer_metrics", return_value=1)
    @patch("main.recent_upload_count", return_value=1)
    @patch("main.filter_new_clips")
    @patch("main.filter_and_rank")
    def test_analytics_sync_runs_when_spacing_limited(self, mock_rank, mock_dedup, mock_recent,
                                                      mock_sync, conn, cfg, streamer, log):
        cfg.analytics_enabled = True
        cfg.max_uploads_per_window = 1
        clips = [
            Clip(id="c1", url="u", title="T", view_count=100,
                 created_at="2026-01-15T12:00:00Z", duration=30, streamer="teststreamer"),
        ]
        twitch = MagicMock()
        twitch.fetch_clips.return_value = clips
        mock_rank.return_value = clips
        mock_dedup.return_value = clips

        result = _process_streamer(
            streamer, twitch, cfg, conn, log, False,
            "creds/secrets.json", None, None, None, None, [], False, 8, 1280,
        )
        _fetched, _filtered, _downloaded, _processed, uploaded, _failed, quota, _skip_reason = result
        assert uploaded == 0
        assert quota is False
        mock_sync.assert_called_once()

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
        _, _, _, _, uploaded, failed, _, _ = result
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
        _, _, _, _, uploaded, _, quota_exhausted, _ = result
        assert quota_exhausted is True
        assert uploaded == 1
        assert mock_process.call_count == 2

    @patch("main.update_streamer_stats")
    @patch("main._process_single_clip")
    @patch("main.get_authenticated_service", return_value=MagicMock())
    @patch("main.recent_upload_count", return_value=0)
    @patch("main.filter_new_clips")
    @patch("main.filter_and_rank")
    def test_auth_error_breaks_loop(self, mock_rank, mock_dedup, mock_recent,
                                     mock_auth, mock_process, mock_stats,
                                     conn, cfg, streamer, log):
        """Auth error on first clip should stop processing remaining clips."""
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

        mock_process.side_effect = [
            ("auth_error", None),
            ("uploaded", "yt_1"),  # Should never be reached
            ("uploaded", "yt_2"),
        ]

        result = _process_streamer(
            streamer, twitch, cfg, conn, log, False,
            "creds/secrets.json", None, None, None, None, [], False, 8, 1280,
        )
        _, _, downloaded, processed, uploaded, failed, _, _ = result
        assert mock_process.call_count == 1  # Only 1 clip attempted
        assert downloaded == 1
        assert processed == 1
        assert failed == 1
        assert uploaded == 0

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
        _, _, _, _, uploaded, _, _, _ = result
        assert uploaded == 1

    @patch("main.update_streamer_stats")
    @patch("main.recent_upload_count", return_value=0)
    @patch("main.filter_new_clips")
    @patch("main.filter_and_rank")
    def test_uploads_remaining_zero_skips_game_names(self, mock_rank, mock_dedup, mock_recent,
                                                       mock_stats, conn, cfg, streamer, log):
        """When uploads_remaining==0, get_game_names is NOT called (API call saved)."""
        mock_recent.return_value = 1  # At max (max_uploads_per_window=1)
        clips = [
            Clip(id="c1", url="u", title="T", view_count=100,
                 created_at="2026-01-15T12:00:00Z", duration=30, streamer="teststreamer"),
        ]

        twitch = MagicMock()
        twitch.fetch_clips.return_value = clips
        mock_rank.return_value = clips
        mock_dedup.return_value = clips

        result = _process_streamer(
            streamer, twitch, cfg, conn, log, False,
            "creds/secrets.json", None, None, None, None, [], False, 8, 1280,
        )
        _fetched, _filtered, _downloaded, _processed, uploaded, _failed, _, _ = result
        assert uploaded == 0
        # get_game_names should NOT have been called since uploads_remaining == 0
        twitch.get_game_names.assert_not_called()

    @patch("main.update_streamer_stats")
    @patch("main._process_single_clip")
    @patch("main.get_authenticated_service", return_value=MagicMock())
    @patch("main.recent_upload_count", return_value=0)
    @patch("main.filter_new_clips")
    @patch("main.filter_and_rank")
    def test_loop_stops_after_uploads_remaining(self, mock_rank, mock_dedup, mock_recent,
                                                mock_auth, mock_process, mock_stats,
                                                conn, cfg, streamer, log):
        """Loop processes all clips but stops uploading after uploads_remaining reached."""
        cfg.max_clips_per_streamer = 5
        cfg.max_uploads_per_window = 3
        mock_recent.return_value = 1  # 3-1 = 2 remaining
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
        mock_process.return_value = ("uploaded", "yt_1")

        _process_streamer(
            streamer, twitch, cfg, conn, log, False,
            "creds/secrets.json", None, None, None, None, [], False, 8, 1280,
        )
        # All 5 clips get game names fetched, but only 2 are uploaded
        game_ids_arg = twitch.get_game_names.call_args[0][0]
        assert len(game_ids_arg) == 5
        assert mock_process.call_count == 2

    @patch("main.update_streamer_stats")
    @patch("main._process_single_clip")
    @patch("main.get_authenticated_service", return_value=MagicMock())
    @patch("main.recent_upload_count", return_value=0)
    @patch("main.filter_new_clips")
    @patch("main.filter_and_rank")
    def test_instagram_rate_limit_disables_remaining_ig_uploads(self, mock_rank, mock_dedup, mock_recent,
                                                                 mock_auth, mock_process, mock_stats,
                                                                 conn, cfg, streamer, log):
        cfg.max_clips_per_streamer = 2
        cfg.max_uploads_per_window = 2
        cfg.instagram_enabled = True
        streamer.instagram_credentials = "creds/ig.json"
        clips = [
            Clip(id="c1", url="u", title="T1", view_count=100,
                 created_at="2026-01-15T12:00:00Z", duration=30, streamer="teststreamer"),
            Clip(id="c2", url="u", title="T2", view_count=100,
                 created_at="2026-01-15T12:01:00Z", duration=30, streamer="teststreamer"),
        ]
        twitch = MagicMock()
        twitch.fetch_clips.return_value = clips
        twitch.get_game_names.return_value = {}
        mock_rank.return_value = clips
        mock_dedup.return_value = clips

        ig_credentials_history: list[str | None] = []

        def side_effect(*args, **kwargs):
            ig_credentials_history.append(kwargs.get("ig_credentials"))
            state = kwargs.get("ig_rate_limited_state")
            if isinstance(state, list) and state and len(ig_credentials_history) == 1:
                state[0] = True
            return ("uploaded", "yt_x")

        mock_process.side_effect = side_effect

        result = _process_streamer(
            streamer, twitch, cfg, conn, log, False,
            "creds/secrets.json", None, None, None, None, [], False, 8, 1280,
        )
        _, _, _, _, uploaded, _, _, _ = result
        assert uploaded == 2
        assert ig_credentials_history == ["creds/ig.json", None]

    @patch("main.update_streamer_stats")
    def test_fetch_failure_returns_zeros(self, mock_stats, conn, cfg, streamer, log):
        twitch = MagicMock()
        twitch.fetch_clips.side_effect = Exception("network error")

        result = _process_streamer(
            streamer, twitch, cfg, conn, log, False,
            "creds/secrets.json", None, None, None, None, [], False, 8, 1280,
        )
        fetched, _filtered, _downloaded, _processed, uploaded, _failed, quota, _skip_reason = result
        assert fetched == 0
        assert uploaded == 0
        assert quota is False

    @patch("main.update_streamer_stats")
    @patch("main.recent_upload_count", return_value=1)
    @patch("main.filter_new_clips")
    @patch("main.filter_and_rank")
    def test_skip_reason_spacing_limited(self, mock_rank, mock_dedup, mock_recent,
                                         mock_stats, conn, cfg, streamer, log):
        cfg.max_uploads_per_window = 1
        clips = [
            Clip(id="c1", url="u", title="T", view_count=100,
                 created_at="2026-01-15T12:00:00Z", duration=30, streamer="teststreamer"),
        ]
        twitch = MagicMock()
        twitch.fetch_clips.return_value = clips
        mock_rank.return_value = clips
        mock_dedup.return_value = clips

        result = _process_streamer(
            streamer, twitch, cfg, conn, log, False,
            "creds/secrets.json", None, None, None, None, [], False, 8, 1280,
        )
        _, _, _, _, uploaded, _, _, skip_reason = result
        assert uploaded == 0
        assert skip_reason == "spacing_limited"

    @patch("main.update_streamer_stats")
    def test_skip_reason_no_clips(self, mock_stats, conn, cfg, streamer, log):
        twitch = MagicMock()
        twitch.fetch_clips.return_value = []

        result = _process_streamer(
            streamer, twitch, cfg, conn, log, False,
            "creds/secrets.json", None, None, None, None, [], False, 8, 1280,
        )
        _, _, _, _, uploaded, _, _, skip_reason = result
        assert uploaded == 0
        assert skip_reason == "no_clips"

    @patch("main.update_streamer_stats")
    @patch("main._process_single_clip", return_value=("uploaded", "yt_1"))
    @patch("main.get_authenticated_service", return_value=MagicMock())
    @patch("main.recent_upload_count", return_value=0)
    @patch("main.filter_new_clips")
    @patch("main.filter_and_rank")
    def test_skip_reason_none_on_success(self, mock_rank, mock_dedup, mock_recent,
                                         mock_auth, mock_process, mock_stats,
                                         conn, cfg, streamer, log):
        clips = [
            Clip(id="c1", url="u", title="T", view_count=100,
                 created_at="2026-01-15T12:00:00Z", duration=30, streamer="teststreamer"),
        ]
        twitch = MagicMock()
        twitch.fetch_clips.return_value = clips
        twitch.get_game_names.return_value = {}
        mock_rank.return_value = clips
        mock_dedup.return_value = clips

        result = _process_streamer(
            streamer, twitch, cfg, conn, log, False,
            "creds/secrets.json", None, None, None, None, [], False, 8, 1280,
        )
        _, _, _, _, uploaded, _, _, skip_reason = result
        assert uploaded == 1
        assert skip_reason is None


# ---- _run_pipeline_inner tests ----

class TestRunPipelineInner:
    @patch.dict("os.environ", {"TWITCH_CLIENT_ID": "id", "TWITCH_CLIENT_SECRET": "secret"})
    @patch("main._process_streamer")
    @patch("main.TwitchClient")
    def test_happy_path_end_to_end(self, mock_twitch_cls, mock_process, conn, cfg):
        streamer = StreamerConfig(name="test", twitch_id="123", youtube_credentials="creds/t.json")
        raw_config = {"youtube": {"client_secrets_file": "creds/secrets.json"}}

        mock_process.return_value = (10, 3, 2, 2, 1, 0, False, None)

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
            (5, 2, 1, 1, 0, 0, True, None),
            (5, 2, 1, 1, 1, 0, False, None),  # Should not be reached
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
        raw_config: dict[str, dict[str, str]] = {"youtube": {}}
        log = MagicMock()
        with pytest.raises(ValueError, match="client_secrets_file"):
            _run_pipeline_inner(cfg, [], raw_config, conn, log)


class TestWriteGithubSummary:
    def test_noop_without_env_var(self, conn, tmp_path, monkeypatch):
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        summary_path = tmp_path / "summary.md"
        run_result = {
            "totals": {"fetched": 1, "filtered": 1, "downloaded": 1, "processed": 1, "uploaded": 1, "failed": 0},
            "streamer_results": [{"streamer": "s", "uploaded": 1, "failed": 0, "skip_reason": None}],
        }

        write_github_summary(run_result, conn)
        assert not summary_path.exists()

    def test_writes_markdown_when_env_set(self, conn, tmp_path, monkeypatch):
        summary_path = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))

        now = datetime.now(UTC)
        run1_started = (now - timedelta(minutes=10)).isoformat()
        run2_started = (now - timedelta(minutes=5)).isoformat()
        run1_id = insert_pipeline_run(conn, run1_started, trigger="cron")
        run2_id = insert_pipeline_run(conn, run2_started, trigger="workflow_dispatch")
        finish_pipeline_run(
            conn,
            run1_id,
            run1_started,
            {"fetched": 5, "filtered": 2, "downloaded": 2, "processed": 2, "uploaded": 2, "failed": 1},
            [{"streamer": "alpha", "uploaded": 2, "failed": 1, "skip_reason": None}],
        )
        finish_pipeline_run(
            conn,
            run2_id,
            run2_started,
            {"fetched": 4, "filtered": 1, "downloaded": 1, "processed": 1, "uploaded": 1, "failed": 0},
            [{"streamer": "beta", "uploaded": 1, "failed": 0, "skip_reason": "no_new_clips"}],
        )

        run_result = {
            "totals": {"fetched": 5, "filtered": 2, "downloaded": 2, "processed": 2, "uploaded": 2, "failed": 1},
            "streamer_results": [{"streamer": "alpha", "uploaded": 2, "failed": 1, "skip_reason": None}],
        }
        write_github_summary(run_result, conn)

        content = summary_path.read_text(encoding="utf-8")
        assert "## Pipeline Run Summary" in content
        assert "| Uploaded | 2 | 3 |" in content
        assert "| Failed | 1 | 1 |" in content
        assert "### Per-Streamer Detail" in content
        assert "| alpha | 2 | 1 | - |" in content
        assert "### Today's Runs" in content


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
        raw: dict[str, dict[str, str]] = {"youtube": {}}
        # Should not raise in dry run mode
        validate_config(streamers, raw, dry_run=True)

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_twitch_env_raises(self):
        streamers = [StreamerConfig(name="s", twitch_id="1", youtube_credentials="c.json")]
        raw = {"youtube": {"client_secrets_file": "s.json"}}
        with pytest.raises(ValueError, match="TWITCH_CLIENT_ID"):
            validate_config(streamers, raw)

    @patch.dict("os.environ", {"TWITCH_CLIENT_ID": "id", "TWITCH_CLIENT_SECRET": "secret"})
    @patch("main.os.path.exists", return_value=False)
    def test_analytics_enabled_missing_client_secrets_file_path_raises(self, mock_exists):
        streamers = [StreamerConfig(name="s", twitch_id="1", youtube_credentials="creds.json")]
        raw = {
            "youtube": {"client_secrets_file": "missing-secrets.json"},
            "pipeline": {"analytics_enabled": True},
        }
        with pytest.raises(ValueError, match=r"analytics_enabled=True but youtube\.client_secrets_file does not exist"):
            validate_config(streamers, raw)

    @patch.dict("os.environ", {"TWITCH_CLIENT_ID": "id", "TWITCH_CLIENT_SECRET": "secret"})
    @patch("main.os.path.exists")
    def test_analytics_enabled_missing_streamer_credentials_path_raises(self, mock_exists):
        # client_secrets_file exists, streamer credentials do not
        mock_exists.side_effect = lambda p: p == "secrets.json"
        streamers = [StreamerConfig(name="s", twitch_id="1", youtube_credentials="missing-creds.json")]
        raw = {
            "youtube": {"client_secrets_file": "secrets.json"},
            "pipeline": {"analytics_enabled": True},
        }
        with pytest.raises(ValueError, match="analytics_enabled=True but streamer 's' youtube_credentials file does not exist"):
            validate_config(streamers, raw)


# ---- _sync_streamer_metrics tests ----

class TestSyncStreamerMetrics:
    """Tests for _sync_streamer_metrics — analytics + reporting API fallback."""

    def _make_clip_row(self, youtube_id, posted_at="2026-01-01T00:00:00+00:00"):
        """Return a dict that looks like a sqlite3.Row from get_clips_for_metrics."""
        return {"youtube_id": youtube_id, "posted_at": posted_at}

    @patch("main.get_analytics_service", return_value=MagicMock())
    @patch("main.get_clips_for_metrics", return_value=[])
    def test_no_eligible_clips(self, mock_clips, mock_service):
        conn = MagicMock()
        result = _sync_streamer_metrics(
            conn, "streamer1", "secrets.json", "creds.json",
            min_age_hours=48, sync_interval_hours=24, max_videos=10,
        )
        assert result == 0

    @patch("main.touch_youtube_metrics_sync", create=True)
    @patch("main.update_youtube_reach_metrics")
    @patch("main.get_reporting_service")
    @patch("main.update_youtube_metrics")
    @patch("main.fetch_video_metrics")
    @patch("main.get_clips_for_metrics")
    @patch("main.get_analytics_service", return_value=MagicMock())
    def test_analytics_success_full_metrics(self, mock_svc, mock_clips,
                                             mock_fetch, mock_update,
                                             mock_reporting_svc, mock_reach_update,
                                             mock_touch):
        """Full analytics (including impressions) — reporting NOT called."""
        mock_clips.return_value = [self._make_clip_row("yt_A")]
        mock_fetch.return_value = {
            "yt_views": 100,
            "yt_estimated_minutes_watched": 10.0,
            "yt_avg_view_duration": 30.0,
            "yt_avg_view_percentage": 60.0,
            "yt_impressions": 500,
            "yt_impressions_ctr": 0.05,
            "yt_last_sync": "2026-02-10T00:00:00+00:00",
        }

        conn = MagicMock()
        result = _sync_streamer_metrics(
            conn, "s", "secrets.json", "creds.json",
            min_age_hours=48, sync_interval_hours=24, max_videos=10,
        )

        assert result == 1
        mock_update.assert_called_once()
        mock_reporting_svc.assert_not_called()
        mock_touch.assert_not_called()

    @patch("main.touch_youtube_metrics_sync", create=True)
    @patch("main.update_youtube_reach_metrics")
    @patch("main.fetch_reach_metrics")
    @patch("main.get_reporting_service", return_value=MagicMock())
    @patch("main.update_youtube_metrics")
    @patch("main.fetch_video_metrics")
    @patch("main.get_clips_for_metrics")
    @patch("main.get_analytics_service", return_value=MagicMock())
    def test_analytics_partial_triggers_reporting(self, mock_svc, mock_clips,
                                                    mock_fetch, mock_update,
                                                    mock_reporting_svc,
                                                    mock_reach_fetch,
                                                    mock_reach_update,
                                                    mock_touch):
        """Analytics returns metrics with yt_impressions=None -> reporting called."""
        mock_clips.return_value = [self._make_clip_row("yt_B")]
        mock_fetch.return_value = {
            "yt_views": 100,
            "yt_estimated_minutes_watched": 10.0,
            "yt_avg_view_duration": 30.0,
            "yt_avg_view_percentage": 60.0,
            "yt_impressions": None,
            "yt_impressions_ctr": None,
            "yt_last_sync": "2026-02-10T00:00:00+00:00",
        }
        mock_reach_fetch.return_value = {
            "yt_B": {"yt_impressions": 200, "yt_impressions_ctr": 0.03},
        }

        conn = MagicMock()
        result = _sync_streamer_metrics(
            conn, "s", "secrets.json", "creds.json",
            min_age_hours=48, sync_interval_hours=24, max_videos=10,
        )

        assert result == 1
        mock_update.assert_called_once()
        mock_reporting_svc.assert_called_once()
        mock_reach_fetch.assert_called_once()
        mock_reach_update.assert_called_once()
        mock_touch.assert_not_called()

    @patch("main.touch_youtube_metrics_sync", create=True)
    @patch("main.update_youtube_reach_metrics")
    @patch("main.fetch_reach_metrics")
    @patch("main.get_reporting_service", return_value=MagicMock())
    @patch("main.update_youtube_metrics")
    @patch("main.fetch_video_metrics")
    @patch("main.get_clips_for_metrics")
    @patch("main.get_analytics_service", return_value=MagicMock())
    def test_analytics_fail_triggers_reporting(self, mock_svc, mock_clips,
                                                mock_fetch, mock_update,
                                                mock_reporting_svc,
                                                mock_reach_fetch,
                                                mock_reach_update,
                                                mock_touch):
        """Analytics raises Exception -> reporting called for that video."""
        mock_clips.return_value = [self._make_clip_row("yt_C")]
        mock_fetch.side_effect = Exception("analytics API down")
        mock_reach_fetch.return_value = {
            "yt_C": {"yt_impressions": 300, "yt_impressions_ctr": 0.04},
        }

        conn = MagicMock()
        result = _sync_streamer_metrics(
            conn, "s", "secrets.json", "creds.json",
            min_age_hours=48, sync_interval_hours=24, max_videos=10,
        )

        assert result == 1
        mock_update.assert_not_called()
        mock_reporting_svc.assert_called_once()
        mock_reach_update.assert_called_once()
        mock_touch.assert_not_called()

    @patch("main.touch_youtube_metrics_sync", create=True)
    @patch("main.update_youtube_reach_metrics")
    @patch("main.fetch_reach_metrics")
    @patch("main.get_reporting_service")
    @patch("main.update_youtube_metrics")
    @patch("main.fetch_video_metrics")
    @patch("main.get_clips_for_metrics")
    @patch("main.get_analytics_service", return_value=MagicMock())
    def test_both_apis_fail_no_touch(self, mock_svc, mock_clips,
                                      mock_fetch, mock_update,
                                      mock_reporting_svc,
                                      mock_reach_fetch,
                                      mock_reach_update,
                                      mock_touch):
        """Both analytics AND reporting fail -> touch_youtube_metrics_sync NOT called."""
        mock_clips.return_value = [self._make_clip_row("yt_D")]
        mock_fetch.side_effect = Exception("analytics down")
        mock_reporting_svc.side_effect = Exception("reporting down")

        conn = MagicMock()
        result = _sync_streamer_metrics(
            conn, "s", "secrets.json", "creds.json",
            min_age_hours=48, sync_interval_hours=24, max_videos=10,
        )

        assert result == 0
        mock_touch.assert_not_called()
        mock_reach_update.assert_not_called()

    @patch("main.touch_youtube_metrics_sync", create=True)
    @patch("main.update_youtube_reach_metrics")
    @patch("main.fetch_reach_metrics")
    @patch("main.get_reporting_service", return_value=MagicMock())
    @patch("main.update_youtube_metrics")
    @patch("main.fetch_video_metrics")
    @patch("main.get_clips_for_metrics")
    @patch("main.get_analytics_service", return_value=MagicMock())
    def test_reporting_api_exception_graceful(self, mock_svc, mock_clips,
                                               mock_fetch, mock_update,
                                               mock_reporting_svc,
                                               mock_reach_fetch,
                                               mock_reach_update,
                                               mock_touch):
        """Reporting throws Exception -> no crash, analytics-only synced count returned."""
        mock_clips.return_value = [self._make_clip_row("yt_E")]
        mock_fetch.return_value = {
            "yt_views": 50,
            "yt_estimated_minutes_watched": 5.0,
            "yt_avg_view_duration": 20.0,
            "yt_avg_view_percentage": 40.0,
            "yt_impressions": None,
            "yt_impressions_ctr": None,
            "yt_last_sync": "2026-02-10T00:00:00+00:00",
        }
        mock_reach_fetch.side_effect = Exception("reporting API crashed")

        conn = MagicMock()
        result = _sync_streamer_metrics(
            conn, "s", "secrets.json", "creds.json",
            min_age_hours=48, sync_interval_hours=24, max_videos=10,
        )

        # Analytics synced 1 video even though reporting failed
        assert result == 1
        mock_update.assert_called_once()

    @patch("main.touch_youtube_metrics_sync", create=True)
    @patch("main.update_youtube_reach_metrics")
    @patch("main.fetch_reach_metrics")
    @patch("main.get_reporting_service", return_value=MagicMock())
    @patch("main.update_youtube_metrics")
    @patch("main.fetch_video_metrics")
    @patch("main.get_clips_for_metrics")
    @patch("main.get_analytics_service", return_value=MagicMock())
    def test_multiple_videos_mixed_results(self, mock_svc, mock_clips,
                                             mock_fetch, mock_update,
                                             mock_reporting_svc,
                                             mock_reach_fetch,
                                             mock_reach_update,
                                             mock_touch):
        """3 videos: full analytics, partial (triggers reporting), analytics failure."""
        mock_clips.return_value = [
            self._make_clip_row("yt_1"),
            self._make_clip_row("yt_2"),
            self._make_clip_row("yt_3"),
        ]

        def fetch_side_effect(service, vid, start, end):
            if vid == "yt_1":
                return {
                    "yt_views": 100, "yt_estimated_minutes_watched": 10.0,
                    "yt_avg_view_duration": 30.0, "yt_avg_view_percentage": 60.0,
                    "yt_impressions": 500, "yt_impressions_ctr": 0.05,
                    "yt_last_sync": "2026-02-10T00:00:00+00:00",
                }
            if vid == "yt_2":
                return {
                    "yt_views": 50, "yt_estimated_minutes_watched": 5.0,
                    "yt_avg_view_duration": 20.0, "yt_avg_view_percentage": 40.0,
                    "yt_impressions": None, "yt_impressions_ctr": None,
                    "yt_last_sync": "2026-02-10T00:00:00+00:00",
                }
            raise Exception("analytics failed for yt_3")

        mock_fetch.side_effect = fetch_side_effect
        mock_reach_fetch.return_value = {
            "yt_2": {"yt_impressions": 200, "yt_impressions_ctr": 0.03},
            "yt_3": {"yt_impressions": 100, "yt_impressions_ctr": 0.02},
        }

        conn = MagicMock()
        result = _sync_streamer_metrics(
            conn, "s", "secrets.json", "creds.json",
            min_age_hours=48, sync_interval_hours=24, max_videos=10,
        )

        # yt_1: analytics OK, yt_2: analytics + reporting, yt_3: reporting only
        assert result == 3
        assert mock_update.call_count == 2  # yt_1 and yt_2
        assert mock_reach_update.call_count == 2  # yt_2 and yt_3

    @patch("main.touch_youtube_metrics_sync", create=True)
    @patch("main.update_youtube_reach_metrics")
    @patch("main.fetch_reach_metrics")
    @patch("main.get_reporting_service", return_value=MagicMock())
    @patch("main.update_youtube_metrics")
    @patch("main.fetch_video_metrics")
    @patch("main.get_clips_for_metrics")
    @patch("main.get_analytics_service", return_value=MagicMock())
    def test_return_count_accuracy(self, mock_svc, mock_clips,
                                    mock_fetch, mock_update,
                                    mock_reporting_svc,
                                    mock_reach_fetch,
                                    mock_reach_update,
                                    mock_touch):
        """Return value of len(synced_ids) matches actual synced videos."""
        mock_clips.return_value = [
            self._make_clip_row("yt_X"),
            self._make_clip_row("yt_Y"),
        ]
        mock_fetch.side_effect = [
            {
                "yt_views": 10, "yt_estimated_minutes_watched": 1.0,
                "yt_avg_view_duration": 5.0, "yt_avg_view_percentage": 20.0,
                "yt_impressions": 100, "yt_impressions_ctr": 0.02,
                "yt_last_sync": "2026-02-10T00:00:00+00:00",
            },
            None,  # yt_Y returns None (empty rows)
        ]
        # yt_Y ends up in pending_reach because metrics is None
        mock_reach_fetch.return_value = {}  # reporting finds nothing

        conn = MagicMock()
        result = _sync_streamer_metrics(
            conn, "s", "secrets.json", "creds.json",
            min_age_hours=48, sync_interval_hours=24, max_videos=10,
        )

        # Only yt_X was actually synced
        assert result == 1

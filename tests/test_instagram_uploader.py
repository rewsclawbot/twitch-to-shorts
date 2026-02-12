import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

import src.instagram_uploader as instagram_uploader
from src.instagram_uploader import (
    InstagramAuthError,
    InstagramPublishError,
    InstagramRateLimitError,
    _create_reel_container,
    _delete_temp_release,
    _drain_release_cleanup_queue,
    _poll_container_status,
    _publish_container,
    build_instagram_caption,
    check_recent_reels,
    load_instagram_token,
    refresh_instagram_token,
    upload_reel,
)
from tests.conftest import make_clip


def _make_creds(tmp_path, days_until_expiry=30):
    """Write a valid credentials file and return its path."""
    expiry = (datetime.now(UTC) + timedelta(days=days_until_expiry)).isoformat()
    creds = {
        "access_token": "test_token_abc",
        "ig_user_id": "12345678",
        "token_expiry": expiry,
    }
    path = tmp_path / "ig_creds.json"
    path.write_text(json.dumps(creds), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# TestLoadInstagramToken
# ---------------------------------------------------------------------------


class TestLoadInstagramToken:
    def test_valid_json_loads(self, tmp_path):
        path = _make_creds(tmp_path)
        data = load_instagram_token(path)
        assert data["access_token"] == "test_token_abc"
        assert data["ig_user_id"] == "12345678"
        assert "token_expiry" in data

    def test_missing_file_raises(self):
        with pytest.raises(InstagramAuthError, match="not found"):
            load_instagram_token("/nonexistent/path/creds.json")

    def test_malformed_json_raises(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json at all", encoding="utf-8")
        with pytest.raises(InstagramAuthError, match="Failed to read"):
            load_instagram_token(str(path))

    def test_missing_keys_raises(self, tmp_path):
        path = tmp_path / "partial.json"
        path.write_text(json.dumps({"access_token": "tok"}), encoding="utf-8")
        with pytest.raises(InstagramAuthError, match="missing keys"):
            load_instagram_token(str(path))

    def test_empty_access_token_raises(self, tmp_path):
        path = tmp_path / "empty_tok.json"
        data = {"access_token": "", "ig_user_id": "123", "token_expiry": "2026-03-01T00:00:00+00:00"}
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(InstagramAuthError, match="non-empty string"):
            load_instagram_token(str(path))


# ---------------------------------------------------------------------------
# TestRefreshInstagramToken
# ---------------------------------------------------------------------------


class TestRefreshInstagramToken:
    @patch("src.instagram_uploader.requests.get")
    def test_refreshes_near_expiry(self, mock_get, tmp_path):
        """Token expiring within 7 days triggers refresh."""
        path = _make_creds(tmp_path, days_until_expiry=3)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "new_token_xyz",
            "expires_in": 5184000,  # 60 days
        }
        mock_get.return_value = mock_resp

        result = refresh_instagram_token(str(path))
        assert result == "new_token_xyz"
        mock_get.assert_called_once()

        # Verify file was updated
        with open(str(path), encoding="utf-8") as f:
            updated = json.loads(f.read())
        assert updated["access_token"] == "new_token_xyz"

    @patch("src.instagram_uploader.requests.get")
    def test_skips_when_fresh(self, mock_get, tmp_path):
        """Token with > 7 days remaining is not refreshed."""
        path = _make_creds(tmp_path, days_until_expiry=30)

        result = refresh_instagram_token(str(path))
        assert result == "test_token_abc"
        mock_get.assert_not_called()

    @patch("src.instagram_uploader.requests.get")
    def test_auth_error_on_api_failure(self, mock_get, tmp_path):
        """API returning non-200 raises InstagramAuthError."""
        path = _make_creds(tmp_path, days_until_expiry=2)

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Invalid token"
        mock_get.return_value = mock_resp

        with pytest.raises(InstagramAuthError, match="Token refresh failed"):
            refresh_instagram_token(str(path))


# ---------------------------------------------------------------------------
# TestBuildInstagramCaption
# ---------------------------------------------------------------------------


class TestBuildInstagramCaption:
    def test_basic_caption(self):
        clip = make_clip(title="Amazing Play")
        caption = build_instagram_caption(clip)
        assert caption == "Amazing Play"

    def test_caption_with_hashtags(self):
        clip = make_clip(title="Great Moment")
        caption = build_instagram_caption(clip, hashtags=["gaming", "twitch", "clips"])
        assert "Great Moment" in caption
        assert "#gaming" in caption
        assert "#twitch" in caption
        assert "#clips" in caption
        assert "\n\n" in caption

    def test_truncation_at_max_length(self):
        clip = make_clip(title="A" * 2300)
        caption = build_instagram_caption(clip, max_length=2200)
        assert len(caption) <= 2200

    def test_template_rendering(self):
        clip = make_clip(title="Cool Clip", streamer="pro_player")
        clip.game_name = "Valorant"
        caption = build_instagram_caption(
            clip, caption_template="{title} by {streamer} | {game}"
        )
        assert caption == "Cool Clip by pro_player | Valorant"

    def test_sanitization_of_control_chars(self):
        clip = make_clip(title="Hello\x00World\x01Test")
        caption = build_instagram_caption(clip)
        assert "\x00" not in caption
        assert "\x01" not in caption
        assert "HelloWorldTest" in caption

    def test_prebuilt_title_used(self):
        clip = make_clip(title="Original")
        caption = build_instagram_caption(clip, prebuilt_title="Custom Title")
        assert caption == "Custom Title"

    def test_hashtags_dedup_hash(self):
        """Hashtags with leading # should not get double-hashed."""
        clip = make_clip(title="Test")
        caption = build_instagram_caption(clip, hashtags=["#gaming", "twitch"])
        assert "##gaming" not in caption
        assert "#gaming" in caption
        assert "#twitch" in caption


# ---------------------------------------------------------------------------
# TestCreateReelContainer
# ---------------------------------------------------------------------------


class TestCreateReelContainer:
    @patch("src.instagram_uploader.requests.post")
    def test_returns_container_id(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "container_123"}
        mock_post.return_value = mock_resp

        result = _create_reel_container("user_1", "tok", "https://example.com/video.mp4", "caption")
        assert result == "container_123"

    @patch("src.instagram_uploader.requests.post")
    def test_raises_auth_error_on_401(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_post.return_value = mock_resp

        with pytest.raises(InstagramAuthError):
            _create_reel_container("user_1", "bad_tok", "https://example.com/video.mp4", "caption")

    @patch("src.instagram_uploader.requests.post")
    def test_raises_auth_error_on_403(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"
        mock_post.return_value = mock_resp

        with pytest.raises(InstagramAuthError):
            _create_reel_container("user_1", "tok", "https://example.com/video.mp4", "caption")

    @patch("src.instagram_uploader.requests.post")
    def test_raises_rate_limit_on_429(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.text = "Rate limited"
        mock_post.return_value = mock_resp

        with pytest.raises(InstagramRateLimitError):
            _create_reel_container("user_1", "tok", "https://example.com/video.mp4", "caption")


# ---------------------------------------------------------------------------
# TestPollContainerStatus
# ---------------------------------------------------------------------------


class TestPollContainerStatus:
    @patch("src.instagram_uploader.requests.get")
    def test_returns_on_finished(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status_code": "FINISHED", "status": "ok"}
        mock_get.return_value = mock_resp

        result = _poll_container_status("container_1", "tok")
        assert result == "container_1"

    @patch("src.instagram_uploader.time.sleep")
    @patch("src.instagram_uploader.requests.get")
    def test_polls_multiple_times(self, mock_get, mock_sleep):
        """Polls IN_PROGRESS twice, then FINISHED."""
        in_progress = MagicMock()
        in_progress.status_code = 200
        in_progress.json.return_value = {"status_code": "IN_PROGRESS", "status": "processing"}

        finished = MagicMock()
        finished.status_code = 200
        finished.json.return_value = {"status_code": "FINISHED", "status": "ok"}

        mock_get.side_effect = [in_progress, in_progress, finished]

        result = _poll_container_status("container_1", "tok", timeout=300, interval=1)
        assert result == "container_1"
        assert mock_get.call_count == 3

    @patch("src.instagram_uploader.requests.get")
    def test_raises_on_error_status(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status_code": "ERROR", "status": "video too short"}
        mock_get.return_value = mock_resp

        with pytest.raises(InstagramPublishError, match="ERROR"):
            _poll_container_status("container_1", "tok")

    @patch("src.instagram_uploader.time.sleep")
    @patch("src.instagram_uploader.time.monotonic")
    @patch("src.instagram_uploader.requests.get")
    def test_raises_on_timeout(self, mock_get, mock_monotonic, mock_sleep):
        """Times out when container stays IN_PROGRESS past deadline."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status_code": "IN_PROGRESS", "status": "processing"}
        mock_get.return_value = mock_resp

        # First call sets deadline (monotonic() + timeout), subsequent calls exceed it
        mock_monotonic.side_effect = [0.0, 2.0]

        with pytest.raises(InstagramPublishError, match="timed out"):
            _poll_container_status("container_1", "tok", timeout=1, interval=0)


# ---------------------------------------------------------------------------
# TestPublishContainer
# ---------------------------------------------------------------------------


class TestPublishContainer:
    @patch("src.instagram_uploader.requests.post")
    def test_returns_media_id(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "media_456"}
        mock_post.return_value = mock_resp

        result = _publish_container("user_1", "tok", "container_1")
        assert result == "media_456"

    @patch("src.instagram_uploader.requests.post")
    def test_raises_on_error(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal error"
        mock_post.return_value = mock_resp

        with pytest.raises(InstagramPublishError, match="Failed to publish"):
            _publish_container("user_1", "tok", "container_1")


# ---------------------------------------------------------------------------
# TestCheckRecentReels
# ---------------------------------------------------------------------------


class TestCheckRecentReels:
    @patch("src.instagram_uploader.requests.get")
    def test_finds_duplicate(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {"id": "media_1", "caption": "Amazing Play\n\n#gaming", "timestamp": "2026-01-01"},
                {"id": "media_2", "caption": "Other clip", "timestamp": "2026-01-01"},
            ]
        }
        mock_get.return_value = mock_resp

        result = check_recent_reels("user_1", "tok", "Amazing Play")
        assert result == "media_1"

    @patch("src.instagram_uploader.requests.get")
    def test_returns_none_on_no_match(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {"id": "media_1", "caption": "Some other reel", "timestamp": "2026-01-01"},
            ]
        }
        mock_get.return_value = mock_resp

        result = check_recent_reels("user_1", "tok", "Unique Title")
        assert result is None

    @patch("src.instagram_uploader.requests.get")
    def test_handles_api_error_gracefully(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Server error"
        mock_get.return_value = mock_resp

        result = check_recent_reels("user_1", "tok", "Any")
        assert result is None


class TestTempReleaseCleanup:
    @patch("src.instagram_uploader.subprocess.run")
    def test_delete_queues_on_failure(self, mock_run, tmp_path, monkeypatch):
        queue_path = tmp_path / "cleanup_queue.txt"
        monkeypatch.setattr(instagram_uploader, "_CLEANUP_QUEUE_PATH", str(queue_path))

        failed = MagicMock()
        failed.returncode = 1
        failed.stderr = "permission denied"
        mock_run.return_value = failed

        ok = _delete_temp_release("temp-ig-clip_1")
        assert ok is False
        assert queue_path.exists()
        assert "temp-ig-clip_1" in queue_path.read_text(encoding="utf-8")

    @patch("src.instagram_uploader._delete_temp_release")
    def test_drain_retries_and_keeps_failures(self, mock_delete, tmp_path, monkeypatch):
        queue_path = tmp_path / "cleanup_queue.txt"
        monkeypatch.setattr(instagram_uploader, "_CLEANUP_QUEUE_PATH", str(queue_path))
        queue_path.write_text("temp-ig-ok\ntemp-ig-fail\n", encoding="utf-8")

        mock_delete.side_effect = [True, False]
        _drain_release_cleanup_queue()

        remaining = queue_path.read_text(encoding="utf-8")
        assert "temp-ig-fail" in remaining
        assert "temp-ig-ok" not in remaining
        mock_delete.assert_any_call("temp-ig-ok", queue_on_failure=False)
        mock_delete.assert_any_call("temp-ig-fail", queue_on_failure=False)


# ---------------------------------------------------------------------------
# TestUploadReel
# ---------------------------------------------------------------------------


class TestUploadReel:
    @patch("src.instagram_uploader._delete_temp_release")
    @patch("src.instagram_uploader._publish_container", return_value="media_789")
    @patch("src.instagram_uploader._poll_container_status", return_value="container_1")
    @patch("src.instagram_uploader._create_reel_container", return_value="container_1")
    @patch("src.instagram_uploader._create_temp_release", return_value=("temp-ig-clip_1", "https://example.com/video.mp4"))
    @patch("src.instagram_uploader.check_recent_reels", return_value=None)
    @patch("src.instagram_uploader.refresh_instagram_token", return_value="tok")
    @patch("src.instagram_uploader.load_instagram_token", return_value={"access_token": "tok", "ig_user_id": "user_1", "token_expiry": "2026-03-01"})
    def test_full_flow(self, mock_load, mock_refresh, mock_dedup, mock_release,
                       mock_container, mock_poll, mock_publish, mock_cleanup):
        clip = make_clip(clip_id="clip_1", title="Epic Moment")
        result = upload_reel("video.mp4", clip, "creds.json")
        assert result == "media_789"
        mock_load.assert_called_once()
        mock_refresh.assert_called_once()
        mock_dedup.assert_called_once()
        mock_release.assert_called_once()
        mock_container.assert_called_once()
        mock_poll.assert_called_once()
        mock_publish.assert_called_once()
        mock_cleanup.assert_called_once_with("temp-ig-clip_1")

    @patch("src.instagram_uploader._delete_temp_release")
    @patch("src.instagram_uploader.check_recent_reels", return_value=None)
    @patch("src.instagram_uploader.refresh_instagram_token", side_effect=InstagramAuthError("bad token"))
    @patch("src.instagram_uploader.load_instagram_token", return_value={"access_token": "tok", "ig_user_id": "user_1", "token_expiry": "2026-03-01"})
    def test_auth_error_propagates(self, mock_load, mock_refresh, mock_dedup, mock_cleanup):
        clip = make_clip()
        with pytest.raises(InstagramAuthError, match="bad token"):
            upload_reel("video.mp4", clip, "creds.json")

    @patch("src.instagram_uploader._delete_temp_release")
    @patch("src.instagram_uploader._create_temp_release", return_value=("temp-ig-clip_1", "https://example.com/video.mp4"))
    @patch("src.instagram_uploader.check_recent_reels", return_value=None)
    @patch("src.instagram_uploader.refresh_instagram_token", return_value="tok")
    @patch("src.instagram_uploader.load_instagram_token", return_value={"access_token": "tok", "ig_user_id": "user_1", "token_expiry": "2026-03-01"})
    @patch("src.instagram_uploader._create_reel_container", side_effect=InstagramRateLimitError("429"))
    def test_rate_limit_propagates(self, mock_container, mock_load, mock_refresh,
                                   mock_dedup, mock_release, mock_cleanup):
        clip = make_clip()
        with pytest.raises(InstagramRateLimitError):
            upload_reel("video.mp4", clip, "creds.json")
        # Cleanup should still be called via finally
        mock_cleanup.assert_called_once_with("temp-ig-clip_1")

    @patch("src.instagram_uploader._delete_temp_release")
    @patch("src.instagram_uploader._create_temp_release", return_value=("temp-ig-clip_1", "https://example.com/video.mp4"))
    @patch("src.instagram_uploader.check_recent_reels", return_value=None)
    @patch("src.instagram_uploader.refresh_instagram_token", return_value="tok")
    @patch("src.instagram_uploader.load_instagram_token", return_value={"access_token": "tok", "ig_user_id": "user_1", "token_expiry": "2026-03-01"})
    @patch("src.instagram_uploader._create_reel_container", side_effect=RuntimeError("unexpected"))
    def test_cleanup_on_failure(self, mock_container, mock_load, mock_refresh,
                                mock_dedup, mock_release, mock_cleanup):
        clip = make_clip()
        result = upload_reel("video.mp4", clip, "creds.json")
        assert result is None
        mock_cleanup.assert_called_once_with("temp-ig-clip_1")

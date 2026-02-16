"""Tests for automated streamer rotation script."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
import yaml

from scripts.rotate_streamers import (
    DEFAULT_FACECAM,
    HEALTH_THRESHOLD,
    MAX_ROTATIONS_PER_RUN,
    MIN_PROTECTED_STREAMERS,
    apply_rotations,
    calculate_health_score,
    evaluate_streamer,
    find_replacement_candidates,
    log_rotation,
    select_rotations,
)
from src.db import insert_clip
from tests.conftest import make_clip


class TestHealthScore:
    """Test health score calculation logic."""
    
    def test_perfect_health(self):
        """Streamer with excellent metrics gets high score."""
        score = calculate_health_score(
            clip_count=20,
            upload_count=10,
            avg_youtube_views=1000.0,
            clips_available=15,
        )
        assert score >= 0.9
    
    def test_zero_activity(self):
        """Inactive streamer gets zero score."""
        score = calculate_health_score(
            clip_count=0,
            upload_count=0,
            avg_youtube_views=0.0,
            clips_available=0,
        )
        assert score == 0.0
    
    def test_low_clips_available(self):
        """Low clip availability drags down score."""
        score = calculate_health_score(
            clip_count=10,
            upload_count=5,
            avg_youtube_views=500.0,
            clips_available=2,  # Only 2 clips available
        )
        # With 2/10 clips available (0.2), 5/5 uploads (1.0), 500/500 views (1.0)
        # Score = 0.2*0.4 + 1.0*0.3 + 1.0*0.3 = 0.08 + 0.3 + 0.3 = 0.68
        assert 0.65 <= score <= 0.70
    
    def test_no_uploads_but_clips_available(self):
        """Clips available but no uploads = moderate score."""
        score = calculate_health_score(
            clip_count=15,
            upload_count=0,
            avg_youtube_views=0.0,
            clips_available=10,
        )
        # 10/10 availability (1.0), 0/5 uploads (0.0), 0/500 views (0.0)
        # Score = 1.0*0.4 + 0.0*0.3 + 0.0*0.3 = 0.4
        assert score == 0.4
    
    def test_uploads_but_low_views(self):
        """Uploads happening but low views = moderate score."""
        score = calculate_health_score(
            clip_count=10,
            upload_count=5,
            avg_youtube_views=100.0,  # Only 100 avg views
            clips_available=8,
        )
        # 8/10 avail (0.8), 5/5 uploads (1.0), 100/500 views (0.2)
        # Score = 0.8*0.4 + 1.0*0.3 + 0.2*0.3 = 0.32 + 0.3 + 0.06 = 0.68
        assert 0.65 <= score <= 0.70


class TestEvaluateStreamer:
    """Test streamer evaluation with DB queries."""
    
    def test_healthy_streamer(self, conn):
        """Streamer with recent uploads and clips."""
        cutoff = (datetime.now(UTC) - timedelta(days=14)).isoformat()
        now = datetime.now(UTC).isoformat()
        
        # Insert some uploaded clips
        for i in range(5):
            clip = make_clip(
                clip_id=f"clip_{i}",
                streamer="TestStreamer",
                created_at=cutoff,
                youtube_id=f"yt_{i}",
            )
            insert_clip(conn, clip)
            # Simulate posted with views
            conn.execute(
                "UPDATE clips SET posted_at = ?, yt_views = ? WHERE clip_id = ?",
                (now, 500 + i * 100, f"clip_{i}"),
            )
        conn.commit()
        
        # Mock Twitch client
        mock_twitch = Mock()
        mock_twitch.fetch_clips.return_value = [Mock() for _ in range(10)]
        
        result = evaluate_streamer(conn, "TestStreamer", "12345", mock_twitch)
        
        assert result["name"] == "TestStreamer"
        assert result["twitch_id"] == "12345"
        assert result["upload_count"] == 5
        assert result["clips_available"] == 10
        assert result["avg_youtube_views"] > 0
        assert result["health_score"] > HEALTH_THRESHOLD
    
    def test_inactive_streamer(self, conn):
        """Streamer with no recent activity."""
        mock_twitch = Mock()
        mock_twitch.fetch_clips.return_value = []  # No clips available
        
        result = evaluate_streamer(conn, "DeadStreamer", "99999", mock_twitch)
        
        assert result["upload_count"] == 0
        assert result["clips_available"] == 0
        assert result["health_score"] == 0.0
    
    def test_clips_but_no_uploads(self, conn):
        """Clips available but we haven't uploaded any."""
        cutoff = (datetime.now(UTC) - timedelta(days=14)).isoformat()
        
        # Insert non-uploaded clips (no youtube_id or posted_at)
        for i in range(8):
            clip = make_clip(
                clip_id=f"clip_{i}",
                streamer="LazyStreamer",
                created_at=cutoff,
            )
            insert_clip(conn, clip)
        conn.commit()
        
        # Mock Twitch shows clips available
        mock_twitch = Mock()
        mock_twitch.fetch_clips.return_value = [Mock() for _ in range(12)]
        
        result = evaluate_streamer(conn, "LazyStreamer", "11111", mock_twitch)
        
        assert result["upload_count"] == 0
        assert result["clips_available"] == 12
        # Should have moderate score (clips available, no uploads)
        assert 0.3 <= result["health_score"] <= 0.5


class TestFindReplacementCandidates:
    """Test discovery integration for finding replacements."""
    
    @patch("scripts.rotate_streamers.discover_streamers")
    def test_filters_current_streamers(self, mock_discover):
        """Should exclude streamers already in config."""
        mock_discover.return_value = [
            {"user_id": "1", "user_name": "NewStreamer1", "score": 85},
            {"user_id": "2", "user_name": "CurrentStreamer", "score": 80},
            {"user_id": "3", "user_name": "NewStreamer2", "score": 75},
        ]
        
        current_ids = {"2"}  # CurrentStreamer already configured
        
        candidates = find_replacement_candidates(
            Mock(),
            current_ids,
            count=5,
        )
        
        assert len(candidates) == 2
        assert all(c["user_id"] not in current_ids for c in candidates)
        assert candidates[0]["user_name"] == "NewStreamer1"  # Sorted by score
    
    @patch("scripts.rotate_streamers.discover_streamers")
    def test_limits_results(self, mock_discover):
        """Should return max count requested."""
        mock_discover.return_value = [
            {"user_id": str(i), "user_name": f"Streamer{i}", "score": 90 - i}
            for i in range(20)
        ]
        
        candidates = find_replacement_candidates(Mock(), set(), count=3)
        
        assert len(candidates) == 3
        assert candidates[0]["score"] > candidates[1]["score"]  # Sorted


class TestSelectRotations:
    """Test rotation decision logic."""
    
    def test_protects_top_performers(self):
        """Top MIN_PROTECTED_STREAMERS should never be rotated."""
        current = [
            {"name": "Best", "health_score": 0.9, "twitch_id": "1"},
            {"name": "Good", "health_score": 0.8, "twitch_id": "2"},
            {"name": "Okay", "health_score": 0.7, "twitch_id": "3"},
            {"name": "Bad", "health_score": 0.2, "twitch_id": "4"},  # Below threshold
        ]
        
        candidates = [
            {"user_id": "10", "user_name": "NewStreamer", "score": 95},
        ]
        
        rotations = select_rotations(current, candidates)
        
        # Should only consider "Bad", not top 3
        assert len(rotations) == 1
        assert rotations[0][0]["name"] == "Bad"
    
    def test_respects_max_rotations(self):
        """Should not exceed MAX_ROTATIONS_PER_RUN."""
        current = [
            {"name": f"Protected{i}", "health_score": 0.9, "twitch_id": str(i)}
            for i in range(MIN_PROTECTED_STREAMERS)
        ] + [
            {"name": f"Bad{i}", "health_score": 0.1, "twitch_id": str(100 + i)}
            for i in range(5)  # 5 underperformers
        ]
        
        candidates = [
            {"user_id": str(200 + i), "user_name": f"New{i}", "score": 90}
            for i in range(10)
        ]
        
        rotations = select_rotations(current, candidates)
        
        assert len(rotations) <= MAX_ROTATIONS_PER_RUN
    
    def test_no_rotation_if_no_better_candidate(self):
        """Should not rotate if replacement isn't better."""
        current = [
            {"name": "Top1", "health_score": 0.9, "twitch_id": "1"},
            {"name": "Top2", "health_score": 0.8, "twitch_id": "2"},
            {"name": "Top3", "health_score": 0.7, "twitch_id": "3"},
            {"name": "Weak", "health_score": 0.35, "twitch_id": "4"},  # Below threshold
        ]
        
        # Candidate has score 30/100 = 0.30 potential, worse than Weak's 0.35
        candidates = [
            {"user_id": "10", "user_name": "WeakReplacement", "score": 30},
        ]
        
        rotations = select_rotations(current, candidates)
        
        assert len(rotations) == 0
    
    def test_rotation_when_better_candidate_exists(self):
        """Should rotate when replacement is clearly better."""
        current = [
            {"name": "Top1", "health_score": 0.9, "twitch_id": "1"},
            {"name": "Top2", "health_score": 0.8, "twitch_id": "2"},
            {"name": "Top3", "health_score": 0.7, "twitch_id": "3"},
            {"name": "Failing", "health_score": 0.2, "twitch_id": "4"},
        ]
        
        candidates = [
            {"user_id": "10", "user_name": "Rising", "score": 85},  # 0.85 potential
        ]
        
        rotations = select_rotations(current, candidates)
        
        assert len(rotations) == 1
        assert rotations[0][0]["name"] == "Failing"
        assert rotations[0][1]["user_name"] == "Rising"
    
    def test_no_rotation_all_healthy(self):
        """No rotations if all streamers above threshold."""
        current = [
            {"name": f"Healthy{i}", "health_score": 0.7 + i * 0.1, "twitch_id": str(i)}
            for i in range(5)
        ]
        
        candidates = [
            {"user_id": "10", "user_name": "NewStreamer", "score": 90},
        ]
        
        rotations = select_rotations(current, candidates)
        
        assert len(rotations) == 0


class TestApplyRotations:
    """Test config modification."""
    
    def test_dry_run_no_changes(self, tmp_path):
        """Dry run should not modify config."""
        config_path = tmp_path / "config.yaml"
        config = {
            "streamers": [
                {"name": "OldStreamer", "twitch_id": "1", "youtube_credentials": "creds.json"},
                {"name": "KeepStreamer", "twitch_id": "2", "youtube_credentials": "creds.json"},
            ]
        }
        config_path.write_text(yaml.dump(config))
        
        rotations = [
            (
                {"name": "OldStreamer", "twitch_id": "1"},
                {"user_id": "10", "user_name": "NewStreamer"},
            )
        ]
        
        # Dry run
        with patch("scripts.rotate_streamers.CONFIG_PATH", config_path):
            result = apply_rotations(config, rotations, dry_run=True)
        
        assert result is True
        # Config file should be unchanged
        reloaded = yaml.safe_load(config_path.read_text())
        assert len(reloaded["streamers"]) == 2
        assert reloaded["streamers"][0]["name"] == "OldStreamer"
    
    def test_execute_modifies_config(self, tmp_path):
        """Execute mode should update config."""
        config_path = tmp_path / "config.yaml"
        config = {
            "streamers": [
                {
                    "name": "OldStreamer",
                    "twitch_id": "1",
                    "youtube_credentials": "creds.json",
                    "facecam": {"x": 0.5, "y": 0.5, "w": 0.2, "h": 0.3},
                    "privacy_status": "public",
                    "category_id": "20",
                },
                {
                    "name": "KeepStreamer",
                    "twitch_id": "2",
                    "youtube_credentials": "creds.json",
                    "facecam": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.3},
                    "privacy_status": "public",
                    "category_id": "20",
                },
            ]
        }
        config_path.write_text(yaml.dump(config))
        
        rotations = [
            (
                {"name": "OldStreamer", "twitch_id": "1"},
                {"user_id": "10", "user_name": "NewStreamer"},
            )
        ]
        
        with patch("scripts.rotate_streamers.CONFIG_PATH", config_path):
            result = apply_rotations(config, rotations, dry_run=False)
        
        assert result is True
        
        # Verify config was updated
        reloaded = yaml.safe_load(config_path.read_text())
        assert len(reloaded["streamers"]) == 2
        
        streamer_names = {s["name"] for s in reloaded["streamers"]}
        assert "OldStreamer" not in streamer_names
        assert "NewStreamer" in streamer_names
        assert "KeepStreamer" in streamer_names
        
        # Verify new streamer has correct defaults
        new_streamer = next(s for s in reloaded["streamers"] if s["name"] == "NewStreamer")
        assert new_streamer["twitch_id"] == "10"
        assert new_streamer["youtube_credentials"] == "creds.json"
        assert new_streamer["facecam"] == DEFAULT_FACECAM
        assert new_streamer["privacy_status"] == "public"
        assert new_streamer["category_id"] == "20"
    
    def test_creates_backup(self, tmp_path):
        """Should create backup before modifying."""
        config_path = tmp_path / "config.yaml"
        config = {
            "streamers": [
                {"name": "OldStreamer", "twitch_id": "1", "youtube_credentials": "creds.json"},
            ]
        }
        config_path.write_text(yaml.dump(config))
        
        rotations = [
            (
                {"name": "OldStreamer", "twitch_id": "1"},
                {"user_id": "10", "user_name": "NewStreamer"},
            )
        ]
        
        with patch("scripts.rotate_streamers.CONFIG_PATH", config_path):
            apply_rotations(config, rotations, dry_run=False)
        
        # Check backup was created
        backups = list(tmp_path.glob("config.yaml.backup-*"))
        assert len(backups) == 1
        
        # Verify backup contains original config
        backup_config = yaml.safe_load(backups[0].read_text())
        assert backup_config["streamers"][0]["name"] == "OldStreamer"
    
    def test_multiple_rotations(self, tmp_path):
        """Should handle multiple rotations in one run."""
        config_path = tmp_path / "config.yaml"
        config = {
            "streamers": [
                {"name": "Keep1", "twitch_id": "1", "youtube_credentials": "creds.json"},
                {"name": "Remove1", "twitch_id": "2", "youtube_credentials": "creds.json"},
                {"name": "Remove2", "twitch_id": "3", "youtube_credentials": "creds.json"},
            ]
        }
        config_path.write_text(yaml.dump(config))
        
        rotations = [
            (
                {"name": "Remove1", "twitch_id": "2"},
                {"user_id": "10", "user_name": "New1"},
            ),
            (
                {"name": "Remove2", "twitch_id": "3"},
                {"user_id": "11", "user_name": "New2"},
            ),
        ]
        
        with patch("scripts.rotate_streamers.CONFIG_PATH", config_path):
            apply_rotations(config, rotations, dry_run=False)
        
        reloaded = yaml.safe_load(config_path.read_text())
        assert len(reloaded["streamers"]) == 3
        
        names = {s["name"] for s in reloaded["streamers"]}
        assert names == {"Keep1", "New1", "New2"}


class TestLogRotation:
    """Test rotation logging."""
    
    def test_creates_log_file(self, tmp_path):
        """Should create log file if it doesn't exist."""
        log_path = tmp_path / "rotation_log.json"
        
        rotations = [
            (
                {
                    "name": "OldStreamer",
                    "twitch_id": "1",
                    "health_score": 0.2,
                    "upload_count": 0,
                    "avg_youtube_views": 0.0,
                },
                {
                    "user_name": "NewStreamer",
                    "user_id": "10",
                    "score": 85,
                    "clip_count": 15,
                    "avg_views": 1200.0,
                },
            )
        ]
        
        with patch("scripts.rotate_streamers.ROTATION_LOG_PATH", log_path):
            log_rotation(rotations, dry_run=False)
        
        assert log_path.exists()
        
        log_data = json.loads(log_path.read_text())
        assert "rotations" in log_data
        assert len(log_data["rotations"]) == 1
        
        entry = log_data["rotations"][0]
        assert entry["dry_run"] is False
        assert len(entry["changes"]) == 1
        assert entry["changes"][0]["removed"]["name"] == "OldStreamer"
        assert entry["changes"][0]["added"]["name"] == "NewStreamer"
    
    def test_appends_to_existing_log(self, tmp_path):
        """Should append to existing log."""
        log_path = tmp_path / "rotation_log.json"
        
        # Create existing log
        existing = {
            "rotations": [
                {
                    "timestamp": "2024-01-01T00:00:00Z",
                    "dry_run": True,
                    "changes": [],
                }
            ]
        }
        log_path.write_text(json.dumps(existing))
        
        rotations = [
            (
                {
                    "name": "OldStreamer",
                    "twitch_id": "1",
                    "health_score": 0.2,
                    "upload_count": 0,
                    "avg_youtube_views": 0.0,
                },
                {
                    "user_name": "NewStreamer",
                    "user_id": "10",
                    "score": 85,
                    "clip_count": 15,
                    "avg_views": 1200.0,
                },
            )
        ]
        
        with patch("scripts.rotate_streamers.ROTATION_LOG_PATH", log_path):
            log_rotation(rotations, dry_run=False)
        
        log_data = json.loads(log_path.read_text())
        assert len(log_data["rotations"]) == 2
        assert log_data["rotations"][0]["timestamp"] == "2024-01-01T00:00:00Z"
        assert log_data["rotations"][1]["dry_run"] is False
    
    def test_records_dry_run_flag(self, tmp_path):
        """Should record whether it was a dry run."""
        log_path = tmp_path / "rotation_log.json"
        
        rotations = [
            (
                {
                    "name": "OldStreamer",
                    "twitch_id": "1",
                    "health_score": 0.2,
                    "upload_count": 0,
                    "avg_youtube_views": 0.0,
                },
                {
                    "user_name": "NewStreamer",
                    "user_id": "10",
                    "score": 85,
                    "clip_count": 15,
                    "avg_views": 1200.0,
                },
            )
        ]
        
        with patch("scripts.rotate_streamers.ROTATION_LOG_PATH", log_path):
            log_rotation(rotations, dry_run=True)
        
        log_data = json.loads(log_path.read_text())
        assert log_data["rotations"][0]["dry_run"] is True

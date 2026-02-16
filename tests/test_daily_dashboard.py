"""Tests for daily dashboard script."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import mock_open, patch

import pytest
import yaml

from scripts.daily_dashboard import (
    format_time_ago,
    format_youtube_url,
    generate_report,
    get_analytics_snapshot,
    get_growth_metrics,
    get_pipeline_health,
    get_trending_games_info,
    get_trending_streamers_for_games,
    get_upload_summary,
    load_config,
    load_trending_cache,
)


@pytest.fixture
def conn():
    """In-memory database for testing."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from src.db import init_schema
    init_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def sample_config():
    """Sample config data."""
    return {
        'streamers': [
            {'name': 'TheBurntPeanut', 'twitch_id': '472066926'},
            {'name': 'xQc', 'twitch_id': '71092938'},
            {'name': 'Lirik', 'twitch_id': '23161357'},
        ],
        'pipeline': {
            'db_path': 'data/clips.db',
        },
    }


@pytest.fixture
def sample_trending_cache():
    """Sample trending cache data."""
    return {
        'timestamp': datetime.now(UTC).timestamp(),
        'games': [
            {'name': 'League of Legends', 'id': '21779', 'rank': 1},
            {'name': 'Grand Theft Auto V', 'id': '32982', 'rank': 2},
            {'name': 'Valorant', 'id': '516575', 'rank': 3},
            {'name': 'Minecraft', 'id': '27471', 'rank': 4},
            {'name': 'Counter-Strike', 'id': '32399', 'rank': 5},
        ],
    }


def populate_test_data_full(conn: sqlite3.Connection):
    """Populate database with full test data."""
    now = datetime.now(UTC)
    
    # Use calendar day boundaries to ensure consistent test results
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_noon = today_midnight + timedelta(hours=12)
    today_morning = today_midnight + timedelta(hours=8)
    
    yesterday_midnight = today_midnight - timedelta(days=1)
    yesterday_noon = yesterday_midnight + timedelta(hours=12)
    
    two_days_ago = yesterday_midnight - timedelta(days=1, hours=-12)
    
    # Recent uploads (today at noon)
    conn.execute("""
        INSERT INTO clips (
            clip_id, streamer, title, title_variant, youtube_id, posted_at,
            yt_views, yt_avg_view_percentage, game_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        'clip1', 'TheBurntPeanut', 'Amazing Play', 'INSANE Play!', 'yt123',
        today_morning.isoformat(), 1500, 65.5, 'League of Legends'
    ))
    
    conn.execute("""
        INSERT INTO clips (
            clip_id, streamer, title, title_variant, youtube_id, posted_at,
            yt_views, yt_avg_view_percentage, game_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        'clip2', 'xQc', 'Epic Moment', 'This is CRAZY', 'yt456',
        today_noon.isoformat(), 2500, 72.3, 'Valorant'
    ))
    
    # Older upload (for best performing)
    conn.execute("""
        INSERT INTO clips (
            clip_id, streamer, title, title_variant, youtube_id, posted_at,
            yt_views, yt_avg_view_percentage, game_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        'clip3', 'Lirik', 'Best Clip Ever', 'LEGENDARY moment', 'yt789',
        two_days_ago.isoformat(), 5000, 80.1, 'Minecraft'
    ))
    
    # Yesterday upload (for growth metrics)
    conn.execute("""
        INSERT INTO clips (
            clip_id, streamer, title, youtube_id, posted_at,
            yt_views, game_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        'clip4', 'TheBurntPeanut', 'Yesterday Upload', 'yt999',
        yesterday_noon.isoformat(), 1000, 'Grand Theft Auto V'
    ))
    
    # Clips in queue
    conn.execute("""
        INSERT INTO clip_queue (clip_id, streamer, score, queued_at, status, clip_data)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        'queued1', 'xQc', 0.85, now.isoformat(), 'pending',
        json.dumps({'id': 'queued1', 'url': 'https://example.com', 'title': 'Queued Clip'})
    ))
    
    conn.execute("""
        INSERT INTO clip_queue (clip_id, streamer, score, queued_at, status, clip_data)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        'queued2', 'Lirik', 0.75, (now - timedelta(days=4)).isoformat(), 'expired',
        json.dumps({'id': 'queued2', 'url': 'https://example.com', 'title': 'Expired Clip'})
    ))
    
    # Failed upload
    conn.execute("""
        INSERT INTO clips (
            clip_id, streamer, title, fail_count, last_failed_at
        ) VALUES (?, ?, ?, ?, ?)
    """, (
        'failed1', 'xQc', 'Failed Upload', 3, now.isoformat()
    ))
    
    # Pipeline run
    conn.execute("""
        INSERT INTO pipeline_runs (
            started_at, finished_at, total_uploaded
        ) VALUES (?, ?, ?)
    """, (
        (now - timedelta(hours=2)).isoformat(),
        (now - timedelta(hours=1)).isoformat(),
        2
    ))
    
    conn.commit()


def populate_test_data_minimal(conn: sqlite3.Connection):
    """Populate database with minimal test data (new channel scenario)."""
    now = datetime.now(UTC)
    
    # Just one clip, no analytics yet
    conn.execute("""
        INSERT INTO clips (
            clip_id, streamer, title, youtube_id, posted_at
        ) VALUES (?, ?, ?, ?, ?)
    """, (
        'clip1', 'TheBurntPeanut', 'First Upload', 'yt123',
        now.isoformat()
    ))
    
    conn.commit()


class TestHelperFunctions:
    """Test helper/utility functions."""
    
    def test_format_youtube_url(self):
        url = format_youtube_url('abc123')
        assert url == 'https://youtube.com/shorts/abc123'
    
    def test_format_time_ago_days(self):
        three_days_ago = (datetime.now(UTC) - timedelta(days=3)).isoformat()
        result = format_time_ago(three_days_ago)
        assert result == '3d ago'
    
    def test_format_time_ago_hours(self):
        five_hours_ago = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
        result = format_time_ago(five_hours_ago)
        assert result == '5h ago'
    
    def test_format_time_ago_minutes(self):
        thirty_mins_ago = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
        result = format_time_ago(thirty_mins_ago)
        assert result == '30m ago'
    
    def test_format_time_ago_invalid(self):
        result = format_time_ago('invalid-timestamp')
        assert result == 'unknown'


class TestLoadFunctions:
    """Test config and cache loading functions."""
    
    def test_load_config(self, tmp_path, sample_config):
        config_file = tmp_path / "config.yaml"
        with open(config_file, 'w') as f:
            yaml.dump(sample_config, f)
        
        result = load_config(str(config_file))
        assert result == sample_config
        assert len(result['streamers']) == 3
    
    def test_load_trending_cache_exists(self, tmp_path, sample_trending_cache):
        cache_file = tmp_path / "trending_cache.json"
        with open(cache_file, 'w') as f:
            json.dump(sample_trending_cache, f)
        
        result = load_trending_cache(str(cache_file))
        assert result is not None
        assert len(result['games']) == 5
        assert result['games'][0]['name'] == 'League of Legends'
    
    def test_load_trending_cache_missing(self, tmp_path):
        cache_file = tmp_path / "nonexistent.json"
        result = load_trending_cache(str(cache_file))
        assert result is None
    
    def test_load_trending_cache_invalid_json(self, tmp_path):
        cache_file = tmp_path / "invalid.json"
        cache_file.write_text("not valid json{")
        result = load_trending_cache(str(cache_file))
        assert result is None


class TestUploadSummary:
    """Test upload summary section."""
    
    def test_upload_summary_with_uploads(self, conn):
        populate_test_data_full(conn)
        result = get_upload_summary(conn)
        
        # At least 2 uploads (clip1, clip2), possibly 3 if yesterday clip is within 24h window
        assert result['count'] >= 2
        assert len(result['uploads']) >= 2
        assert result['uploads'][0]['streamer'] in ['TheBurntPeanut', 'xQc']
        assert result['uploads'][0]['youtube_id'] in ['yt123', 'yt456', 'yt999']
    
    def test_upload_summary_no_uploads(self, conn):
        # Empty database
        result = get_upload_summary(conn)
        
        assert result['count'] == 0
        assert result['uploads'] == []


class TestAnalyticsSnapshot:
    """Test analytics snapshot section."""
    
    def test_analytics_with_full_data(self, conn):
        populate_test_data_full(conn)
        result = get_analytics_snapshot(conn)
        
        assert result['total_views'] == 10000  # 1500 + 2500 + 5000 + 1000
        assert result['total_shorts'] == 4
        assert result['avg_views'] == 2500  # 10000 / 4
        assert result['avg_retention'] is not None
        assert 65.0 <= result['avg_retention'] <= 80.0
        
        assert result['best_short'] is not None
        assert result['best_short']['views'] == 5000
        assert result['best_short']['title'] == 'LEGENDARY moment'
    
    def test_analytics_no_data(self, conn):
        result = get_analytics_snapshot(conn)
        
        assert result['total_views'] == 0
        assert result['total_shorts'] == 0
        assert result['avg_views'] == 0
        assert result['avg_retention'] is None
        assert result['best_short'] is None
    
    def test_analytics_no_retention_data(self, conn):
        # Insert clip without retention data
        conn.execute("""
            INSERT INTO clips (clip_id, streamer, title, youtube_id, posted_at, yt_views)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ('clip1', 'Test', 'Title', 'yt1', datetime.now(UTC).isoformat(), 100))
        conn.commit()
        
        result = get_analytics_snapshot(conn)
        assert result['avg_retention'] is None


class TestPipelineHealth:
    """Test pipeline health section."""
    
    def test_pipeline_health_full_data(self, conn):
        populate_test_data_full(conn)
        result = get_pipeline_health(conn)
        
        assert result['total_clips'] == 5  # 4 regular + 1 failed
        assert result['uploaded_clips'] == 4
        assert result['queued_clips'] == 2
        assert result['queue_pending'] == 1
        assert result['queue_expired'] == 1
        assert result['last_run_time'] is not None
        assert result['failed_uploads_24h'] == 1
    
    def test_pipeline_health_minimal_data(self, conn):
        populate_test_data_minimal(conn)
        result = get_pipeline_health(conn)
        
        assert result['total_clips'] == 1
        assert result['uploaded_clips'] == 1
        assert result['queued_clips'] == 0
        assert result['queue_pending'] == 0
        assert result['queue_expired'] == 0
        assert result['last_run_time'] is None
        assert result['failed_uploads_24h'] == 0


class TestTrendingGames:
    """Test trending games section."""
    
    def test_trending_games_with_cache(self, sample_config, sample_trending_cache):
        result = get_trending_games_info(sample_config, sample_trending_cache)
        
        assert len(result['games']) == 5
        assert result['games'][0]['name'] == 'League of Legends'
        assert result['games'][0]['rank'] == 1
        assert 'streamer_names' in result
    
    def test_trending_games_no_cache(self, sample_config):
        result = get_trending_games_info(sample_config, None)
        
        assert result['games'] == []
        assert result['streamer_games'] == {}
    
    def test_trending_streamers_for_games(self, conn):
        populate_test_data_full(conn)
        
        trending_games = [
            {'name': 'League of Legends', 'rank': 1},
            {'name': 'Valorant', 'rank': 2},
            {'name': 'Minecraft', 'rank': 3},
        ]
        
        result = get_trending_streamers_for_games(conn, trending_games)
        
        assert 'League of Legends' in result
        assert 'TheBurntPeanut' in result['League of Legends']
        assert 'Valorant' in result
        assert 'xQc' in result['Valorant']
    
    def test_trending_streamers_no_games(self, conn):
        result = get_trending_streamers_for_games(conn, [])
        assert result == {}


class TestGrowthMetrics:
    """Test growth metrics section."""
    
    def test_growth_metrics_with_data(self, conn):
        populate_test_data_full(conn)
        result = get_growth_metrics(conn)
        
        # Today's uploads: clip1 (8am) + clip2 (noon) = 4000
        assert result['today_views'] == 4000
        # Yesterday's upload: clip4 (noon yesterday) = 1000
        assert result['yesterday_views'] == 1000
        # Week uploads - at least 2 from today
        assert result['this_week_uploads'] >= 2
        assert result['last_week_uploads'] >= 0
    
    def test_growth_metrics_no_data(self, conn):
        result = get_growth_metrics(conn)
        
        assert result['today_views'] == 0
        assert result['yesterday_views'] == 0
        assert result['this_week_uploads'] == 0
        assert result['last_week_uploads'] == 0


class TestFullReport:
    """Test full report generation."""
    
    def test_generate_report_full_data(self, tmp_path, conn, sample_config, sample_trending_cache):
        # Setup files
        db_path = tmp_path / "clips.db"
        config_path = tmp_path / "config.yaml"
        
        # Write config
        with open(config_path, 'w') as f:
            yaml.dump(sample_config, f)
        
        # Populate and save database
        populate_test_data_full(conn)
        # Copy to file (SQLite in-memory doesn't support this well, so we'll mock)
        
        with patch('scripts.daily_dashboard.get_db_connection', return_value=conn), \
             patch('scripts.daily_dashboard.load_trending_cache', return_value=sample_trending_cache):
            
            report = generate_report(str(db_path), str(config_path))
            
            # Verify report contains all sections
            assert '📊 ClipFrenzy Daily Dashboard' in report
            assert '🎬 Upload Summary' in report
            assert '📈 Analytics Snapshot' in report
            assert '⚙️ Pipeline Health' in report
            assert '🔥 Trending Games' in report
            assert '📊 Growth Metrics' in report
            
            # Verify data is present
            # Upload count may vary (2 or 3) depending on timing
            assert 'short(s) uploaded' in report
            assert 'Total channel views: 10,000' in report
            assert 'League of Legends' in report
    
    def test_generate_report_minimal_data(self, tmp_path, conn, sample_config):
        db_path = tmp_path / "clips.db"
        config_path = tmp_path / "config.yaml"
        
        with open(config_path, 'w') as f:
            yaml.dump(sample_config, f)
        
        populate_test_data_minimal(conn)
        
        with patch('scripts.daily_dashboard.get_db_connection', return_value=conn), \
             patch('scripts.daily_dashboard.load_trending_cache', return_value=None):
            
            report = generate_report(str(db_path), str(config_path))
            
            # Should still have all sections
            assert '📊 ClipFrenzy Daily Dashboard' in report
            assert '🎬 Upload Summary' in report
            assert '📈 Analytics Snapshot' in report
            
            # Should handle missing data gracefully
            assert '1 short(s) uploaded' in report
            assert 'No trending data available' in report
    
    def test_generate_report_sections_render_correctly(self, tmp_path, conn, sample_config):
        """Verify each section renders with proper formatting."""
        config_path = tmp_path / "config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(sample_config, f)
        
        populate_test_data_full(conn)
        
        with patch('scripts.daily_dashboard.get_db_connection', return_value=conn):
            report = generate_report("dummy.db", str(config_path))
            
            # Check emoji are present
            assert '📊' in report
            assert '🎬' in report
            assert '📈' in report
            assert '⚙️' in report
            
            # Check bullet points
            assert '  •' in report
            
            # Check URLs are formatted
            assert 'https://youtube.com/shorts/' in report
            
            # Check numbers are formatted with commas
            assert ':,}' not in report  # No unformatted numbers
            assert '10,000' in report or '5,000' in report

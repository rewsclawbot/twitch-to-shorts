"""Tests for automated streamer discovery module."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.twitch_client import TwitchClient
from scripts.discover_streamers import (
    analyze_streamer,
    score_streamer,
    generate_yaml_config,
    discover_streamers,
)


@pytest.fixture
def mock_client():
    """Create a mock TwitchClient."""
    client = MagicMock(spec=TwitchClient)
    return client


@pytest.fixture
def sample_clips():
    """Sample clip data for testing."""
    from src.models import Clip
    
    return [
        Clip(
            id=f"clip{i}",
            url=f"https://clips.twitch.tv/clip{i}",
            title=f"Clip {i}",
            view_count=1000 + i * 100,
            created_at="2024-01-01T00:00:00Z",
            duration=20.0 + i,
            game_id=f"game{i % 3}",
        )
        for i in range(20)
    ]


class TestAnalyzeStreamer:
    def test_returns_none_for_insufficient_clips(self, mock_client):
        """Should return None if streamer has fewer than 5 clips."""
        from src.models import Clip
        
        mock_client.fetch_clips.return_value = [
            Clip(
                id="clip1",
                url="https://clips.twitch.tv/clip1",
                title="Test",
                view_count=1000,
                created_at="2024-01-01T00:00:00Z",
                duration=20.0,
                game_id="game1",
            )
        ]
        
        result = analyze_streamer(mock_client, "user123", "testuser", "TestUser")
        assert result is None
    
    def test_analyzes_streamer_with_sufficient_clips(self, mock_client, sample_clips):
        """Should analyze streamer metrics from clips."""
        mock_client.fetch_clips.return_value = sample_clips
        mock_client.get_game_names.return_value = {
            "game0": "Game Zero",
            "game1": "Game One",
            "game2": "Game Two",
        }
        
        result = analyze_streamer(mock_client, "user123", "testuser", "TestUser")
        
        assert result is not None
        assert result["user_id"] == "user123"
        assert result["user_login"] == "testuser"
        assert result["user_name"] == "TestUser"
        assert result["clip_count"] == 20
        assert result["avg_views"] > 0
        assert result["max_views"] > 0
        assert result["unique_games"] == 3
        assert len(result["top_games"]) <= 3
        assert result["clip_frequency"] > 0
        assert 0 <= result["shorts_ready_pct"] <= 100
    
    def test_calculates_shorts_ready_percentage(self, mock_client):
        """Should correctly calculate percentage of shorts-ready clips."""
        from src.models import Clip
        
        clips = [
            Clip(id=f"c{i}", url="", title="", view_count=100, 
                 created_at="2024-01-01T00:00:00Z", 
                 duration=15.0 if i < 7 else 45.0, game_id="g1")
            for i in range(10)
        ]
        
        mock_client.fetch_clips.return_value = clips
        mock_client.get_game_names.return_value = {"g1": "TestGame"}
        
        result = analyze_streamer(mock_client, "u1", "user", "User")
        
        # 7 out of 10 clips are ≤30s
        assert result["shorts_ready_pct"] == 70.0


class TestScoreStreamer:
    def test_scores_excellent_streamer_highly(self):
        """Should give high score to streamer with excellent metrics."""
        data = {
            "clip_frequency": 15.0,  # 15 clips/day
            "avg_views": 3000.0,     # High avg views
            "max_views": 80000,      # Very viral clip
            "unique_games": 6,       # Good diversity
            "shorts_ready_pct": 80.0,  # Most clips shorts-ready
        }
        
        score = score_streamer(data)
        assert score >= 90  # Should get high score
    
    def test_scores_mediocre_streamer_moderately(self):
        """Should give moderate score to streamer with average metrics."""
        data = {
            "clip_frequency": 5.0,   # Decent clip rate
            "avg_views": 800.0,      # Moderate views
            "max_views": 15000,      # Some virality
            "unique_games": 2,       # Limited diversity
            "shorts_ready_pct": 45.0,  # Some shorts-ready
        }
        
        score = score_streamer(data)
        assert 30 <= score <= 70  # Should get moderate score
    
    def test_scores_poor_streamer_lowly(self):
        """Should give low score to streamer with poor metrics."""
        data = {
            "clip_frequency": 1.0,   # Few clips
            "avg_views": 100.0,      # Low views
            "max_views": 500,        # No virality
            "unique_games": 1,       # No diversity
            "shorts_ready_pct": 10.0,  # Few shorts-ready
        }
        
        score = score_streamer(data)
        assert score <= 30  # Should get low score
    
    def test_caps_score_at_100(self):
        """Should cap score at 100 even with exceptional metrics."""
        data = {
            "clip_frequency": 100.0,  # Unrealistic
            "avg_views": 50000.0,     # Unrealistic
            "max_views": 1000000,     # Unrealistic
            "unique_games": 50,       # Unrealistic
            "shorts_ready_pct": 100.0,  # Perfect
        }
        
        score = score_streamer(data)
        assert score == 100.0


class TestGenerateYamlConfig:
    def test_generates_valid_yaml(self):
        """Should generate valid YAML config block."""
        data = {
            "user_id": "123456",
            "user_login": "testuser",
            "user_name": "TestUser",
            "clip_count": 50,
            "avg_views": 1500.5,
            "unique_games": 3,
            "top_games": ["Game1", "Game2", "Game3"],
        }
        
        yaml = generate_yaml_config(data)
        
        assert "name: TestUser" in yaml
        assert 'twitch_id: "123456"' in yaml
        assert "youtube_credentials: credentials-testuser.json" in yaml
        assert "x: 0.75" in yaml
        assert "y: 0.02" in yaml
        assert "w: 0.22" in yaml
        assert "h: 0.30" in yaml
        assert "50 clips/week" in yaml
        assert "1500 avg views" in yaml
        assert "3 games" in yaml
    
    def test_includes_top_games_comment(self):
        """Should include top games in comment."""
        data = {
            "user_id": "123",
            "user_login": "user",
            "user_name": "User",
            "clip_count": 10,
            "avg_views": 500.0,
            "unique_games": 2,
            "top_games": ["Fortnite", "Valorant"],
        }
        
        yaml = generate_yaml_config(data)
        assert "Fortnite, Valorant" in yaml


class TestDiscoverStreamers:
    def test_finds_streamers_in_viewer_range(self, mock_client):
        """Should filter streamers by viewer count range."""
        mock_client.get_top_games.return_value = [
            {"id": "game1", "name": "Game1", "rank": 1}
        ]
        
        mock_client.get_streams.return_value = [
            {
                "user_id": "u1",
                "user_login": "user1",
                "user_name": "User1",
                "game_id": "game1",
                "game_name": "Game1",
                "viewer_count": 500,  # Below min
                "started_at": "2024-01-01T00:00:00Z",
                "language": "en",
                "title": "Test",
            },
            {
                "user_id": "u2",
                "user_login": "user2",
                "user_name": "User2",
                "game_id": "game1",
                "game_name": "Game1",
                "viewer_count": 5000,  # In range
                "started_at": "2024-01-01T00:00:00Z",
                "language": "en",
                "title": "Test",
            },
            {
                "user_id": "u3",
                "user_login": "user3",
                "user_name": "User3",
                "game_id": "game1",
                "game_name": "Game1",
                "viewer_count": 100000,  # Above max
                "started_at": "2024-01-01T00:00:00Z",
                "language": "en",
                "title": "Test",
            },
        ]
        
        # Mock sufficient clips for u2
        from src.models import Clip
        clips = [
            Clip(id=f"c{i}", url="", title="", view_count=1000, 
                 created_at="2024-01-01T00:00:00Z", duration=20.0, game_id="g1")
            for i in range(10)
        ]
        mock_client.fetch_clips.return_value = clips
        mock_client.get_game_names.return_value = {"g1": "Game1"}
        
        results = discover_streamers(
            mock_client,
            min_viewers=1000,
            max_viewers=50000,
            max_results=10,
        )
        
        # Should only find u2
        assert len(results) == 1
        assert results[0]["user_id"] == "u2"
    
    def test_filters_by_game_name(self, mock_client):
        """Should filter streams by specific game."""
        mock_client.get_top_games.return_value = [
            {"id": "game1", "name": "Fortnite", "rank": 1},
            {"id": "game2", "name": "Valorant", "rank": 2},
        ]
        
        mock_client.get_streams.return_value = []
        
        discover_streamers(
            mock_client,
            game_name="Fortnite",
            min_viewers=1000,
            max_viewers=50000,
        )
        
        # Should only request streams for Fortnite
        mock_client.get_streams.assert_called_once_with(game_id="game1", first=100)
    
    def test_returns_empty_for_unknown_game(self, mock_client):
        """Should return empty list for unknown game."""
        mock_client.get_top_games.return_value = [
            {"id": "game1", "name": "Fortnite", "rank": 1}
        ]
        
        results = discover_streamers(
            mock_client,
            game_name="UnknownGame",
            min_viewers=1000,
            max_viewers=50000,
        )
        
        assert results == []
    
    def test_deduplicates_streamers(self, mock_client):
        """Should not analyze same streamer multiple times."""
        mock_client.get_top_games.return_value = [
            {"id": "game1", "name": "Game1", "rank": 1},
            {"id": "game2", "name": "Game2", "rank": 2},
        ]
        
        # Same streamer appears in multiple games
        stream_data = {
            "user_id": "u1",
            "user_login": "user1",
            "user_name": "User1",
            "game_id": "game1",
            "game_name": "Game1",
            "viewer_count": 5000,
            "started_at": "2024-01-01T00:00:00Z",
            "language": "en",
            "title": "Test",
        }
        
        mock_client.get_streams.side_effect = [
            [stream_data],  # First game
            [stream_data],  # Second game (same streamer)
        ]
        
        from src.models import Clip
        clips = [
            Clip(id=f"c{i}", url="", title="", view_count=1000, 
                 created_at="2024-01-01T00:00:00Z", duration=20.0, game_id="g1")
            for i in range(10)
        ]
        mock_client.fetch_clips.return_value = clips
        mock_client.get_game_names.return_value = {"g1": "Game1"}
        
        results = discover_streamers(
            mock_client,
            min_viewers=1000,
            max_viewers=50000,
        )
        
        # Should only analyze once
        assert len(results) == 1
        assert mock_client.fetch_clips.call_count == 1
    
    def test_sorts_by_score_descending(self, mock_client):
        """Should return results sorted by score (highest first)."""
        mock_client.get_top_games.return_value = [
            {"id": "game1", "name": "Game1", "rank": 1}
        ]
        
        mock_client.get_streams.return_value = [
            {
                "user_id": f"u{i}",
                "user_login": f"user{i}",
                "user_name": f"User{i}",
                "game_id": "game1",
                "game_name": "Game1",
                "viewer_count": 5000,
                "started_at": "2024-01-01T00:00:00Z",
                "language": "en",
                "title": "Test",
            }
            for i in range(3)
        ]
        
        from src.models import Clip
        
        # Return different clip metrics for each streamer
        def mock_fetch_clips(user_id, **kwargs):
            # u0: poor metrics, u1: excellent metrics, u2: good metrics
            if user_id == "u0":
                view_multiplier = 1
            elif user_id == "u1":
                view_multiplier = 10
            else:  # u2
                view_multiplier = 5
            
            return [
                Clip(id=f"c{i}", url="", title="", 
                     view_count=100 * view_multiplier, 
                     created_at="2024-01-01T00:00:00Z", 
                     duration=20.0, game_id="g1")
                for i in range(10)
            ]
        
        mock_client.fetch_clips.side_effect = mock_fetch_clips
        mock_client.get_game_names.return_value = {"g1": "Game1"}
        
        results = discover_streamers(
            mock_client,
            min_viewers=1000,
            max_viewers=50000,
        )
        
        # Should be sorted: u1 (best), u2 (good), u0 (poor)
        assert len(results) == 3
        assert results[0]["user_id"] == "u1"
        assert results[1]["user_id"] == "u2"
        assert results[2]["user_id"] == "u0"
        assert results[0]["score"] > results[1]["score"] > results[2]["score"]


class TestTwitchClientExtensions:
    """Test new TwitchClient methods."""
    
    @patch("src.twitch_client.requests.request")
    @patch("src.twitch_client.requests.post")
    def test_get_streams(self, mock_post, mock_request):
        """Should fetch live streams."""
        # Mock token
        token_resp = MagicMock()
        token_resp.status_code = 200
        token_resp.json.return_value = {"access_token": "token123", "expires_in": 3600}
        token_resp.raise_for_status = MagicMock()
        mock_post.return_value = token_resp
        
        # Mock streams response
        streams_resp = MagicMock()
        streams_resp.status_code = 200
        streams_resp.json.return_value = {
            "data": [
                {
                    "user_id": "123",
                    "user_login": "testuser",
                    "user_name": "TestUser",
                    "game_id": "456",
                    "game_name": "TestGame",
                    "viewer_count": 5000,
                    "started_at": "2024-01-01T00:00:00Z",
                    "language": "en",
                    "title": "Test Stream",
                }
            ]
        }
        streams_resp.raise_for_status = MagicMock()
        mock_request.return_value = streams_resp
        
        client = TwitchClient("client_id", "client_secret")
        streams = client.get_streams(first=20)
        
        assert len(streams) == 1
        assert streams[0]["user_id"] == "123"
        assert streams[0]["user_login"] == "testuser"
        assert streams[0]["viewer_count"] == 5000
    
    @patch("src.twitch_client.requests.request")
    @patch("src.twitch_client.requests.post")
    def test_get_streams_with_game_filter(self, mock_post, mock_request):
        """Should filter streams by game ID."""
        # Mock token
        token_resp = MagicMock()
        token_resp.status_code = 200
        token_resp.json.return_value = {"access_token": "token123", "expires_in": 3600}
        token_resp.raise_for_status = MagicMock()
        mock_post.return_value = token_resp
        
        # Mock streams response
        streams_resp = MagicMock()
        streams_resp.status_code = 200
        streams_resp.json.return_value = {"data": []}
        streams_resp.raise_for_status = MagicMock()
        mock_request.return_value = streams_resp
        
        client = TwitchClient("client_id", "client_secret")
        client.get_streams(game_id="456", first=20)
        
        # Verify game_id was passed in params
        call_kwargs = mock_request.call_args[1]
        assert "params" in call_kwargs
        assert call_kwargs["params"]["game_id"] == "456"
    
    def test_get_streams_validates_first_parameter(self):
        """Should validate first parameter range."""
        client = TwitchClient("client_id", "client_secret")
        
        with pytest.raises(ValueError, match="first must be between 1 and 100"):
            client.get_streams(first=0)
        
        with pytest.raises(ValueError, match="first must be between 1 and 100"):
            client.get_streams(first=101)
    
    @patch("src.twitch_client.requests.request")
    @patch("src.twitch_client.requests.post")
    def test_get_user_by_login(self, mock_post, mock_request):
        """Should fetch user info by login."""
        # Mock token
        token_resp = MagicMock()
        token_resp.status_code = 200
        token_resp.json.return_value = {"access_token": "token123", "expires_in": 3600}
        token_resp.raise_for_status = MagicMock()
        mock_post.return_value = token_resp
        
        # Mock user response
        user_resp = MagicMock()
        user_resp.status_code = 200
        user_resp.json.return_value = {
            "data": [
                {
                    "id": "123",
                    "login": "testuser",
                    "display_name": "TestUser",
                    "profile_image_url": "https://example.com/image.png",
                    "view_count": 10000,
                }
            ]
        }
        user_resp.raise_for_status = MagicMock()
        mock_request.return_value = user_resp
        
        client = TwitchClient("client_id", "client_secret")
        user = client.get_user_by_login("testuser")
        
        assert user is not None
        assert user["id"] == "123"
        assert user["login"] == "testuser"
        assert user["display_name"] == "TestUser"
        assert user["view_count"] == 10000
    
    @patch("src.twitch_client.requests.request")
    @patch("src.twitch_client.requests.post")
    def test_get_user_by_login_returns_none_if_not_found(self, mock_post, mock_request):
        """Should return None if user not found."""
        # Mock token
        token_resp = MagicMock()
        token_resp.status_code = 200
        token_resp.json.return_value = {"access_token": "token123", "expires_in": 3600}
        token_resp.raise_for_status = MagicMock()
        mock_post.return_value = token_resp
        
        # Mock empty user response
        user_resp = MagicMock()
        user_resp.status_code = 200
        user_resp.json.return_value = {"data": []}
        user_resp.raise_for_status = MagicMock()
        mock_request.return_value = user_resp
        
        client = TwitchClient("client_id", "client_secret")
        user = client.get_user_by_login("nonexistent")
        
        assert user is None

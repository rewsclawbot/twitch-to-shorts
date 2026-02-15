"""Tests for trending games detection and caching."""

import json
import os
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.trending import (
    CACHE_FILE,
    CACHE_TTL_SECONDS,
    get_trending_games,
    get_trending_multiplier,
    get_trending_multipliers,
)
from tests.conftest import make_clip


@pytest.fixture
def mock_twitch_client():
    """Mock TwitchClient with get_top_games method."""
    client = MagicMock()
    client.get_top_games.return_value = [
        {"id": "1", "name": "League of Legends", "rank": 1},
        {"id": "2", "name": "Fortnite", "rank": 2},
        {"id": "3", "name": "Valorant", "rank": 3},
        {"id": "4", "name": "Minecraft", "rank": 4},
        {"id": "5", "name": "Apex Legends", "rank": 5},
        {"id": "6", "name": "Counter-Strike 2", "rank": 6},
        {"id": "7", "name": "Dota 2", "rank": 7},
        {"id": "8", "name": "Overwatch 2", "rank": 8},
        {"id": "9", "name": "Rocket League", "rank": 9},
        {"id": "10", "name": "Call of Duty", "rank": 10},
        {"id": "11", "name": "Grand Theft Auto V", "rank": 11},
        {"id": "12", "name": "World of Warcraft", "rank": 12},
        {"id": "13", "name": "Hearthstone", "rank": 13},
        {"id": "14", "name": "Dead by Daylight", "rank": 14},
        {"id": "15", "name": "Rust", "rank": 15},
        {"id": "16", "name": "Escape from Tarkov", "rank": 16},
        {"id": "17", "name": "PUBG: BATTLEGROUNDS", "rank": 17},
        {"id": "18", "name": "Destiny 2", "rank": 18},
        {"id": "19", "name": "Terraria", "rank": 19},
        {"id": "20", "name": "Stardew Valley", "rank": 20},
    ]
    return client


@pytest.fixture(autouse=True)
def cleanup_cache():
    """Remove cache file before and after each test."""
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
    yield
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)


class TestGetTrendingGames:
    def test_fetches_from_api_when_no_cache(self, mock_twitch_client):
        """First call should fetch from API when cache doesn't exist."""
        games = get_trending_games(mock_twitch_client)
        
        assert len(games) == 20
        assert games[0]["name"] == "League of Legends"
        assert games[0]["rank"] == 1
        assert games[19]["name"] == "Stardew Valley"
        assert games[19]["rank"] == 20
        mock_twitch_client.get_top_games.assert_called_once_with(limit=20)
    
    def test_saves_to_cache(self, mock_twitch_client):
        """Should save fetched games to cache file."""
        get_trending_games(mock_twitch_client)
        
        assert os.path.exists(CACHE_FILE)
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        assert "timestamp" in data
        assert "games" in data
        assert len(data["games"]) == 20
        assert data["games"][0]["name"] == "League of Legends"
    
    def test_uses_cache_when_valid(self, mock_twitch_client):
        """Should use cached data when cache is still valid."""
        # First call populates cache
        get_trending_games(mock_twitch_client)
        mock_twitch_client.get_top_games.reset_mock()
        
        # Second call should use cache
        games = get_trending_games(mock_twitch_client)
        
        assert len(games) == 20
        mock_twitch_client.get_top_games.assert_not_called()
    
    def test_refreshes_cache_when_expired(self, mock_twitch_client):
        """Should fetch new data when cache TTL expires."""
        # Create stale cache
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        stale_data = {
            "timestamp": time.time() - CACHE_TTL_SECONDS - 100,
            "games": [{"id": "999", "name": "Old Game", "rank": 1}],
        }
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(stale_data, f)
        
        # Should fetch fresh data
        games = get_trending_games(mock_twitch_client)
        
        assert len(games) == 20
        assert games[0]["name"] == "League of Legends"
        mock_twitch_client.get_top_games.assert_called_once()
    
    def test_handles_api_failure_gracefully(self, mock_twitch_client):
        """Should return empty list on API failure."""
        mock_twitch_client.get_top_games.side_effect = Exception("API error")
        
        games = get_trending_games(mock_twitch_client)
        
        assert games == []
    
    def test_handles_corrupted_cache(self, mock_twitch_client):
        """Should fetch from API if cache file is corrupted."""
        # Create corrupted cache
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            f.write("not valid json {]")
        
        games = get_trending_games(mock_twitch_client)
        
        assert len(games) == 20
        mock_twitch_client.get_top_games.assert_called_once()


class TestGetTrendingMultiplier:
    def test_top_5_game_gets_1_5x(self, mock_twitch_client):
        """Top 5 ranked games should get 1.5x multiplier."""
        multiplier = get_trending_multiplier("League of Legends", mock_twitch_client)
        assert multiplier == 1.5
        
        multiplier = get_trending_multiplier("Apex Legends", mock_twitch_client)
        assert multiplier == 1.5
    
    def test_top_6_10_game_gets_1_3x(self, mock_twitch_client):
        """Games ranked 6-10 should get 1.3x multiplier."""
        multiplier = get_trending_multiplier("Counter-Strike 2", mock_twitch_client)
        assert multiplier == 1.3
        
        multiplier = get_trending_multiplier("Call of Duty", mock_twitch_client)
        assert multiplier == 1.3
    
    def test_top_11_20_game_gets_1_15x(self, mock_twitch_client):
        """Games ranked 11-20 should get 1.15x multiplier."""
        multiplier = get_trending_multiplier("Grand Theft Auto V", mock_twitch_client)
        assert multiplier == 1.15
        
        multiplier = get_trending_multiplier("Stardew Valley", mock_twitch_client)
        assert multiplier == 1.15
    
    def test_non_trending_game_gets_1_0x(self, mock_twitch_client):
        """Games not in top 20 should get 1.0x multiplier."""
        multiplier = get_trending_multiplier("Some Unknown Game", mock_twitch_client)
        assert multiplier == 1.0
    
    def test_empty_game_name_gets_1_0x(self, mock_twitch_client):
        """Empty game name should get 1.0x multiplier."""
        assert get_trending_multiplier("", mock_twitch_client) == 1.0
        assert get_trending_multiplier(None, mock_twitch_client) == 1.0
    
    def test_case_insensitive_matching(self, mock_twitch_client):
        """Should match game names case-insensitively."""
        assert get_trending_multiplier("LEAGUE OF LEGENDS", mock_twitch_client) == 1.5
        assert get_trending_multiplier("league of legends", mock_twitch_client) == 1.5
        assert get_trending_multiplier("LeAgUe Of LeGeNdS", mock_twitch_client) == 1.5
    
    def test_whitespace_handling(self, mock_twitch_client):
        """Should handle whitespace in game names."""
        assert get_trending_multiplier("  League of Legends  ", mock_twitch_client) == 1.5


class TestGetTrendingMultipliers:
    def test_returns_dict_of_all_multipliers(self, mock_twitch_client):
        """Should return a dict mapping all trending games to multipliers."""
        multipliers = get_trending_multipliers(mock_twitch_client)
        
        assert len(multipliers) == 20
        assert multipliers["League of Legends"] == 1.5
        assert multipliers["Apex Legends"] == 1.5
        assert multipliers["Counter-Strike 2"] == 1.3
        assert multipliers["Call of Duty"] == 1.3
        assert multipliers["Grand Theft Auto V"] == 1.15
        assert multipliers["Stardew Valley"] == 1.15
    
    def test_efficient_batch_lookup(self, mock_twitch_client):
        """Should only call API once for batch lookup."""
        multipliers = get_trending_multipliers(mock_twitch_client)
        mock_twitch_client.get_top_games.assert_called_once()
        
        # Verify all expected games are present
        assert "League of Legends" in multipliers
        assert "Stardew Valley" in multipliers


class TestIntegrationWithClipScoring:
    """Test integration with clip_filter.compute_score."""
    
    def test_trending_multiplier_applied_to_score(self, mock_twitch_client):
        """Clips from trending games should get boosted scores."""
        from src.clip_filter import compute_score
        
        trending_multipliers = get_trending_multipliers(mock_twitch_client)
        
        # Create two identical clips with different games
        clip_trending = make_clip(
            view_count=1000,
            duration=20,
            game_name="League of Legends",
        )
        clip_normal = make_clip(
            view_count=1000,
            duration=20,
            game_name="Unknown Game",
        )
        
        score_trending = compute_score(
            clip_trending,
            trending_multipliers=trending_multipliers,
        )
        score_normal = compute_score(
            clip_normal,
            trending_multipliers=trending_multipliers,
        )
        
        # Trending clip should have 1.5x score
        assert score_trending > score_normal
        assert abs(score_trending / score_normal - 1.5) < 0.01
    
    def test_trending_works_with_game_multipliers(self, mock_twitch_client):
        """Trending and game multipliers should stack."""
        from src.clip_filter import compute_score
        
        trending_multipliers = get_trending_multipliers(mock_twitch_client)
        game_multipliers = {"League of Legends": 2.0}
        
        clip = make_clip(
            view_count=1000,
            duration=20,
            game_name="League of Legends",
        )
        
        # With only trending
        score_trending = compute_score(
            clip,
            trending_multipliers=trending_multipliers,
        )
        
        # With both trending and game multipliers
        score_both = compute_score(
            clip,
            game_multipliers=game_multipliers,
            trending_multipliers=trending_multipliers,
        )
        
        # Should apply both: base * 2.0 (game) * 1.5 (trending)
        assert score_both > score_trending
        assert abs(score_both / score_trending - 2.0) < 0.01
    
    def test_no_trending_multipliers_still_works(self):
        """compute_score should work when trending_multipliers is None."""
        from src.clip_filter import compute_score
        
        clip = make_clip(view_count=1000, duration=20)
        score = compute_score(clip, trending_multipliers=None)
        
        assert score > 0


class TestTwitchClientGetTopGames:
    """Test the new get_top_games method on TwitchClient."""
    
    @patch("src.twitch_client.requests.request")
    @patch("src.twitch_client.requests.post")
    def test_get_top_games_success(self, mock_post, mock_request):
        """Should successfully fetch top games from Twitch API."""
        from src.twitch_client import TwitchClient
        
        # Mock token response
        token_resp = MagicMock()
        token_resp.status_code = 200
        token_resp.json.return_value = {"access_token": "test_token", "expires_in": 3600}
        mock_post.return_value = token_resp
        
        # Mock games response
        games_resp = MagicMock()
        games_resp.status_code = 200
        games_resp.json.return_value = {
            "data": [
                {"id": "1", "name": "League of Legends"},
                {"id": "2", "name": "Fortnite"},
                {"id": "3", "name": "Valorant"},
            ]
        }
        games_resp.headers = {}
        games_resp.raise_for_status = MagicMock()
        mock_request.return_value = games_resp
        
        client = TwitchClient("test_id", "test_secret")
        games = client.get_top_games(limit=3)
        
        assert len(games) == 3
        assert games[0] == {"id": "1", "name": "League of Legends", "rank": 1}
        assert games[1] == {"id": "2", "name": "Fortnite", "rank": 2}
        assert games[2] == {"id": "3", "name": "Valorant", "rank": 3}
        
        # Verify API call
        mock_request.assert_called_once()
        call_kwargs = mock_request.call_args[1]
        assert call_kwargs["params"]["first"] == 3
    
    def test_get_top_games_limit_validation(self):
        """Should validate limit parameter."""
        from src.twitch_client import TwitchClient
        
        client = TwitchClient("test_id", "test_secret")
        
        with pytest.raises(ValueError, match="limit must be between 1 and 100"):
            client.get_top_games(limit=0)
        
        with pytest.raises(ValueError, match="limit must be between 1 and 100"):
            client.get_top_games(limit=101)
    
    @patch("src.twitch_client.requests.request")
    @patch("src.twitch_client.requests.post")
    def test_get_top_games_empty_response(self, mock_post, mock_request):
        """Should handle empty API response gracefully."""
        from src.twitch_client import TwitchClient
        
        # Mock token
        token_resp = MagicMock()
        token_resp.status_code = 200
        token_resp.json.return_value = {"access_token": "test_token", "expires_in": 3600}
        mock_post.return_value = token_resp
        
        # Mock empty games response
        games_resp = MagicMock()
        games_resp.status_code = 200
        games_resp.json.return_value = {"data": []}
        games_resp.headers = {}
        games_resp.raise_for_status = MagicMock()
        mock_request.return_value = games_resp
        
        client = TwitchClient("test_id", "test_secret")
        games = client.get_top_games(limit=20)
        
        assert games == []

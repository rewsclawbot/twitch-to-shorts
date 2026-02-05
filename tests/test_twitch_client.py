"""Tests for TwitchClient: token refresh, rate limiting, pagination, backoff, TLS verification."""

import time
from unittest.mock import patch, MagicMock, call

import pytest
import requests

from src.twitch_client import TwitchClient, TOKEN_URL, CLIPS_URL, GAMES_URL


def _make_token_response(token="test_token", expires_in=3600):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 200
    resp.json.return_value = {"access_token": token, "expires_in": expires_in}
    resp.raise_for_status = MagicMock()
    return resp


def _make_response(status_code=200, json_data=None, headers=None):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.headers = headers or {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(response=resp)
    return resp


class TestGetToken:
    @patch("src.twitch_client.requests.post")
    def test_fetches_token_on_first_call(self, mock_post):
        mock_post.return_value = _make_token_response(token="abc123")
        client = TwitchClient("id", "secret")
        token = client._get_token()
        assert token == "abc123"
        mock_post.assert_called_once()
        # Verify client_secret is in POST data, not params
        call_kwargs = mock_post.call_args
        assert call_kwargs[1]["data"]["client_secret"] == "secret"

    @patch("src.twitch_client.requests.post")
    def test_caches_token(self, mock_post):
        mock_post.return_value = _make_token_response()
        client = TwitchClient("id", "secret")
        client._get_token()
        client._get_token()
        # Should only call POST once — second call uses cache
        assert mock_post.call_count == 1

    @patch("src.twitch_client.time.sleep")
    @patch("src.twitch_client.time.monotonic")
    @patch("src.twitch_client.requests.post")
    def test_backoff_after_failure(self, mock_post, mock_monotonic, mock_sleep):
        # First call fails, second succeeds
        mock_post.side_effect = [
            requests.ConnectionError("network down"),
            _make_token_response(),
        ]
        # Time sequence: first call at 100.0, failure recorded at 100.0,
        # second call at 100.5 (within 2s backoff window)
        mock_monotonic.side_effect = [100.0, 100.0, 100.5, 100.5, 100.5]

        client = TwitchClient("id", "secret")
        with pytest.raises(requests.ConnectionError):
            client._get_token()

        # Second call should trigger backoff sleep
        token = client._get_token()
        assert token == "test_token"
        mock_sleep.assert_called_once()


class TestRequest:
    @patch("src.twitch_client.requests.request")
    @patch("src.twitch_client.requests.post")
    def test_token_refresh_on_401(self, mock_post, mock_request):
        # Token request succeeds
        mock_post.return_value = _make_token_response(token="token1")
        client = TwitchClient("id", "secret")

        # First API call returns 401, second returns 200
        resp_401 = _make_response(status_code=401)
        resp_401.raise_for_status = MagicMock()  # Don't raise for 401 (handled by retry)
        resp_200 = _make_response(status_code=200, json_data={"data": []})
        mock_request.side_effect = [resp_401, resp_200]

        result = client._request("GET", "https://example.com/api")
        assert result.status_code == 200
        # Token should have been cleared after 401, forcing re-fetch
        assert mock_request.call_count == 2

    @patch("src.twitch_client.time.sleep")
    @patch("src.twitch_client.time.time", return_value=1000)
    @patch("src.twitch_client.requests.request")
    @patch("src.twitch_client.requests.post")
    def test_rate_limit_sleep_on_429(self, mock_post, mock_request, mock_time, mock_sleep):
        mock_post.return_value = _make_token_response()
        client = TwitchClient("id", "secret")

        resp_429 = _make_response(status_code=429, headers={"Ratelimit-Reset": "1005"})
        resp_429.raise_for_status = MagicMock()  # Don't raise for 429
        resp_200 = _make_response(status_code=200, json_data={"data": []})
        mock_request.side_effect = [resp_429, resp_200]

        result = client._request("GET", "https://example.com/api")
        assert result.status_code == 200
        # Should sleep for 5s (1005 - 1000)
        mock_sleep.assert_called_once_with(5)

    @patch("src.twitch_client.time.sleep")
    @patch("src.twitch_client.requests.request")
    @patch("src.twitch_client.requests.post")
    def test_rate_limit_fallback_wait(self, mock_post, mock_request, mock_sleep):
        mock_post.return_value = _make_token_response()
        client = TwitchClient("id", "secret")

        # 429 without valid Ratelimit-Reset header
        resp_429 = _make_response(status_code=429, headers={})
        resp_429.raise_for_status = MagicMock()
        resp_200 = _make_response(status_code=200)
        mock_request.side_effect = [resp_429, resp_200]

        client._request("GET", "https://example.com/api")
        # Should fall back to 5s wait
        mock_sleep.assert_called_once_with(5)

    @patch("src.twitch_client.requests.request")
    @patch("src.twitch_client.requests.post")
    def test_verify_true_enforced(self, mock_post, mock_request):
        mock_post.return_value = _make_token_response()
        mock_request.return_value = _make_response(status_code=200)

        client = TwitchClient("id", "secret")
        client._request("GET", "https://example.com/api")

        call_kwargs = mock_request.call_args
        assert call_kwargs[1]["verify"] is True


class TestFetchClips:
    @patch("src.twitch_client.requests.request")
    @patch("src.twitch_client.requests.post")
    def test_pagination_terminates_on_no_cursor(self, mock_post, mock_request):
        mock_post.return_value = _make_token_response()

        page1 = _make_response(json_data={
            "data": [
                {"id": "c1", "url": "https://clips.twitch.tv/c1", "title": "Clip 1",
                 "view_count": 100, "created_at": "2026-01-01T00:00:00Z", "duration": 30, "game_id": "1"},
            ],
            "pagination": {},  # No cursor = last page
        })
        mock_request.return_value = page1

        client = TwitchClient("id", "secret")
        clips = client.fetch_clips("12345", lookback_hours=24)

        assert len(clips) == 1
        assert clips[0].id == "c1"
        # Should only make one API request (no more pages)
        assert mock_request.call_count == 1

    @patch("src.twitch_client.requests.request")
    @patch("src.twitch_client.requests.post")
    def test_pagination_follows_cursor(self, mock_post, mock_request):
        mock_post.return_value = _make_token_response()

        page1 = _make_response(json_data={
            "data": [
                {"id": "c1", "url": "https://clips.twitch.tv/c1", "title": "Clip 1",
                 "view_count": 100, "created_at": "2026-01-01T00:00:00Z", "duration": 30, "game_id": "1"},
            ],
            "pagination": {"cursor": "page2_cursor"},
        })
        page2 = _make_response(json_data={
            "data": [
                {"id": "c2", "url": "https://clips.twitch.tv/c2", "title": "Clip 2",
                 "view_count": 200, "created_at": "2026-01-01T01:00:00Z", "duration": 25, "game_id": "1"},
            ],
            "pagination": {},
        })
        mock_request.side_effect = [page1, page2]

        client = TwitchClient("id", "secret")
        clips = client.fetch_clips("12345", lookback_hours=24)

        assert len(clips) == 2
        assert mock_request.call_count == 2

    @patch("src.twitch_client.requests.request")
    @patch("src.twitch_client.requests.post")
    def test_max_clips_limit(self, mock_post, mock_request):
        mock_post.return_value = _make_token_response()

        clips_data = [
            {"id": f"c{i}", "url": f"https://clips.twitch.tv/c{i}", "title": f"Clip {i}",
             "view_count": 100, "created_at": "2026-01-01T00:00:00Z", "duration": 30, "game_id": "1"}
            for i in range(10)
        ]
        mock_request.return_value = _make_response(json_data={
            "data": clips_data,
            "pagination": {"cursor": "next"},
        })

        client = TwitchClient("id", "secret")
        clips = client.fetch_clips("12345", max_clips=5)

        assert len(clips) == 5

    @patch("src.twitch_client.requests.request")
    @patch("src.twitch_client.requests.post")
    def test_skips_malformed_clip_data(self, mock_post, mock_request):
        mock_post.return_value = _make_token_response()

        mock_request.return_value = _make_response(json_data={
            "data": [
                {"id": "good", "url": "https://clips.twitch.tv/good", "title": "Good",
                 "view_count": 100, "created_at": "2026-01-01T00:00:00Z", "duration": 30},
                {"id": "bad"},  # Missing required fields
            ],
            "pagination": {},
        })

        client = TwitchClient("id", "secret")
        clips = client.fetch_clips("12345")
        assert len(clips) == 1
        assert clips[0].id == "good"


class TestGetGameNames:
    @patch("src.twitch_client.requests.request")
    @patch("src.twitch_client.requests.post")
    def test_resolves_game_ids(self, mock_post, mock_request):
        mock_post.return_value = _make_token_response()
        mock_request.return_value = _make_response(json_data={
            "data": [
                {"id": "123", "name": "Fortnite"},
                {"id": "456", "name": "Valorant"},
            ],
        })

        client = TwitchClient("id", "secret")
        names = client.get_game_names(["123", "456"])
        assert names == {"123": "Fortnite", "456": "Valorant"}

    @patch("src.twitch_client.requests.request")
    @patch("src.twitch_client.requests.post")
    def test_empty_ids_returns_empty(self, mock_post, mock_request):
        mock_post.return_value = _make_token_response()
        client = TwitchClient("id", "secret")
        assert client.get_game_names([]) == {}
        assert client.get_game_names(["", ""]) == {}
        mock_request.assert_not_called()

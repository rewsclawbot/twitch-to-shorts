"""Tests for engagement module (first comment, pinned comment)."""
from unittest.mock import MagicMock, patch

import pytest

from src.engagement import (
    _render_comment,
    post_first_comment,
)


class TestRenderComment:
    def test_renders_all_placeholders(self):
        template = "{game} - {streamer} - {title}"
        result = _render_comment(template, "Fortnite", "Ninja", "Epic Win")
        assert result == "Fortnite - Ninja - Epic Win"

    def test_renders_with_empty_values(self):
        template = "Playing {game} with {streamer}"
        result = _render_comment(template, "", "", "")
        assert "this game" in result
        assert "the streamer" in result

    def test_renders_with_none_values(self):
        template = "{game} clip from {streamer}"
        result = _render_comment(template, None, None, None)
        assert "this game" in result


class TestPostFirstComment:
    def test_posts_comment_successfully(self):
        service = MagicMock()
        service.commentThreads().insert().execute.return_value = {"id": "comment123"}
        
        result = post_first_comment(service, "video123", "Fortnite", "Ninja", "Epic Win")
        assert result == "comment123"
        service.commentThreads().insert.assert_called()

    def test_returns_none_on_403(self):
        from googleapiclient.errors import HttpError
        from unittest.mock import PropertyMock
        
        service = MagicMock()
        resp = MagicMock()
        resp.status = 403
        error = HttpError(resp, b"forbidden")
        service.commentThreads().insert().execute.side_effect = error
        
        result = post_first_comment(service, "video123")
        assert result is None

    def test_returns_none_on_exception(self):
        service = MagicMock()
        service.commentThreads().insert().execute.side_effect = RuntimeError("boom")
        
        result = post_first_comment(service, "video123")
        assert result is None

    def test_pin_uses_pinned_templates(self):
        service = MagicMock()
        service.commentThreads().insert().execute.return_value = {"id": "c1"}
        
        with patch("src.engagement.random.choice") as mock_choice:
            mock_choice.return_value = "📺 Full credit: {streamer} on Twitch\n🎮 Game: {game}\n💬 Subscribe for daily gaming clips!"
            result = post_first_comment(service, "v1", "Fortnite", "Ninja", pin=True)
            assert result == "c1"
            # Verify pin=True passes pinned templates to random.choice
            called_templates = mock_choice.call_args[0][0]
            assert any("Full credit" in t for t in called_templates)

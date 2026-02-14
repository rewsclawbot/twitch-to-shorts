import random
from unittest.mock import MagicMock, patch

from src.title_optimizer import (
    _rewrite_title_with_llm,
    _should_optimize,
    optimize_title,
)


def test_should_optimize_deterministic():
    clip_id = "deterministic_clip_123"
    first = _should_optimize(clip_id)
    for _ in range(20):
        assert _should_optimize(clip_id) is first


def test_should_optimize_roughly_50_50():
    rng = random.Random(1337)
    sample_size = 1000
    optimized_count = sum(
        _should_optimize(f"clip_{rng.getrandbits(64)}_{i}")
        for i in range(sample_size)
    )
    ratio = optimized_count / sample_size
    assert 0.4 <= ratio <= 0.6


def test_optimize_title_no_api_key():
    with patch.dict("os.environ", {"TITLE_OPTIMIZER_ENABLED": "true"}, clear=True):
        with patch("src.title_optimizer._rewrite_title_with_llm") as mock_rewrite:
            result = optimize_title("Original Twitch Title", "streamer", "game", "clip_treatment")
    assert result == "Original Twitch Title"
    mock_rewrite.assert_not_called()


def test_optimize_title_disabled():
    with patch.dict(
        "os.environ",
        {"TITLE_OPTIMIZER_ENABLED": "false", "OPENAI_API_KEY": "test-key"},
        clear=True,
    ):
        with patch("src.title_optimizer._rewrite_title_with_llm") as mock_rewrite:
            result = optimize_title("Original Twitch Title", "streamer", "game", "clip_treatment")
    assert result == "Original Twitch Title"
    mock_rewrite.assert_not_called()


def test_optimize_title_control_group():
    with patch.dict(
        "os.environ",
        {"TITLE_OPTIMIZER_ENABLED": "true", "OPENAI_API_KEY": "test-key"},
        clear=True,
    ):
        with patch("src.title_optimizer._rewrite_title_with_llm") as mock_rewrite:
            result = optimize_title("Original Twitch Title", "streamer", "game", "clip_control")
    assert result == "Original Twitch Title"
    mock_rewrite.assert_not_called()


def test_optimize_title_treatment_group():
    with patch.dict(
        "os.environ",
        {"TITLE_OPTIMIZER_ENABLED": "true", "OPENAI_API_KEY": "test-key"},
        clear=True,
    ):
        with patch(
            "src.title_optimizer._rewrite_title_with_llm",
            return_value="INSANE FINAL CLUTCH! 🔥",
        ) as mock_rewrite:
            result = optimize_title("lol", "streamer", "game", "clip_treatment")
    assert result == "INSANE FINAL CLUTCH! 🔥"
    mock_rewrite.assert_called_once_with("lol", "streamer", "game")


def test_rewrite_title_with_llm_success():
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content='  "BIG WIN MOMENT! 🔥"\nextra'))]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response

    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True):
        with patch("src.title_optimizer.OpenAI", return_value=mock_client) as mock_openai:
            result = _rewrite_title_with_llm("lol", "streamer", "Valorant")

    assert result == "BIG WIN MOMENT! 🔥"
    mock_openai.assert_called_once_with(api_key="test-key")
    kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-4o-mini"
    assert kwargs["timeout"] == 10
    assert kwargs["messages"][0]["role"] == "system"
    assert "YouTube Shorts" in kwargs["messages"][0]["content"]


def test_rewrite_title_with_llm_failure():
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = RuntimeError("api error")

    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True):
        with patch("src.title_optimizer.OpenAI", return_value=mock_client):
            with patch("src.title_optimizer.time.sleep") as mock_sleep:
                result = _rewrite_title_with_llm("lol", "streamer", "game")

    assert result is None
    assert mock_client.chat.completions.create.call_count == 2
    mock_sleep.assert_called_once_with(2)


def test_rewrite_title_with_llm_timeout():
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = TimeoutError("timed out")

    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True):
        with patch("src.title_optimizer.OpenAI", return_value=mock_client):
            with patch("src.title_optimizer.time.sleep") as mock_sleep:
                result = _rewrite_title_with_llm("lol", "streamer", "game")

    assert result is None
    assert mock_client.chat.completions.create.call_count == 2
    mock_sleep.assert_called_once_with(2)


def test_optimize_title_truncation():
    long_title = "A" * 150
    with patch.dict("os.environ", {}, clear=True):
        result = optimize_title(long_title, "streamer", "game", "clip_1")
    assert len(result) == 100
    assert result.endswith("...")


def test_optimize_title_llm_failure_fallback():
    with patch.dict(
        "os.environ",
        {"TITLE_OPTIMIZER_ENABLED": "true", "OPENAI_API_KEY": "test-key"},
        clear=True,
    ):
        with patch("src.title_optimizer._rewrite_title_with_llm", return_value=None) as mock_rewrite:
            result = optimize_title("Original Twitch Title", "streamer", "game", "clip_treatment")
    assert result == "Original Twitch Title"
    mock_rewrite.assert_called_once_with("Original Twitch Title", "streamer", "game")


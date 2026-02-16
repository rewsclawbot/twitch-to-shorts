from unittest.mock import MagicMock, patch

import src.narrator as narrator
from src.models import Clip, PipelineConfig, StreamerConfig
from src.pipeline import _process_single_clip


def _make_clip() -> Clip:
    return Clip(
        id="clip_1",
        url="https://clips.twitch.tv/clip_1",
        title="Insane clutch play",
        view_count=1200,
        created_at="2026-01-15T12:00:00Z",
        duration=25,
        game_id="1234",
        streamer="TheBurntPeanut",
        game_name="ARC Raiders",
    )


def test_generate_narration_text_returns_string_when_llm_fails(monkeypatch):
    monkeypatch.setattr(narrator, "_call_claude_cli", lambda *_: None)
    monkeypatch.setattr(narrator, "_call_openai", lambda *_: None)

    text = narrator.generate_narration_text(
        "Insane clutch play", "ARC Raiders", "TheBurntPeanut"
    )

    assert isinstance(text, str)


def test_generate_narration_text_returns_non_empty(monkeypatch):
    monkeypatch.setattr(narrator, "_call_claude_cli", lambda *_: None)
    monkeypatch.setattr(narrator, "_call_openai", lambda *_: None)

    text = narrator.generate_narration_text("Big win", "Fortnite", "Streamer")

    assert text.strip() != ""


def test_add_narration_returns_none_when_edge_tts_unavailable(tmp_path, monkeypatch):
    video_path = tmp_path / "input.mp4"
    video_path.write_bytes(b"fake-video")

    monkeypatch.setattr(narrator, "edge_tts", None)

    result = narrator.add_narration(
        str(video_path),
        str(tmp_path),
        "Insane clutch play",
        "ARC Raiders",
        "TheBurntPeanut",
    )

    assert result is None


def test_add_narration_returns_none_for_empty_video_path(tmp_path):
    result = narrator.add_narration(
        "",
        str(tmp_path),
        "Insane clutch play",
        "ARC Raiders",
        "TheBurntPeanut",
    )
    assert result is None


def test_template_fallback_produces_reasonable_text(monkeypatch):
    monkeypatch.setattr(narrator, "_call_claude_cli", lambda *_: None)
    monkeypatch.setattr(narrator, "_call_openai", lambda *_: None)

    text = narrator.generate_narration_text(
        "Insane clutch play",
        "ARC Raiders",
        "TheBurntPeanut",
    )

    assert "ARC Raiders" in text
    lower = text.lower()
    assert "moment you need to see" in lower or "check out this" in lower


def test_generate_narration_text_under_100_chars(monkeypatch):
    long_line = (
        "This is an intentionally long narration line that should be truncated to "
        "stay under one hundred characters for short intro timing."
    )
    monkeypatch.setattr(narrator, "_call_claude_cli", lambda *_: long_line)

    text = narrator.generate_narration_text("clip", "game", "streamer")

    assert 0 < len(text) < 100


@patch("src.narrator.add_narration", return_value="/tmp/test/clip_1_narrated.mp4")
@patch("src.pipeline.score_visual_quality", return_value=1.0)
@patch("src.pipeline.detect_leading_silence", return_value=0.0)
@patch("src.pipeline._cleanup_tmp_files")
@patch("src.pipeline.crop_to_vertical", return_value="/tmp/test/clip_1_vertical.mp4")
@patch("src.pipeline.download_clip", return_value="/tmp/test/clip_1.mp4")
def test_pipeline_narration_flow_calls_add_narration(
    mock_download,
    mock_crop,
    mock_cleanup,
    mock_silence,
    mock_quality,
    mock_add_narration,
):
    cfg = PipelineConfig(
        tmp_dir="/tmp/test",
        max_clip_duration_seconds=60,
        min_visual_quality=0.0,
        narration_enabled=True,
    )
    cfg.peak_action_trim = False
    cfg.loop_optimize = False
    cfg.context_overlay = False

    streamer = StreamerConfig(
        name="TheBurntPeanut",
        twitch_id="472066926",
        youtube_credentials="credentials/theburntpeanut_youtube.json",
    )
    clip = _make_clip()

    result, youtube_id = _process_single_clip(
        clip,
        None,
        conn=MagicMock(),
        cfg=cfg,
        streamer=streamer,
        log=MagicMock(),
        dry_run=True,
        title_template=None,
        title_templates=None,
        description_template=None,
        description_templates=None,
        extra_tags_global=[],
    )

    assert result == "dry_run"
    assert youtube_id is None
    mock_add_narration.assert_called_once_with(
        "/tmp/test/clip_1_vertical.mp4",
        cfg.tmp_dir,
        clip.title,
        clip.game_name or "",
        streamer.name,
    )


def test_pipeline_config_accepts_narration_enabled_field():
    cfg = PipelineConfig(narration_enabled=True)
    assert cfg.narration_enabled is True

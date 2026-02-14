"""Tests for Whisper caption fallback and backend selection."""

from unittest.mock import MagicMock, patch

from src.captioner import _segments_to_ass, _transcribe_whisper, generate_captions
from src.models import CaptionWord


def test_transcribe_whisper_success():
    model = MagicMock()
    model.transcribe.return_value = {
        "segments": [
            {"start": 0.0, "end": 1.2, "text": "hello there"},
        ]
    }

    with patch("src.captioner.whisper") as mock_whisper:
        mock_whisper.load_model.return_value = model
        segments = _transcribe_whisper("audio.flac")

    assert segments == [{"start": 0.0, "end": 1.2, "text": "hello there"}]
    mock_whisper.load_model.assert_called_once_with("base")
    model.transcribe.assert_called_once_with("audio.flac", language="en")


def test_transcribe_whisper_failure():
    with patch("src.captioner.whisper") as mock_whisper:
        mock_whisper.load_model.side_effect = RuntimeError("model failure")
        segments = _transcribe_whisper("audio.flac")

    assert segments == []


def test_segments_to_ass_format(tmp_path):
    segments = [{"start": 0.0, "end": 1.0, "text": "hello world"}]
    output_path = str(tmp_path / "whisper.ass")

    result = _segments_to_ass(segments, output_path)
    assert result == output_path

    content = (tmp_path / "whisper.ass").read_text(encoding="utf-8")
    assert "[Script Info]" in content
    assert "[V4+ Styles]" in content
    assert "[Events]" in content
    assert "&H80000000,1,0,0,0,100,100,0,0,1,4,0,2,20,20,400,1" in content
    assert "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,HELLO WORLD" in content


def test_segments_to_ass_uppercase(tmp_path):
    segments = [{"start": 0.0, "end": 1.0, "text": "hello MixedCase"}]
    output_path = str(tmp_path / "uppercase.ass")

    _segments_to_ass(segments, output_path)

    content = (tmp_path / "uppercase.ass").read_text(encoding="utf-8")
    events = content.split("[Events]")[1]
    assert "HELLO MIXEDCASE" in events
    assert "hello MixedCase" not in events


def test_caption_backend_auto_no_deepgram():
    whisper_segments = [{"start": 0.0, "end": 1.0, "text": "fallback line"}]
    with (
        patch.dict("os.environ", {"CAPTION_BACKEND": "auto"}, clear=True),
        patch("src.captioner.transcribe_clip", return_value=None) as mock_deepgram,
        patch("src.captioner.extract_audio") as mock_extract_audio,
        patch("src.captioner._transcribe_whisper", return_value=whisper_segments) as mock_whisper,
        patch("src.captioner._segments_to_ass", return_value="/tmp/test_captions.ass") as mock_ass,
    ):
        result = generate_captions("test.mp4", "/tmp")

    assert result == "/tmp/test_captions.ass"
    mock_deepgram.assert_called_once_with("test.mp4", "/tmp")
    mock_extract_audio.assert_called_once_with("test.mp4", "/tmp/test_audio_whisper.flac")
    mock_whisper.assert_called_once_with("/tmp/test_audio_whisper.flac")
    mock_ass.assert_called_once_with(whisper_segments, "/tmp/test_captions.ass")


def test_caption_backend_whisper_forced():
    whisper_segments = [{"start": 0.0, "end": 1.0, "text": "forced whisper"}]
    with (
        patch.dict(
            "os.environ",
            {"DEEPGRAM_API_KEY": "key", "CAPTION_BACKEND": "whisper"},
            clear=True,
        ),
        patch("src.captioner.transcribe_clip") as mock_deepgram,
        patch("src.captioner.extract_audio") as mock_extract_audio,
        patch("src.captioner._transcribe_whisper", return_value=whisper_segments) as mock_whisper,
        patch("src.captioner._segments_to_ass", return_value="/tmp/test_captions.ass") as mock_ass,
    ):
        result = generate_captions("test.mp4", "/tmp")

    assert result == "/tmp/test_captions.ass"
    mock_deepgram.assert_not_called()
    mock_extract_audio.assert_called_once_with("test.mp4", "/tmp/test_audio_whisper.flac")
    mock_whisper.assert_called_once_with("/tmp/test_audio_whisper.flac")
    mock_ass.assert_called_once_with(whisper_segments, "/tmp/test_captions.ass")


def test_caption_backend_deepgram_forced():
    words = [CaptionWord("hello", 0.0, 0.5)]
    with (
        patch.dict(
            "os.environ",
            {"DEEPGRAM_API_KEY": "key", "CAPTION_BACKEND": "deepgram"},
            clear=True,
        ),
        patch("src.captioner.transcribe_clip", return_value=words) as mock_deepgram,
        patch("src.captioner.generate_ass_subtitles", return_value="/tmp/test_captions.ass") as mock_ass,
        patch("src.captioner._transcribe_whisper") as mock_whisper,
    ):
        result = generate_captions("test.mp4", "/tmp")

    assert result == "/tmp/test_captions.ass"
    mock_deepgram.assert_called_once_with("test.mp4", "/tmp")
    mock_ass.assert_called_once_with(words, "/tmp/test_captions.ass", silence_offset=0.0)
    mock_whisper.assert_not_called()

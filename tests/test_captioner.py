"""Tests for captioner: transcription, ASS generation, word grouping, graceful degradation."""

from unittest.mock import patch

from src.captioner import (
    _format_ass_time,
    _group_words,
    generate_ass_subtitles,
    generate_captions,
    transcribe_clip,
)
from src.models import CaptionWord


class TestFormatAssTime:
    def test_zero(self):
        assert _format_ass_time(0.0) == "0:00:00.00"

    def test_simple_seconds(self):
        assert _format_ass_time(1.5) == "0:00:01.50"

    def test_minutes(self):
        assert _format_ass_time(65.25) == "0:01:05.25"

    def test_hours(self):
        assert _format_ass_time(3661.99) == "1:01:01.99"

    def test_negative_clamps_to_zero(self):
        assert _format_ass_time(-5.0) == "0:00:00.00"

    def test_centiseconds_rounding(self):
        # Total centiseconds approach: 1.999 * 100 = 199.9 -> round = 200
        # 200 cs = 2.00s -> "0:00:02.00"
        assert _format_ass_time(1.999) == "0:00:02.00"

    def test_exact_minute(self):
        assert _format_ass_time(60.0) == "0:01:00.00"

    def test_boundary_59_995(self):
        # 59.995 * 100 = 5999.5 -> round = 6000 cs -> 1:00:00.00
        assert _format_ass_time(59.995) == "0:01:00.00"


class TestGroupWords:
    def test_empty_list(self):
        assert _group_words([]) == []

    def test_single_word(self):
        words = [CaptionWord("hello", 0.0, 0.5)]
        groups = _group_words(words)
        assert len(groups) == 1
        assert len(groups[0]) == 1

    def test_max_three_words_per_group(self):
        words = [
            CaptionWord("one", 0.0, 0.2),
            CaptionWord("two", 0.2, 0.4),
            CaptionWord("three", 0.4, 0.6),
            CaptionWord("four", 0.6, 0.8),
        ]
        groups = _group_words(words)
        assert len(groups[0]) == 3
        assert len(groups[1]) == 1

    def test_break_on_gap_over_0_3s(self):
        words = [
            CaptionWord("hello", 0.0, 0.5),
            CaptionWord("world", 0.9, 1.4),  # gap of 0.4s > 0.3s
        ]
        groups = _group_words(words)
        assert len(groups) == 2

    def test_no_break_on_small_gap(self):
        words = [
            CaptionWord("hello", 0.0, 0.4),
            CaptionWord("world", 0.5, 0.9),  # gap of 0.1s < 0.3s
        ]
        groups = _group_words(words)
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_break_on_duration_limit(self):
        words = [
            CaptionWord("one", 0.0, 0.8),
            CaptionWord("two", 0.8, 1.6),
            CaptionWord("three", 1.6, 2.5),  # group duration > 2.0s
        ]
        groups = _group_words(words)
        assert len(groups) == 2

    def test_break_on_sentence_punctuation(self):
        words = [
            CaptionWord("hello.", 0.0, 0.3),
            CaptionWord("world", 0.3, 0.6),
        ]
        groups = _group_words(words)
        assert len(groups) == 2

    def test_break_on_question_mark(self):
        words = [
            CaptionWord("what?", 0.0, 0.3),
            CaptionWord("yes", 0.3, 0.6),
        ]
        groups = _group_words(words)
        assert len(groups) == 2

    def test_break_on_exclamation(self):
        words = [
            CaptionWord("wow!", 0.0, 0.3),
            CaptionWord("nice", 0.3, 0.6),
        ]
        groups = _group_words(words)
        assert len(groups) == 2

    def test_break_on_comma(self):
        words = [
            CaptionWord("well,", 0.0, 0.3),
            CaptionWord("actually", 0.3, 0.6),
        ]
        groups = _group_words(words)
        assert len(groups) == 2

    def test_no_break_on_word_without_punctuation(self):
        words = [
            CaptionWord("hello", 0.0, 0.2),
            CaptionWord("world", 0.2, 0.4),
        ]
        groups = _group_words(words)
        assert len(groups) == 1


class TestGenerateAssSubtitles:
    def test_generates_valid_ass_file(self, tmp_path):
        words = [
            CaptionWord("Hello", 0.0, 0.5),
            CaptionWord("world", 0.5, 1.0),
        ]
        output = str(tmp_path / "test.ass")
        result = generate_ass_subtitles(words, output)
        assert result == output

        with open(output) as f:
            content = f.read()
        assert "[Script Info]" in content
        assert "[V4+ Styles]" in content
        assert "[Events]" in content
        assert "Dialogue:" in content
        assert "HELLO WORLD" in content  # uppercased

    def test_text_is_uppercased(self, tmp_path):
        words = [CaptionWord("hello", 0.0, 0.5)]
        output = str(tmp_path / "test.ass")
        generate_ass_subtitles(words, output)
        with open(output) as f:
            content = f.read()
        assert "HELLO" in content
        assert "hello" not in content.split("[Events]")[1]

    def test_ass_timing_format(self, tmp_path):
        words = [
            CaptionWord("Test", 1.5, 2.75),
        ]
        output = str(tmp_path / "test.ass")
        generate_ass_subtitles(words, output)

        with open(output) as f:
            content = f.read()
        assert "0:00:01.50" in content
        assert "0:00:02.75" in content

    def test_multiple_groups(self, tmp_path):
        words = [
            CaptionWord("one", 0.0, 0.2),
            CaptionWord("two", 0.2, 0.4),
            CaptionWord("three", 0.4, 0.6),
            CaptionWord("four", 2.5, 2.8),  # new group (gap > 0.3s)
        ]
        output = str(tmp_path / "test.ass")
        generate_ass_subtitles(words, output)

        with open(output) as f:
            content = f.read()
        # Should have 2 Dialogue lines
        assert content.count("Dialogue:") == 2

    def test_resolution_1080x1920(self, tmp_path):
        words = [CaptionWord("test", 0.0, 1.0)]
        output = str(tmp_path / "test.ass")
        generate_ass_subtitles(words, output)

        with open(output) as f:
            content = f.read()
        assert "PlayResX: 1080" in content
        assert "PlayResY: 1920" in content

    def test_margin_v_400(self, tmp_path):
        words = [CaptionWord("test", 0.0, 1.0)]
        output = str(tmp_path / "test.ass")
        generate_ass_subtitles(words, output)

        with open(output) as f:
            content = f.read()
        assert ",400,1" in content  # MarginV=400 in style line

    def test_bold_1_outline_4(self, tmp_path):
        words = [CaptionWord("test", 0.0, 1.0)]
        output = str(tmp_path / "test.ass")
        generate_ass_subtitles(words, output)

        with open(output) as f:
            content = f.read()
        # Bold=1 (after BackColour), Outline=4 (after BorderStyle=1)
        # Style line: ...&H80000000,1,0,0,0,100,100,0,0,1,4,0,2,20,20,400,1
        assert "&H80000000,1,0,0,0,100,100,0,0,1,4,0,2,20,20,400,1" in content

    def test_ass_special_chars_escaped(self, tmp_path):
        words = [
            CaptionWord("{test}", 0.0, 0.5),
            CaptionWord("back\\slash", 0.5, 1.0),
        ]
        output = str(tmp_path / "test.ass")
        generate_ass_subtitles(words, output)

        with open(output) as f:
            content = f.read()
        events = content.split("[Events]")[1]
        assert "\\{TEST\\}" in events
        assert "BACK\\\\SLASH" in events

    def test_silence_offset_adjusts_times(self, tmp_path):
        words = [
            CaptionWord("early", 1.0, 1.5),
            CaptionWord("late", 3.0, 3.5),
        ]
        output = str(tmp_path / "test.ass")
        generate_ass_subtitles(words, output, silence_offset=2.0)

        with open(output) as f:
            content = f.read()
        # "early" at 1.0-1.5 shifted by 2.0 -> max(0, -1.0)=0.0 to max(0, -0.5)=0.0
        # Since end=0, the word is dropped
        # "late" at 3.0-3.5 shifted by 2.0 -> 1.0 to 1.5
        assert "0:00:01.00" in content
        assert "LATE" in content
        assert content.count("Dialogue:") == 1  # "early" was dropped


class TestTranscribeClip:
    def test_missing_api_key_returns_none(self):
        import os
        env = os.environ.copy()
        env.pop("DEEPGRAM_API_KEY", None)
        with patch.dict("os.environ", env, clear=True):
            result = transcribe_clip("test.mp4", "/tmp")
            assert result is None

    @patch("src.captioner.extract_audio", side_effect=Exception("ffmpeg failed"))
    def test_audio_extraction_failure_returns_none(self, mock_extract):
        with patch.dict("os.environ", {"DEEPGRAM_API_KEY": "test-key"}):
            result = transcribe_clip("test.mp4", "/tmp")
            assert result is None

    def test_missing_deepgram_sdk_returns_none(self):
        """When deepgram-sdk is not installed, transcribe returns None."""
        with patch("src.captioner.DeepgramClient", None), patch.dict("os.environ", {"DEEPGRAM_API_KEY": "test-key"}):
                result = transcribe_clip("test.mp4", "/tmp")
                assert result is None


@patch.dict("os.environ", {"CAPTION_BACKEND": "auto"}, clear=False)
class TestGenerateCaptions:
    @patch("src.captioner.transcribe_clip", return_value=None)
    def test_transcription_failure_returns_none(self, mock_transcribe):
        result = generate_captions("test.mp4", "/tmp")
        assert result is None

    @patch("src.captioner.generate_ass_subtitles", return_value="/tmp/test.ass")
    @patch("src.captioner.transcribe_clip")
    def test_success_returns_ass_path(self, mock_transcribe, mock_ass):
        mock_transcribe.return_value = [
            CaptionWord("hello", 0.0, 0.5),
        ]
        result = generate_captions("test.mp4", "/tmp")
        assert result == "/tmp/test.ass"

    @patch("src.captioner.generate_ass_subtitles", side_effect=Exception("write failed"))
    @patch("src.captioner.transcribe_clip")
    def test_ass_generation_failure_returns_none(self, mock_transcribe, mock_ass):
        mock_transcribe.return_value = [
            CaptionWord("hello", 0.0, 0.5),
        ]
        result = generate_captions("test.mp4", "/tmp")
        assert result is None

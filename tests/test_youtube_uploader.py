import pytest

from src.youtube_uploader import _truncate_title, _choose_template, _dedupe_tags


class TestTruncateTitle:
    def test_short_title_unchanged(self):
        assert _truncate_title("Hello World", max_len=100) == "Hello World"

    def test_exact_length_unchanged(self):
        title = "x" * 100
        assert _truncate_title(title, max_len=100) == title

    def test_truncates_at_word_boundary(self):
        title = "This is a fairly long title that should be cut off at a word boundary"
        result = _truncate_title(title, max_len=40)
        assert result.endswith("...")
        assert len(result) <= 40
        # Should not split mid-word
        without_ellipsis = result[:-3]
        assert not without_ellipsis.endswith(" ")  # rstrip removes trailing space

    def test_adds_ellipsis_on_truncation(self):
        title = "A" * 200
        result = _truncate_title(title, max_len=50)
        assert result.endswith("...")

    def test_no_ellipsis_when_short(self):
        result = _truncate_title("Short", max_len=100)
        assert "..." not in result

    def test_truncation_respects_max_len(self):
        title = "word " * 50  # 250 chars
        result = _truncate_title(title, max_len=80)
        assert len(result) <= 80


class TestTemplatesAndTags:
    def test_choose_template_is_deterministic(self):
        templates = ["A", "B", "C"]
        first = _choose_template("clip_123", templates)
        second = _choose_template("clip_123", templates)
        assert first == second

    def test_dedupe_tags_is_case_insensitive(self):
        tags = ["Foo", "foo", "Bar", " ", "bar"]
        assert _dedupe_tags(tags) == ["Foo", "Bar"]

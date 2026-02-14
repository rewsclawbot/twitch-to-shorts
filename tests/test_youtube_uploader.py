from unittest.mock import MagicMock, patch

from src.youtube_uploader import (
    _choose_template,
    _dedupe_tags,
    _render_template,
    _sanitize_text,
    _truncate_title,
    _uploads_playlist_cache,
    build_upload_title,
    build_upload_title_with_variant,
    check_channel_for_duplicate,
    get_title_variant_label,
    upload_short,
    validate_templates,
)
from tests.conftest import make_clip


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


class TestSanitizeText:
    def test_strips_control_characters(self):
        text = "Hello\x00World\x01\x1f!"
        assert _sanitize_text(text) == "HelloWorld!"

    def test_strips_null_bytes(self):
        assert _sanitize_text("abc\x00def") == "abcdef"

    def test_strips_angle_brackets(self):
        assert _sanitize_text("Hello <script>alert(1)</script> World") == "Hello scriptalert(1)/script World"

    def test_strips_unicode_bidi_overrides(self):
        # U+202E = Right-to-Left Override
        assert _sanitize_text("Hello\u202eWorld") == "HelloWorld"
        # U+200E = Left-to-Right Mark
        assert _sanitize_text("Hello\u200eWorld") == "HelloWorld"
        # U+200F = Right-to-Left Mark
        assert _sanitize_text("Hello\u200fWorld") == "HelloWorld"
        # U+202A-U+202E range
        for cp in range(0x202A, 0x202F):
            assert chr(cp) not in _sanitize_text(f"a{chr(cp)}b")
        # U+2066-U+2069 range
        for cp in range(0x2066, 0x206A):
            assert chr(cp) not in _sanitize_text(f"a{chr(cp)}b")

    def test_strips_whitespace(self):
        assert _sanitize_text("  hello  ") == "hello"

    def test_preserves_normal_unicode(self):
        assert _sanitize_text("Cafe\u0301 games") == "Cafe\u0301 games"

    def test_empty_string(self):
        assert _sanitize_text("") == ""

    def test_string_exceeding_100_chars(self):
        # _sanitize_text itself doesn't truncate; that's _truncate_title's job.
        # Verify it passes through long strings unchanged (aside from control chars).
        long_text = "A" * 200
        assert _sanitize_text(long_text) == long_text


class TestRenderTemplate:
    def test_basic_render(self):
        clip = make_clip(title="Great Play", streamer="streamer1")
        clip.game_name = "Fortnite"
        result = _render_template("{title} by {streamer} playing {game}", clip)
        assert result == "Great Play by streamer1 playing Fortnite"

    def test_format_string_in_title_escaped(self):
        clip = make_clip(title="{malicious_key}")
        clip.game_name = ""
        result = _render_template("{title}", clip)
        assert result == "{malicious_key}"

    def test_format_string_braces_in_streamer_escaped(self):
        clip = make_clip(streamer="user{with}braces")
        result = _render_template("{streamer}", clip)
        assert result == "user{with}braces"

    def test_game_name_alias(self):
        clip = make_clip()
        clip.game_name = "Valorant"
        r1 = _render_template("{game}", clip)
        r2 = _render_template("{game_name}", clip)
        assert r1 == r2 == "Valorant"

    def test_unknown_key_returns_empty(self):
        clip = make_clip()
        result = _render_template("{nonexistent}", clip)
        assert result == ""


class TestValidateTemplates:
    def test_valid_templates_no_warning(self, caplog):
        validate_templates(["{title} - {streamer}", "{game_name}"])
        assert "unknown keys" not in caplog.text

    def test_unknown_key_logs_warning(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            validate_templates(["{title} {bad_key}"])
        assert "unknown keys" in caplog.text
        assert "bad_key" in caplog.text

    def test_none_templates_no_error(self):
        validate_templates(None)

    def test_empty_list_no_error(self):
        validate_templates([])


def _make_mock_service(video_id="vid_123"):
    """Create a mock YouTube service that returns a successful upload."""
    service = MagicMock()
    insert_req = MagicMock()
    insert_req.next_chunk.return_value = (None, {"id": video_id})
    service.videos().insert.return_value = insert_req
    return service


class TestBuildUploadTitle:
    def test_default_title_no_streamer_suffix(self):
        """Default title (no templates) should be clip title only, without '| streamer'."""
        clip = make_clip(title="Epic Headshot", streamer="StreamerName")
        result = build_upload_title(clip)
        assert result == "Epic Headshot"
        assert "StreamerName" not in result

    def test_template_overrides_default(self):
        clip = make_clip(title="Nice Play", streamer="s1")
        clip.game_name = "Valorant"
        result = build_upload_title(clip, title_template="{title} | {game}")
        assert result == "Nice Play | Valorant"

    def test_templates_list_overrides_single(self):
        clip = make_clip(clip_id="deterministic_id", title="Cool Clip", streamer="s1")
        clip.game_name = "Apex"
        result = build_upload_title(clip, title_template="{title} SINGLE", title_templates=["{title} LIST"])
        assert result == "Cool Clip LIST"

    def test_get_title_variant_label_for_templates(self):
        clip = make_clip(clip_id="deterministic_id")
        label = get_title_variant_label(clip, title_templates=["A", "B", "C"])
        assert label.startswith("template_")

    def test_build_upload_title_with_variant_returns_variant_label(self):
        clip = make_clip(clip_id="deterministic_id", title="Clip")
        title, variant = build_upload_title_with_variant(clip, title_templates=["{title} A", "{title} B"])
        assert title in {"Clip A", "Clip B"}
        assert variant in {"template_0", "template_1"}


class TestUploadShortPrebuiltTitle:
    @patch("src.youtube_uploader.MediaFileUpload")
    def test_prebuilt_title_used_directly(self, _mock_media):
        """When prebuilt_title is provided, build_upload_title is NOT called."""
        service = _make_mock_service()
        clip = make_clip(title="Original Title", streamer="streamer1")

        with patch("src.youtube_uploader.build_upload_title") as mock_build:
            result = upload_short(
                service, "fake_video.mp4", clip, prebuilt_title="My Custom Title"
            )

        mock_build.assert_not_called()
        assert result == "vid_123"
        # Verify the custom title was passed in the API body
        body = service.videos().insert.call_args[1]["body"]
        assert body["snippet"]["title"] == "My Custom Title"

    @patch("src.youtube_uploader.MediaFileUpload")
    def test_no_prebuilt_title_calls_build(self, _mock_media):
        """When prebuilt_title is None, build_upload_title is called as before."""
        service = _make_mock_service()
        clip = make_clip(title="Original Title", streamer="streamer1")

        with patch("src.youtube_uploader.build_upload_title", return_value="Built Title") as mock_build:
            result = upload_short(service, "fake_video.mp4", clip)

        mock_build.assert_called_once_with(clip, None, None)
        assert result == "vid_123"
        body = service.videos().insert.call_args[1]["body"]
        assert body["snippet"]["title"] == "Built Title"

    @patch("src.youtube_uploader.MediaFileUpload")
    def test_prebuilt_title_none_falls_back(self, _mock_media):
        """Explicitly passing prebuilt_title=None still calls build_upload_title."""
        service = _make_mock_service()
        clip = make_clip(title="Test", streamer="s1")

        with patch("src.youtube_uploader.build_upload_title", return_value="Fallback") as mock_build:
            upload_short(service, "fake_video.mp4", clip, prebuilt_title=None)

        mock_build.assert_called_once()

    @patch("src.youtube_uploader.MediaFileUpload")
    def test_tags_always_include_shorts_hashtag(self, _mock_media):
        service = _make_mock_service()
        clip = make_clip(title="Clip", streamer="s1")
        upload_short(service, "fake_video.mp4", clip)
        body = service.videos().insert.call_args[1]["body"]
        assert "#shorts" in body["snippet"]["tags"]

    @patch("src.youtube_uploader.MediaFileUpload")
    def test_game_hashtag_added_to_tags(self, _mock_media):
        service = _make_mock_service()
        clip = make_clip(title="Clip", streamer="s1")
        clip.game_name = "Apex Legends"
        upload_short(service, "fake_video.mp4", clip)
        body = service.videos().insert.call_args[1]["body"]
        assert "#apexlegends" in body["snippet"]["tags"]

    @patch("src.youtube_uploader.MediaFileUpload")
    def test_default_description_contains_seo_and_credit(self, _mock_media):
        service = _make_mock_service()
        clip = make_clip(title="Insane Clutch", streamer="TheBurntPeanut")
        clip.game_name = "Valorant"
        upload_short(service, "fake_video.mp4", clip)
        description = service.videos().insert.call_args[1]["body"]["snippet"]["description"]
        assert "Valorant highlight" in description
        assert "Credit: TheBurntPeanut on Twitch." in description
        assert "#shorts" in description.lower()

    @patch("src.youtube_uploader.MediaFileUpload")
    def test_template_description_gets_required_hashtags(self, _mock_media):
        service = _make_mock_service()
        clip = make_clip(title="Title", streamer="s1")
        clip.game_name = "Counter-Strike 2"
        upload_short(service, "fake_video.mp4", clip, description_template="Custom line")
        description = service.videos().insert.call_args[1]["body"]["snippet"]["description"]
        assert "#shorts" in description.lower()
        assert "#gaming" in description.lower()
        assert "#counterstrike2" in description.lower()


class TestChannelDedup:
    def setup_method(self):
        _uploads_playlist_cache.clear()

    def test_empty_page_with_next_token_stops_pagination(self):
        service = MagicMock()
        service.channels.return_value.list.return_value.execute.return_value = {
            "items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UPLOADS_1"}}}],
        }
        service.playlistItems.return_value.list.return_value.execute.return_value = {
            "items": [],
            "nextPageToken": "still-more",
        }

        result = check_channel_for_duplicate(service, "Title", max_results=50, cache_key="chan-a")
        assert result is None
        # Guard should break immediately instead of looping forever.
        assert service.playlistItems.return_value.list.call_count == 1

    def test_cache_isolated_by_cache_key(self):
        service = MagicMock()
        service.channels.return_value.list.return_value.execute.side_effect = [
            {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UPLOADS_A"}}}]},
            {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UPLOADS_B"}}}]},
        ]
        service.playlistItems.return_value.list.return_value.execute.return_value = {"items": []}

        check_channel_for_duplicate(service, "Title A", cache_key="channel-a")
        check_channel_for_duplicate(service, "Title B", cache_key="channel-b")

        # Different keys should not share cached uploads playlist IDs.
        assert service.channels.return_value.list.call_count == 2

from pathlib import Path
from unittest.mock import patch

from PIL import Image

from src.thumbnail_enhancer import _find_bold_font, _wrap_text, enhance_thumbnail


def _create_test_image(path: Path) -> None:
    image = Image.new("RGB", (100, 178), color=(25, 50, 75))
    image.save(path)


def test_wrap_text_short():
    text = "INSANE PLAY"
    assert _wrap_text(text) == ["INSANE PLAY"]


def test_wrap_text_long():
    text = "This was a ridiculous clutch that nobody expected"
    wrapped = _wrap_text(text)
    assert len(wrapped) == 2
    assert all(line for line in wrapped)
    assert all(len(line) <= 30 for line in wrapped)


def test_wrap_text_very_long():
    text = (
        "This is a very long clip title that should absolutely wrap into multiple "
        "lines and truncate extra words for readability"
    )
    wrapped = _wrap_text(text)
    assert len(wrapped) == 2
    assert wrapped[-1].endswith("...")


def test_wrap_text_no_spaces():
    text = "A" * 80
    wrapped = _wrap_text(text)
    assert len(wrapped) == 2
    assert wrapped[0] == "A" * 30
    assert wrapped[1].endswith("...")


def test_enhance_thumbnail_creates_image(tmp_path):
    image_path = tmp_path / "thumb.jpg"
    _create_test_image(image_path)

    result = enhance_thumbnail(str(image_path), "INSANE PLAY")

    assert result == str(image_path)
    assert image_path.exists()
    with Image.open(image_path) as image:
        assert image.size == (100, 178)


def test_enhance_thumbnail_output_path(tmp_path):
    image_path = tmp_path / "thumb.jpg"
    output_path = tmp_path / "thumb_enhanced.jpg"
    _create_test_image(image_path)

    result = enhance_thumbnail(str(image_path), "INSANE PLAY", output_path=str(output_path))

    assert result == str(output_path)
    assert output_path.exists()


def test_enhance_thumbnail_disabled(tmp_path):
    image_path = tmp_path / "thumb.jpg"
    output_path = tmp_path / "thumb_enhanced.jpg"
    _create_test_image(image_path)

    with patch.dict("os.environ", {"THUMBNAIL_TEXT_ENABLED": "false"}, clear=False):
        result = enhance_thumbnail(str(image_path), "INSANE PLAY", output_path=str(output_path))

    assert result == str(image_path)
    assert not output_path.exists()


def test_enhance_thumbnail_missing_file(tmp_path):
    missing_path = tmp_path / "missing.jpg"
    result = enhance_thumbnail(str(missing_path), "INSANE PLAY")
    assert result == str(missing_path)


def test_enhance_thumbnail_dimensions_preserved(tmp_path):
    image_path = tmp_path / "thumb.jpg"
    output_path = tmp_path / "thumb_enhanced.jpg"
    _create_test_image(image_path)

    result = enhance_thumbnail(str(image_path), "INSANE PLAY", output_path=str(output_path))

    assert result == str(output_path)
    with Image.open(image_path) as original, Image.open(output_path) as enhanced:
        assert original.size == enhanced.size


def test_find_bold_font():
    font = _find_bold_font(36)
    assert hasattr(font, "getbbox")

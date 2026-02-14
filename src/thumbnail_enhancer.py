"""Thumbnail text overlay enhancer for YouTube Shorts CTR improvements."""

import logging
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

_FONT_DIRS = (
    Path("/System/Library/Fonts/"),
    Path("/Library/Fonts/"),
    Path("/usr/share/fonts/"),
)
_FONT_EXTENSIONS = (".ttf", ".ttc", ".otf")
_FONT_NAME_HINTS = ("arial", "helvetica", "dejavu")
_FONT_BOLD_HINT = "bold"
_DIRECT_FONT_FILES = (
    "Arial Bold.ttf",
    "Helvetica Bold.ttf",
    "DejaVuSans-Bold.ttf",
)

_CACHED_BOLD_FONT_PATH: str | None = None
_BOLD_FONT_LOOKUP_DONE = False


def _thumbnail_text_enabled() -> bool:
    """Return whether thumbnail text overlay is enabled."""
    return os.environ.get("THUMBNAIL_TEXT_ENABLED", "true").strip().lower() == "true"


def _truncate_with_ellipsis(text: str, max_chars: int) -> str:
    """Return text constrained to max_chars with trailing ellipsis when needed."""
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return "." * max_chars
    return text[: max_chars - 3].rstrip() + "..."


def _nearest_space_index(text: str, target: int) -> int:
    """Return split index at prior space, falling back to hard-wrap target."""
    before = text.rfind(" ", 0, target + 1)
    if before > 0:
        return before
    return target


def _wrap_text(text: str, max_chars: int = 30) -> list[str]:
    """Wrap text into at most two lines and truncate overflow with ellipsis."""
    normalized = " ".join(text.strip().split())
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]

    lines: list[str] = []
    remaining = normalized

    while remaining and len(lines) < 2:
        if len(remaining) <= max_chars:
            lines.append(remaining)
            remaining = ""
            break

        split_idx = _nearest_space_index(remaining, max_chars)
        if split_idx <= 0:
            split_idx = max_chars

        line = remaining[:split_idx].rstrip()
        if not line:
            line = remaining[:max_chars]
            split_idx = len(line)

        lines.append(line)
        remaining = remaining[split_idx:].lstrip()

    if remaining:
        overflow_text = f"{lines[-1]} {remaining}".strip()
        lines[-1] = _truncate_with_ellipsis(overflow_text, max_chars)

    return lines[:2]


def _iter_bold_font_candidates() -> list[str]:
    """Return ordered font path candidates for bold system fonts."""
    candidates: list[str] = []

    for font_dir in _FONT_DIRS:
        if not font_dir.exists():
            continue

        for filename in _DIRECT_FONT_FILES:
            direct_path = font_dir / filename
            if direct_path.is_file():
                candidates.append(str(direct_path))

        for root, _, files in os.walk(font_dir):
            for filename in files:
                lowered = filename.lower()
                if not lowered.endswith(_FONT_EXTENSIONS):
                    continue
                if _FONT_BOLD_HINT not in lowered:
                    continue
                if not any(hint in lowered for hint in _FONT_NAME_HINTS):
                    continue
                candidates.append(str(Path(root) / filename))

    # Preserve ordering while removing duplicates.
    return list(dict.fromkeys(candidates))


def _find_bold_font(size: int) -> ImageFont.ImageFont:
    """Return a bold font at size, falling back to Pillow default when unavailable."""
    global _BOLD_FONT_LOOKUP_DONE, _CACHED_BOLD_FONT_PATH

    if _CACHED_BOLD_FONT_PATH:
        try:
            return ImageFont.truetype(_CACHED_BOLD_FONT_PATH, size=size)
        except Exception as err:
            log.warning("Failed to load cached bold font %s: %s", _CACHED_BOLD_FONT_PATH, err)

    if not _BOLD_FONT_LOOKUP_DONE:
        for font_path in _iter_bold_font_candidates():
            try:
                font = ImageFont.truetype(font_path, size=size)
                _CACHED_BOLD_FONT_PATH = font_path
                _BOLD_FONT_LOOKUP_DONE = True
                return font
            except Exception:
                continue

        _BOLD_FONT_LOOKUP_DONE = True
        log.warning("No bold system font found, using Pillow default font")

    return ImageFont.load_default()


def enhance_thumbnail(image_path: str, text: str, output_path: str | None = None) -> str:
    """Apply a readable text overlay to a thumbnail image with graceful fallback."""
    if not _thumbnail_text_enabled():
        return image_path

    lines = _wrap_text(text, max_chars=30)
    if not lines:
        return image_path

    try:
        with Image.open(image_path) as input_image:
            image = input_image.copy()

        width, height = image.size
        font_size = max(36, min(96, width // 12))
        font = _find_bold_font(font_size)
        draw = ImageDraw.Draw(image)

        y_position = int(height * 0.2)
        line_spacing = max(4, font_size // 8)

        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font, stroke_width=3)
            line_width = bbox[2] - bbox[0]
            line_height = bbox[3] - bbox[1]
            x_position = max(0, (width - line_width) // 2)
            draw.text(
                (x_position, y_position),
                line,
                font=font,
                fill="white",
                stroke_width=3,
                stroke_fill="black",
            )
            y_position += line_height + line_spacing

        destination = output_path or image_path
        image.save(destination)
        log.info("Added text overlay to thumbnail: %s", image_path)
        return destination
    except Exception as err:
        log.warning("Failed to enhance thumbnail %s: %s", image_path, err)
        return image_path

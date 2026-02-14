# Codex Task: Thumbnail Text Overlay Module

## Context
This is a Twitch-to-YouTube-Shorts pipeline. Current thumbnails are auto-extracted frames (YDIF-based) with no text overlay. YouTube Shorts with text on thumbnails get significantly higher CTR. We need to add bold text overlays to thumbnails.

## Task
Create `src/thumbnail_enhancer.py` and `tests/test_thumbnail_enhancer.py`.

## Requirements

### src/thumbnail_enhancer.py

1. **`enhance_thumbnail(image_path: str, text: str, output_path: str | None = None) -> str`**
   - Takes a thumbnail image path and overlay text
   - Adds bold text overlay to the image
   - Returns path to enhanced image (overwrites if output_path is None, uses output_path otherwise)
   - If enhancement fails for any reason, return the original image_path unchanged (graceful degradation)

2. **Text Styling:**
   - Font: Try to load a bold system font (Arial Bold, Helvetica Bold, DejaVu Sans Bold) — fall back to Pillow default if none found
   - Font size: Auto-calculated based on image width (roughly width/12, min 36px, max 96px)
   - Color: White text
   - Stroke: 3px black outline for readability on any background
   - Position: Centered horizontally, top 20% of image vertically
   - Word wrap: If text is >30 chars, split into 2 lines (break at nearest space to midpoint)
   - Max 2 lines — truncate with "..." if still too long after wrapping

3. **`_find_bold_font(size: int) -> ImageFont`**
   - Search common system font paths for bold fonts
   - macOS: `/System/Library/Fonts/`, `/Library/Fonts/`
   - Linux: `/usr/share/fonts/`
   - Return Pillow default font if nothing found
   - Cache the font path after first lookup (module-level variable)

4. **`_wrap_text(text: str, max_chars: int = 30) -> list[str]`**
   - Split text into lines of max_chars each
   - Break at word boundaries (nearest space to max_chars)
   - Return max 2 lines, truncating the rest with "..."

5. **Config support:**
   - Read `THUMBNAIL_TEXT_ENABLED` env var (default: "true" — enabled by default)
   - If disabled, return original image_path immediately

6. **Logging:**
   - Log when text overlay is added: `log.info("Added text overlay to thumbnail: %s", image_path)`
   - Log warnings on failure (font not found, image processing error)

### tests/test_thumbnail_enhancer.py

Write comprehensive tests:
1. `test_wrap_text_short` — text under 30 chars returns single line
2. `test_wrap_text_long` — text over 30 chars wraps to 2 lines
3. `test_wrap_text_very_long` — text that needs 3+ lines truncates with "..."
4. `test_wrap_text_no_spaces` — text with no spaces gets hard-wrapped
5. `test_enhance_thumbnail_creates_image` — creates an enhanced image file
6. `test_enhance_thumbnail_output_path` — respects custom output_path
7. `test_enhance_thumbnail_disabled` — returns original when THUMBNAIL_TEXT_ENABLED=false
8. `test_enhance_thumbnail_missing_file` — returns original path on missing input file
9. `test_enhance_thumbnail_dimensions_preserved` — output has same dimensions as input
10. `test_find_bold_font` — returns a font object (even if default)

For image tests, create a small test image (100x178 pixels, solid color) using Pillow in the test fixture.

### Integration
Do NOT modify main.py or any other files. Just create the two new files.
The module should be importable:
```python
from src.thumbnail_enhancer import enhance_thumbnail
enhanced_path = enhance_thumbnail("thumb.jpg", "INSANE PLAY! 🔥")
```

### Dependencies
Use `Pillow` (PIL). It's already in requirements.txt.

### Style
- Follow the existing code style in src/ (logging patterns, error handling, type hints)
- Use `log = logging.getLogger(__name__)`
- Graceful degradation on all failures (never crash the pipeline)
- All functions should have docstrings

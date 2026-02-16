#!/usr/bin/env python3
"""Backfill custom thumbnails for already-uploaded YouTube Shorts.

Downloads each short via yt-dlp, extracts the best thumbnail frame,
enhances it with a text overlay, and sets it on YouTube.

Supports --only to target specific video IDs, and aborts early on rate limits.
"""

import argparse
import logging
import os
import sys
import tempfile
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db import get_connection
from src.thumbnail_enhancer import enhance_thumbnail
from src.video_processor import extract_thumbnail
from src.youtube_uploader import set_thumbnail, get_authenticated_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def download_youtube_video(youtube_id: str, output_dir: str) -> str | None:
    """Download a YouTube video via yt-dlp, return path to downloaded file."""
    import subprocess

    url = f"https://www.youtube.com/shorts/{youtube_id}"
    output_template = os.path.join(output_dir, f"{youtube_id}.%(ext)s")

    cmd = [
        sys.executable, "-m", "yt_dlp",
        url,
        "-o", output_template,
        "--format", "best[height<=1080]",
        "--no-playlist",
        "--quiet",
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)
    except subprocess.CalledProcessError as e:
        log.error("Failed to download %s: %s", youtube_id, e.stderr[:200] if e.stderr else str(e))
        return None
    except subprocess.TimeoutExpired:
        log.error("Download timed out for %s", youtube_id)
        return None

    # Find the downloaded file
    for f in os.listdir(output_dir):
        if f.startswith(youtube_id) and not f.endswith(".part"):
            return os.path.join(output_dir, f)

    return None


def _is_rate_limit_error(exc: Exception) -> bool:
    """Check if an exception is a YouTube 429 rate limit."""
    return "429" in str(exc) or "uploadRateLimitExceeded" in str(exc)


def backfill_thumbnails(
    db_path: str = "data/clips.db",
    credentials_path: str = "credentials/theburntpeanut_youtube.json",
    client_secrets: str = "credentials/client_secret.json",
    thumbnail_samples: int = 8,
    thumbnail_width: int = 1080,
    dry_run: bool = False,
    limit: int | None = None,
    only_ids: list[str] | None = None,
    spacing: int = 2,
):
    """Main backfill logic.

    Args:
        only_ids: If provided, only process these YouTube video IDs.
        spacing: Seconds between uploads to avoid rate limits.
    """
    conn = get_connection(db_path)

    # Get uploaded clips
    if only_ids:
        placeholders = ",".join("?" for _ in only_ids)
        query = f"""
            SELECT clip_id, youtube_id, title, streamer
            FROM clips
            WHERE youtube_id IN ({placeholders})
            ORDER BY posted_at
        """
        rows = conn.execute(query, only_ids).fetchall()
    else:
        query = """
            SELECT clip_id, youtube_id, title, streamer
            FROM clips
            WHERE youtube_id IS NOT NULL
            ORDER BY posted_at
        """
        rows = conn.execute(query).fetchall()

    if limit:
        rows = rows[:limit]

    log.info("Found %d uploaded shorts to backfill thumbnails for", len(rows))

    if not rows:
        return

    # Authenticate with YouTube
    yt_service = get_authenticated_service(client_secrets, credentials_path)
    if not yt_service:
        log.error("Failed to authenticate with YouTube API")
        return

    success_count = 0
    fail_count = 0
    rate_limited = False

    for row in rows:
        youtube_id = row["youtube_id"]
        title = row["title"] or ""
        clip_id = row["clip_id"]

        log.info("Processing %s (%s): %s", youtube_id, clip_id[:20], title[:50])

        with tempfile.TemporaryDirectory(prefix="thumb_backfill_") as tmp_dir:
            # Step 1: Download the video from YouTube
            video_path = download_youtube_video(youtube_id, tmp_dir)
            if not video_path:
                log.warning("Skipping %s — download failed", youtube_id)
                fail_count += 1
                continue

            log.info("Downloaded %s to %s", youtube_id, video_path)

            # Step 2: Extract best thumbnail frame
            thumb_path = extract_thumbnail(
                video_path,
                tmp_dir,
                samples=thumbnail_samples,
                width=thumbnail_width,
            )
            if not thumb_path:
                log.warning("Skipping %s — thumbnail extraction failed", youtube_id)
                fail_count += 1
                continue

            # Step 3: Enhance with text overlay
            enhanced_path = enhance_thumbnail(thumb_path, title)

            if dry_run:
                log.info("[DRY RUN] Would set thumbnail for %s from %s", youtube_id, enhanced_path)
                success_count += 1
                continue

            # Step 4: Upload to YouTube
            result = set_thumbnail(yt_service, youtube_id, enhanced_path)
            if result:
                log.info("✅ Set thumbnail for %s", youtube_id)
                success_count += 1
            else:
                log.warning("❌ Failed to set thumbnail for %s", youtube_id)
                fail_count += 1
                # Check if we got rate limited — abort early, no point continuing
                # set_thumbnail returns False and logs the HttpError
                rate_limited = True
                log.error(
                    "⚠️ Rate limit detected — aborting remaining %d videos. "
                    "Retry later with --only for the failed IDs.",
                    len(rows) - success_count - fail_count,
                )
                break

            # Pause between uploads to avoid rate limiting
            time.sleep(spacing)

    log.info(
        "Backfill complete: %d/%d succeeded, %d failed%s",
        success_count,
        len(rows),
        fail_count,
        " (rate-limited, aborted early)" if rate_limited else "",
    )
    conn.close()

    if rate_limited:
        sys.exit(2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill thumbnails for uploaded Shorts")
    parser.add_argument("--db", default="data/clips.db", help="Path to clips database")
    parser.add_argument("--credentials", default="credentials/theburntpeanut_youtube.json")
    parser.add_argument("--client-secrets", default="credentials/client_secret.json")
    parser.add_argument("--samples", type=int, default=8, help="Thumbnail frame samples")
    parser.add_argument("--width", type=int, default=1080, help="Thumbnail width")
    parser.add_argument("--dry-run", action="store_true", help="Download and extract but don't upload")
    parser.add_argument("--limit", type=int, help="Process only first N videos")
    parser.add_argument("--only", nargs="+", metavar="VIDEO_ID",
                        help="Only process these specific YouTube video IDs")
    parser.add_argument("--spacing", type=int, default=2,
                        help="Seconds between uploads (default: 2)")
    args = parser.parse_args()

    backfill_thumbnails(
        db_path=args.db,
        credentials_path=args.credentials,
        client_secrets=args.client_secrets,
        thumbnail_samples=args.samples,
        thumbnail_width=args.width,
        dry_run=args.dry_run,
        limit=args.limit,
        only_ids=args.only,
        spacing=args.spacing,
    )

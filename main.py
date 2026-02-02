import argparse
import logging
import logging.handlers
import os
import sys
import glob
import time

import yaml
from dotenv import load_dotenv

load_dotenv()

from src.twitch_client import TwitchClient
from src.clip_filter import filter_and_rank
from src.dedup import filter_new_clips
from src.downloader import download_clip
from src.video_processor import crop_to_vertical
from src.youtube_uploader import get_authenticated_service, upload_short, verify_upload, QuotaExhaustedError
from src.db import get_connection, insert_clip, update_streamer_stats, recent_upload_count, increment_fail_count

LOCK_FILE = os.path.join("data", "pipeline.lock")


def setup_logging(log_file: str | None = None):
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_file, maxBytes=10 * 1024 * 1024, backupCount=3
            )
        )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def clean_stale_tmp(tmp_dir: str, max_age_hours: int = 24):
    """Remove .mp4 files older than max_age_hours from tmp_dir."""
    if not os.path.isdir(tmp_dir):
        return
    cutoff = time.time() - max_age_hours * 3600
    for f in glob.glob(os.path.join(tmp_dir, "*.mp4")) + glob.glob(os.path.join(tmp_dir, "*.mp4.tmp")):
        try:
            if os.path.getmtime(f) < cutoff:
                os.remove(f)
        except OSError as e:
            log.warning("Failed to delete stale file %s: %s", f, e)


log = logging.getLogger(__name__)


def _try_create_lock() -> bool:
    """Attempt atomic lock file creation. Returns True if created."""
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False


def acquire_lock() -> bool:
    """PID-file based lock with atomic creation. Returns True if lock acquired."""
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    if _try_create_lock():
        return True
    # Lock file exists — check for stale PID
    try:
        with open(LOCK_FILE) as f:
            old_pid = int(f.read().strip())
        os.kill(old_pid, 0)
        return False  # Process is alive
    except (ValueError, OSError) as e:
        log.warning("Removed stale/corrupt lock file: %s", e)
    try:
        os.remove(LOCK_FILE)
    except OSError:
        return False
    return _try_create_lock()


def release_lock():
    try:
        os.remove(LOCK_FILE)
    except OSError:
        pass


def run_pipeline(config: dict, dry_run: bool = False):
    log = logging.getLogger("pipeline")
    cfg = config["pipeline"]
    conn = get_connection(cfg["db_path"])
    try:
        _run_pipeline_inner(config, cfg, conn, log, dry_run=dry_run)
    finally:
        clean_stale_tmp(cfg["tmp_dir"], max_age_hours=1)
        conn.close()


def _run_pipeline_inner(config: dict, cfg: dict, conn, log, dry_run: bool = False):

    # Read Twitch credentials from environment
    twitch_client_id = os.environ.get("TWITCH_CLIENT_ID") or (config.get("twitch") or {}).get("client_id")
    twitch_client_secret = os.environ.get("TWITCH_CLIENT_SECRET") or (config.get("twitch") or {}).get("client_secret")
    if not twitch_client_id or not twitch_client_secret:
        log.error("TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET environment variables must be set")
        raise ValueError("TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET must be set")

    twitch = TwitchClient(twitch_client_id, twitch_client_secret)

    # Clean stale tmp files at startup
    clean_stale_tmp(cfg["tmp_dir"])

    total_uploaded = 0

    for streamer in config["streamers"]:
        name = streamer["name"]
        twitch_id = streamer["twitch_id"]
        log.info("=== Processing streamer: %s ===", name)

        # 1. Fetch clips
        try:
            clips = twitch.fetch_clips(twitch_id, cfg["clip_lookback_hours"])
        except Exception:
            log.exception("Failed to fetch clips for %s", name)
            continue

        if not clips:
            log.info("No clips found for %s", name)
            continue

        # Tag clips with streamer name
        for c in clips:
            c["streamer"] = name

        # 2. Filter & rank
        ranked = filter_and_rank(
            conn, clips, name,
            velocity_weight=cfg["velocity_weight"],
            top_percentile=cfg["top_percentile"],
            bootstrap_top_n=cfg["bootstrap_top_n"],
            max_clips=cfg["max_clips_per_streamer"],
        )

        # 3. Deduplicate
        new_clips = filter_new_clips(conn, ranked)
        log.info("%d new clips after dedup (from %d ranked)", len(new_clips), len(ranked))

        if not new_clips:
            continue

        # Resolve game names for tags
        game_ids = [c.get("game_id", "") for c in new_clips]
        try:
            game_names = twitch.get_game_names(game_ids)
        except Exception:
            log.warning("Failed to resolve game names, continuing without")
            game_names = {}
        for c in new_clips:
            c["game_name"] = game_names.get(c.get("game_id", ""), "")

        # Upload scheduling: max 1 upload per streamer per 4 hours
        upload_spacing_hours = cfg.get("upload_spacing_hours", 4)
        max_uploads_per_window = cfg.get("max_uploads_per_window", 1)
        recent = recent_upload_count(conn, name, upload_spacing_hours)
        uploads_remaining = max(max_uploads_per_window - recent, 0)
        if uploads_remaining == 0:
            log.info("Skipping uploads for %s: %d uploaded in last %dh", name, recent, upload_spacing_hours)
            continue

        # Get YouTube service for this streamer (skip in dry-run)
        yt_service = None
        if not dry_run:
            try:
                yt_service = get_authenticated_service(
                    config["youtube"]["client_secrets_file"],
                    streamer["youtube_credentials"],
                )
            except Exception:
                log.exception("Failed to authenticate YouTube for %s", name)
                continue

        quota_exhausted = False
        max_duration = cfg.get("max_clip_duration_seconds", 60)
        for clip in new_clips[:uploads_remaining]:
            # Pre-filter by duration before downloading
            if clip["duration"] > max_duration:
                log.info("Skipping clip %s (%.1fs > %ds max duration)", clip["id"], clip["duration"], max_duration)
                continue

            # 4. Download
            video_path = download_clip(clip, cfg["tmp_dir"])
            if not video_path:
                continue

            # 5. Process video
            vertical_path = crop_to_vertical(
                video_path, cfg["tmp_dir"], cfg["max_clip_duration_seconds"],
                facecam=streamer.get("facecam"),
            )
            if not vertical_path:
                increment_fail_count(conn, clip["id"], clip["streamer"], clip["created_at"])
                continue

            # 6. Upload (skip in dry-run)
            if dry_run:
                log.info("[DRY RUN] Would upload clip %s: %s", clip["id"], clip["title"])
                total_uploaded += 1
                continue

            try:
                youtube_id = upload_short(yt_service, vertical_path, clip["title"], name,
                                         game_name=clip.get("game_name", ""),
                                         category_id=streamer.get("category_id", "20"),
                                         privacy_status=streamer.get("privacy_status", "public"))
            except QuotaExhaustedError:
                log.warning("YouTube quota exhausted — stopping uploads for this run")
                quota_exhausted = True
                break

            # 7. Verify & record in DB only after successful upload
            if not youtube_id:
                increment_fail_count(conn, clip["id"], clip["streamer"], clip["created_at"])
                continue

            if youtube_id:
                if not verify_upload(yt_service, youtube_id):
                    log.warning("Upload verification failed for clip %s (yt=%s), skipping DB insert", clip["id"], youtube_id)
                    increment_fail_count(conn, clip["id"], clip["streamer"], clip["created_at"])
                    continue
                clip["youtube_id"] = youtube_id
                insert_clip(conn, clip)
                total_uploaded += 1
                log.info("Uploaded clip %s -> YouTube %s", clip["id"], youtube_id)

                # Clean up tmp files after successful upload
                for tmp_path in (video_path, vertical_path):
                    try:
                        os.remove(tmp_path)
                    except OSError as e:
                        log.warning("Failed to remove tmp file %s: %s", tmp_path, e)

        # Update rolling stats
        update_streamer_stats(conn, name)

        if quota_exhausted:
            break

    log.info("Pipeline complete. Uploaded %d clips total.", total_uploaded)


def main():
    parser = argparse.ArgumentParser(description="Twitch-to-Shorts pipeline")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run full pipeline but skip YouTube upload")
    args = parser.parse_args()

    config = load_config()
    setup_logging(config["pipeline"].get("log_file"))
    log = logging.getLogger("main")

    if not acquire_lock():
        log.error("Pipeline is already running (lockfile: %s). Exiting.", LOCK_FILE)
        sys.exit(1)

    try:
        if args.dry_run:
            log.info("Starting Twitch-to-Shorts pipeline (DRY RUN)")
        else:
            log.info("Starting Twitch-to-Shorts pipeline")
        run_pipeline(config, dry_run=args.dry_run)
    finally:
        release_lock()


if __name__ == "__main__":
    main()

import argparse
import logging
import logging.handlers
import os
import sys
import time
from datetime import datetime, timezone

import yaml
from dotenv import load_dotenv

load_dotenv()

from src.models import Clip, FacecamConfig, StreamerConfig, PipelineConfig
from src.twitch_client import TwitchClient
from src.clip_filter import filter_and_rank
from src.dedup import filter_new_clips
from src.downloader import download_clip
from src.video_processor import crop_to_vertical, extract_thumbnail
from src.youtube_uploader import (
    get_authenticated_service,
    upload_short,
    verify_upload,
    set_thumbnail,
    QuotaExhaustedError,
)
from src.youtube_analytics import get_analytics_service, fetch_video_metrics
from src.db import (
    get_connection,
    insert_clip,
    update_streamer_stats,
    recent_upload_count,
    increment_fail_count,
    get_clips_for_metrics,
    update_youtube_metrics,
    touch_youtube_metrics_sync,
)

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


def load_config(path: str = "config.yaml") -> tuple[PipelineConfig, list[StreamerConfig], dict]:
    """Load config.yaml and return typed objects plus the raw dict for non-modeled keys."""
    with open(path) as f:
        raw = yaml.safe_load(f)

    pipeline = PipelineConfig(**raw.get("pipeline", {}))

    streamers: list[StreamerConfig] = []
    for s in raw.get("streamers", []):
        s = dict(s)  # shallow copy to avoid mutating raw config
        facecam_data = s.pop("facecam", None)
        facecam = FacecamConfig(**facecam_data) if facecam_data else None
        streamers.append(StreamerConfig(facecam=facecam, **s))

    return pipeline, streamers, raw


def validate_config(streamers: list[StreamerConfig], raw_config: dict, dry_run: bool = False):
    """Validate required config and environment for a safe run."""
    errors: list[str] = []

    if not streamers:
        errors.append("No streamers configured in config.yaml under 'streamers'")

    for s in streamers:
        name = s.name or "<unnamed>"
        if not s.name:
            errors.append("Streamer entry missing 'name'")
        if not s.twitch_id:
            errors.append(f"Streamer '{name}' missing 'twitch_id'")
        if not dry_run and not s.youtube_credentials:
            errors.append(f"Streamer '{name}' missing 'youtube_credentials'")

    youtube = raw_config.get("youtube") or {}
    if not dry_run and not youtube.get("client_secrets_file"):
        errors.append("Missing youtube.client_secrets_file in config.yaml")

    twitch = raw_config.get("twitch") or {}
    if not (os.environ.get("TWITCH_CLIENT_ID") or twitch.get("client_id")):
        errors.append("Missing Twitch client ID (set TWITCH_CLIENT_ID)")
    if not (os.environ.get("TWITCH_CLIENT_SECRET") or twitch.get("client_secret")):
        errors.append("Missing Twitch client secret (set TWITCH_CLIENT_SECRET)")

    if errors:
        raise ValueError("Invalid configuration:\n" + "\n".join(f"- {e}" for e in errors))


def clean_stale_tmp(tmp_dir: str, max_age_hours: int = 24):
    """Remove stale media/tmp files older than max_age_hours from tmp_dir."""
    if not os.path.isdir(tmp_dir):
        return
    cutoff = time.time() - max_age_hours * 3600
    suffixes = (".mp4", ".mp4.tmp", ".part", ".ytdl")
    for entry in os.scandir(tmp_dir):
        if not entry.is_file():
            continue
        name = entry.name
        if not any(name.endswith(suffix) for suffix in suffixes):
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                os.remove(entry.path)
        except OSError as e:
            log.warning("Failed to delete stale file %s: %s", entry.path, e)


log = logging.getLogger(__name__)


def _cleanup_tmp_files(*paths: str | None):
    """Best-effort cleanup for temporary media files."""
    for path in paths:
        if not path:
            continue
        try:
            os.remove(path)
        except OSError as e:
            log.warning("Failed to remove tmp file %s: %s", path, e)


def _sync_streamer_metrics(
    conn,
    streamer: str,
    client_secrets_file: str,
    credentials_file: str,
    min_age_hours: int,
    sync_interval_hours: int,
    max_videos: int,
) -> int:
    service = get_analytics_service(client_secrets_file, credentials_file)
    rows = get_clips_for_metrics(conn, streamer, min_age_hours, sync_interval_hours, max_videos)
    if not rows:
        return 0

    end_date = datetime.now(timezone.utc).date().isoformat()
    synced = 0
    for row in rows:
        youtube_id = row["youtube_id"]
        posted_at = row["posted_at"]
        if not youtube_id or not posted_at:
            continue
        start_date = datetime.fromisoformat(posted_at).date().isoformat()
        metrics = fetch_video_metrics(service, youtube_id, start_date, end_date)
        if metrics:
            update_youtube_metrics(conn, youtube_id, metrics)
            synced += 1
        else:
            touch_youtube_metrics_sync(conn, youtube_id, datetime.now(timezone.utc).isoformat())
    return synced


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            err = ctypes.get_last_error()
            # Access denied: assume the process exists to avoid breaking lock safety.
            return err == 5
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except OSError:
            return False


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
        if _pid_is_running(old_pid):
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


def run_pipeline(pipeline: PipelineConfig, streamers: list[StreamerConfig], raw_config: dict, dry_run: bool = False):
    log = logging.getLogger("pipeline")
    conn = get_connection(pipeline.db_path)
    try:
        _run_pipeline_inner(pipeline, streamers, raw_config, conn, log, dry_run=dry_run)
    finally:
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception as e:
            log.warning("WAL checkpoint failed: %s", e)
        clean_stale_tmp(pipeline.tmp_dir, max_age_hours=1)
        conn.close()


def _run_pipeline_inner(cfg: PipelineConfig, streamers: list[StreamerConfig], raw_config: dict, conn, log, dry_run: bool = False):

    twitch_client_id = os.environ.get("TWITCH_CLIENT_ID") or (raw_config.get("twitch") or {}).get("client_id")
    twitch_client_secret = os.environ.get("TWITCH_CLIENT_SECRET") or (raw_config.get("twitch") or {}).get("client_secret")
    if not twitch_client_id or not twitch_client_secret:
        log.error("TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET environment variables must be set")
        raise ValueError("TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET must be set")

    twitch = TwitchClient(twitch_client_id, twitch_client_secret)
    youtube_cfg = raw_config.get("youtube") or {}
    title_template = youtube_cfg.get("title_template")
    title_templates = youtube_cfg.get("title_templates")
    description_template = youtube_cfg.get("description_template")
    description_templates = youtube_cfg.get("description_templates")
    thumbnail_enabled = bool(youtube_cfg.get("thumbnail_enabled", False))
    thumbnail_samples = int(youtube_cfg.get("thumbnail_samples", 8))
    thumbnail_width = int(youtube_cfg.get("thumbnail_width", 1280))
    extra_tags_global = youtube_cfg.get("extra_tags") or []

    if isinstance(title_templates, str):
        title_templates = [title_templates]
    if isinstance(description_templates, str):
        description_templates = [description_templates]
    if isinstance(extra_tags_global, str):
        extra_tags_global = [extra_tags_global]

    total_fetched = 0
    total_filtered = 0
    total_downloaded = 0
    total_processed = 0
    total_uploaded = 0
    total_failed = 0

    for streamer in streamers:
        name = streamer.name
        twitch_id = streamer.twitch_id
        log.info("=== Processing streamer: %s ===", name)

        try:
            clips = twitch.fetch_clips(twitch_id, cfg.clip_lookback_hours)
        except Exception:
            log.exception("Failed to fetch clips for %s", name)
            continue

        if not clips:
            log.info("No clips found for %s", name)
            continue

        total_fetched += len(clips)

        for c in clips:
            c.streamer = name

        ranked = filter_and_rank(
            conn, clips, name,
            velocity_weight=cfg.velocity_weight,
            min_view_count=cfg.min_view_count,
            age_decay=cfg.age_decay,
            view_transform=cfg.view_transform,
            title_quality_weight=cfg.title_quality_weight,
            analytics_enabled=cfg.analytics_enabled,
        )

        new_clips = filter_new_clips(conn, ranked)
        new_clips = new_clips[:cfg.max_clips_per_streamer]
        total_filtered += len(new_clips)
        log.info("%d new clips after dedup (from %d ranked)", len(new_clips), len(ranked))

        if not new_clips:
            continue

        game_ids = [c.game_id for c in new_clips]
        try:
            game_names = twitch.get_game_names(game_ids)
        except Exception:
            log.warning("Failed to resolve game names, continuing without")
            game_names = {}
        for c in new_clips:
            c.game_name = game_names.get(c.game_id, "")

        # Upload scheduling: max 1 upload per streamer per 4 hours
        recent = recent_upload_count(conn, name, cfg.upload_spacing_hours)
        uploads_remaining = max(cfg.max_uploads_per_window - recent, 0)
        if uploads_remaining == 0:
            log.info("Skipping uploads for %s: %d uploaded in last %dh", name, recent, cfg.upload_spacing_hours)
            continue

        yt_service = None
        if not dry_run:
            try:
                yt_service = get_authenticated_service(
                    youtube_cfg["client_secrets_file"],
                    streamer.youtube_credentials,
                )
            except Exception:
                log.exception("Failed to authenticate YouTube for %s", name)
                continue

        quota_exhausted = False
        consecutive_403s = 0
        max_duration = cfg.max_clip_duration_seconds
        for clip in new_clips[:uploads_remaining]:
            if clip.duration > max_duration:
                log.info("Skipping clip %s (%.1fs > %ds max duration)", clip.id, clip.duration, max_duration)
                continue

            video_path = download_clip(clip, cfg.tmp_dir)
            if not video_path:
                increment_fail_count(conn, clip)
                total_failed += 1
                continue
            total_downloaded += 1

            vertical_path = crop_to_vertical(
                video_path, cfg.tmp_dir, cfg.max_clip_duration_seconds,
                facecam=streamer.facecam,
                facecam_mode=streamer.facecam_mode,
            )
            thumbnail_path = None
            if not vertical_path:
                increment_fail_count(conn, clip)
                total_failed += 1
                _cleanup_tmp_files(video_path)
                continue
            total_processed += 1

            if dry_run:
                log.info("[DRY RUN] Would upload clip %s: %s", clip.id, clip.title)
                total_uploaded += 1
                continue

            try:
                youtube_id = upload_short(yt_service, vertical_path, clip,
                                         category_id=streamer.category_id,
                                         privacy_status=streamer.privacy_status,
                                         title_template=title_template,
                                         title_templates=title_templates,
                                         description_template=description_template,
                                         description_templates=description_templates,
                                         extra_tags=(extra_tags_global or []) + (streamer.extra_tags or []))
            except QuotaExhaustedError:
                log.warning("YouTube quota exhausted — stopping uploads for this run")
                _cleanup_tmp_files(video_path, vertical_path, thumbnail_path)
                quota_exhausted = True
                break

            if not youtube_id:
                increment_fail_count(conn, clip)
                total_failed += 1
                consecutive_403s += 1
                _cleanup_tmp_files(video_path, vertical_path, thumbnail_path)
                if consecutive_403s >= 3:
                    log.warning("3 consecutive upload failures for %s — skipping remaining clips", name)
                    break
                continue

            if not verify_upload(yt_service, youtube_id):
                log.warning("Upload verification failed for clip %s (yt=%s), skipping DB insert", clip.id, youtube_id)
                increment_fail_count(conn, clip)
                total_failed += 1
                _cleanup_tmp_files(video_path, vertical_path, thumbnail_path)
                continue

            if thumbnail_enabled:
                thumbnail_path = extract_thumbnail(
                    vertical_path,
                    cfg.tmp_dir,
                    samples=thumbnail_samples,
                    width=thumbnail_width,
                )
                if thumbnail_path:
                    set_thumbnail(yt_service, youtube_id, thumbnail_path)

            consecutive_403s = 0
            clip.youtube_id = youtube_id
            insert_clip(conn, clip)
            total_uploaded += 1
            log.info("Uploaded clip %s -> YouTube %s", clip.id, youtube_id)

            _cleanup_tmp_files(video_path, vertical_path, thumbnail_path)

        update_streamer_stats(conn, name)

        if cfg.analytics_enabled and not dry_run:
            try:
                synced = _sync_streamer_metrics(
                    conn,
                    name,
                    youtube_cfg["client_secrets_file"],
                    streamer.youtube_credentials,
                    cfg.analytics_min_age_hours,
                    cfg.analytics_sync_interval_hours,
                    cfg.analytics_max_videos_per_run,
                )
                if synced:
                    log.info("Synced analytics for %d videos for %s", synced, name)
            except Exception:
                log.exception("Analytics sync failed for %s", name)

        if quota_exhausted:
            break

    log.info(
        "Pipeline complete: fetched=%d filtered=%d downloaded=%d processed=%d uploaded=%d failed=%d",
        total_fetched, total_filtered, total_downloaded, total_processed, total_uploaded, total_failed,
    )


def main():
    parser = argparse.ArgumentParser(description="Twitch-to-Shorts pipeline")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run full pipeline but skip YouTube upload")
    args = parser.parse_args()

    pipeline, streamers, raw_config = load_config()
    setup_logging(pipeline.log_file)
    log = logging.getLogger("main")

    try:
        validate_config(streamers, raw_config, dry_run=args.dry_run)
    except ValueError as e:
        log.error(str(e))
        sys.exit(1)

    if not acquire_lock():
        log.error("Pipeline is already running (lockfile: %s). Exiting.", LOCK_FILE)
        sys.exit(1)

    try:
        if args.dry_run:
            log.info("Starting Twitch-to-Shorts pipeline (DRY RUN)")
        else:
            log.info("Starting Twitch-to-Shorts pipeline")
        run_pipeline(pipeline, streamers, raw_config, dry_run=args.dry_run)
    finally:
        release_lock()


if __name__ == "__main__":
    main()

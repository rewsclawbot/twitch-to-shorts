import argparse
import contextlib
import logging
import logging.handlers
import os
import sys
import time
from datetime import UTC, datetime

import yaml
from dotenv import load_dotenv

load_dotenv()

from src.clip_filter import filter_and_rank  # noqa: E402
from src.db import (  # noqa: E402
    get_clips_for_metrics,
    get_connection,
    increment_fail_count,
    insert_clip,
    recent_upload_count,
    record_known_clip,
    update_instagram_id,
    update_streamer_stats,
    update_youtube_metrics,
    update_youtube_reach_metrics,
)
from src.dedup import filter_new_clips  # noqa: E402
from src.downloader import download_clip  # noqa: E402
from src.instagram_uploader import (  # noqa: E402
    InstagramAuthError,
    InstagramRateLimitError,
    upload_reel,
)
from src.models import FacecamConfig, PipelineConfig, StreamerConfig  # noqa: E402
from src.title_optimizer import optimize_title  # noqa: E402
from src.twitch_client import TwitchClient  # noqa: E402
from src.video_processor import crop_to_vertical, detect_leading_silence, extract_thumbnail  # noqa: E402
from src.youtube_analytics import fetch_video_metrics, get_analytics_service  # noqa: E402
from src.youtube_reporting import fetch_reach_metrics, get_reporting_service  # noqa: E402
from src.youtube_uploader import (  # noqa: E402
    AuthenticationError,
    ForbiddenError,
    QuotaExhaustedError,
    build_upload_title,
    check_channel_for_duplicate,
    get_authenticated_service,
    set_thumbnail,
    upload_short,
)

LOCK_FILE = os.path.join("data", "pipeline.lock")


def setup_logging(log_file: str | None = None):
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
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

    pipeline_dict = raw.get("pipeline", {})
    # Bridge captions config into PipelineConfig
    captions_cfg = raw.get("captions", {})
    if "captions_enabled" not in pipeline_dict and captions_cfg.get("enabled"):
        pipeline_dict["captions_enabled"] = captions_cfg["enabled"]
    pipeline = PipelineConfig(**pipeline_dict)

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
    pipeline_cfg = raw_config.get("pipeline") or {}
    analytics_enabled = bool(pipeline_cfg.get("analytics_enabled", False))
    if not dry_run and not youtube.get("client_secrets_file"):
        errors.append("Missing youtube.client_secrets_file in config.yaml")

    if analytics_enabled and not dry_run:
        client_secrets_file = youtube.get("client_secrets_file")
        if client_secrets_file and not os.path.exists(client_secrets_file):
            errors.append(
                f"analytics_enabled=True but youtube.client_secrets_file does not exist: {client_secrets_file}"
            )
        for s in streamers:
            name = s.name or "<unnamed>"
            if s.youtube_credentials and not os.path.exists(s.youtube_credentials):
                errors.append(
                    f"analytics_enabled=True but streamer '{name}' youtube_credentials file does not exist: {s.youtube_credentials}"
                )

    pipeline_cfg_instagram = bool(pipeline_cfg.get("instagram_enabled", False))
    if pipeline_cfg_instagram:
        for s in streamers:
            name = s.name or "<unnamed>"
            if not getattr(s, 'instagram_credentials', None):
                log.warning("instagram_enabled=True but streamer '%s' has no instagram_credentials", name)

    if not os.environ.get("TWITCH_CLIENT_ID"):
        errors.append("Missing Twitch client ID (set TWITCH_CLIENT_ID)")
    if not os.environ.get("TWITCH_CLIENT_SECRET"):
        errors.append("Missing Twitch client secret (set TWITCH_CLIENT_SECRET)")

    if errors:
        raise ValueError("Invalid configuration:\n" + "\n".join(f"- {e}" for e in errors))


def clean_stale_tmp(tmp_dir: str, max_age_hours: int = 24):
    """Remove stale media/tmp files older than max_age_hours from tmp_dir."""
    if not os.path.isdir(tmp_dir):
        return
    cutoff = time.time() - max_age_hours * 3600
    suffixes = (".mp4", ".mp4.tmp", ".part", ".ytdl", ".ass", ".wav", ".flac")
    for entry in os.scandir(tmp_dir):
        if entry.is_symlink() or not entry.is_file():
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
        log.info("Analytics sync for %s: 0 videos eligible", streamer)
        return 0

    end_date = datetime.now(UTC).date().isoformat()
    synced_ids: set[str] = set()
    pending_reach: dict[str, str] = {}
    analytics_ok = 0
    analytics_fail = 0
    for row in rows:
        youtube_id = row["youtube_id"]
        posted_at = row["posted_at"]
        if not youtube_id or not posted_at:
            continue
        start_date = datetime.fromisoformat(posted_at).date().isoformat()
        try:
            metrics = fetch_video_metrics(service, youtube_id, start_date, end_date)
        except Exception:
            log.warning("Analytics metrics failed for %s", youtube_id, exc_info=True)
            metrics = None
        if metrics:
            analytics_ok += 1
            update_youtube_metrics(conn, youtube_id, metrics)
            synced_ids.add(youtube_id)
            if metrics.get("yt_impressions") is None or metrics.get("yt_impressions_ctr") is None:
                pending_reach[youtube_id] = start_date
        else:
            analytics_fail += 1
            pending_reach[youtube_id] = start_date

    reporting_ok = 0
    if pending_reach:
        reach_metrics: dict[str, dict] = {}
        try:
            reporting_service = get_reporting_service(client_secrets_file, credentials_file)
            min_start_date = min(pending_reach.values())
            reach_metrics = fetch_reach_metrics(
                reporting_service,
                set(pending_reach.keys()),
                min_start_date,
                end_date,
            )
            if reach_metrics:
                log.info("Reporting reach metrics found for %d videos", len(reach_metrics))
        except Exception:
            log.warning("Reporting API reach sync failed for %s", streamer, exc_info=True)

        now = datetime.now(UTC).isoformat()
        for youtube_id, data in reach_metrics.items():
            update_youtube_reach_metrics(
                conn,
                youtube_id,
                data.get("yt_impressions"),
                data.get("yt_impressions_ctr"),
                now,
            )
            synced_ids.add(youtube_id)
            reporting_ok += 1

    log.info(
        "Analytics sync for %s: %d eligible, analytics_ok=%d analytics_fail=%d reporting_ok=%d synced=%d",
        streamer, len(rows), analytics_ok, analytics_fail, reporting_ok, len(synced_ids),
    )
    return len(synced_ids)


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        windll_ctor = getattr(ctypes, "WinDLL", None)
        if windll_ctor is None:
            return False
        kernel32 = windll_ctor("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            get_last_error = getattr(ctypes, "get_last_error", None)
            err = int(get_last_error()) if callable(get_last_error) else 0
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
    # Atomic replace: write new PID to temp file, then os.replace() over the lock.
    # This avoids the TOCTOU race of remove-then-create.
    tmp_lock = LOCK_FILE + ".tmp"
    try:
        fd = os.open(tmp_lock, os.O_CREAT | os.O_TRUNC | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        os.replace(tmp_lock, LOCK_FILE)
        return True
    except OSError:
        return False


def release_lock():
    with contextlib.suppress(OSError):
        os.remove(LOCK_FILE)


def write_github_summary(run_result: dict, conn):
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    from src.db import get_todays_runs

    today_runs = get_todays_runs(conn)
    today_uploaded = sum((row["total_uploaded"] or 0) for row in today_runs)
    today_failed = sum((row["total_failed"] or 0) for row in today_runs)

    totals = run_result["totals"]
    lines = [
        "## Pipeline Run Summary",
        "",
        f"| Metric | This Run | Today ({len(today_runs)} runs) |",
        "|--------|----------|-------------------------|",
        f"| Uploaded | {totals['uploaded']} | {today_uploaded} |",
        f"| Failed | {totals['failed']} | {today_failed} |",
        f"| Fetched | {totals['fetched']} | - |",
        f"| Filtered | {totals['filtered']} | - |",
        "",
    ]

    streamer_results = run_result.get("streamer_results") or []
    if streamer_results:
        lines += [
            "### Per-Streamer Detail",
            "",
            "| Streamer | Uploaded | Failed | Skip Reason |",
            "|----------|----------|--------|-------------|",
        ]
        for sr in streamer_results:
            lines.append(
                f"| {sr['streamer']} | {sr['uploaded']} | {sr['failed']} | {sr['skip_reason'] or '-'} |"
            )
        lines.append("")

    if len(today_runs) > 1:
        lines += [
            "### Today's Runs",
            "",
            "| Time (UTC) | Uploaded | Failed | Trigger |",
            "|------------|----------|--------|---------|",
        ]
        for row in today_runs:
            lines.append(
                f"| {row['started_at'][:19]} | {row['total_uploaded']} | {row['total_failed']} | {row['trigger']} |"
            )
        lines.append("")

    with open(summary_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def run_pipeline(pipeline: PipelineConfig, streamers: list[StreamerConfig], raw_config: dict, dry_run: bool = False):
    log = logging.getLogger("pipeline")
    conn = get_connection(pipeline.db_path)
    try:
        result = _run_pipeline_inner(pipeline, streamers, raw_config, conn, log, dry_run=dry_run)
        if result:
            write_github_summary(result, conn)
    finally:
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception as e:
            log.warning("WAL checkpoint failed: %s", e)
        clean_stale_tmp(pipeline.tmp_dir, max_age_hours=1)
        conn.close()


def _process_single_clip(clip, yt_service, conn, cfg, streamer, log, dry_run,
                         title_template, title_templates, description_template,
                         description_templates, extra_tags_global,
                         thumbnail_enabled, thumbnail_samples, thumbnail_width,
                         captions_enabled=False,
                         ig_credentials=None, ig_caption_template=None,
                         ig_caption_templates=None, ig_hashtags=None,
                         ig_rate_limited_state=None):
    """Process a single clip: download, crop, upload, verify, thumbnail.

    Returns a tuple of (result, youtube_id) where result is one of:
        "downloaded_fail" - download failed
        "processed_fail"  - video processing failed
        "dry_run"         - dry run, no upload
        "duplicate"       - already on channel
        "quota_exhausted" - YouTube quota hit
        "forbidden"       - 403 from YouTube
        "auth_error"      - authentication/credential failure
        "upload_fail"     - upload returned no ID
        "uploaded"        - successful upload
    """
    # Defense-in-depth: check YouTube channel BEFORE download/process to save resources
    planned_title = None
    if yt_service and not dry_run:
        planned_title = build_upload_title(clip, title_template, title_templates)
        if os.environ.get("TITLE_OPTIMIZER_ENABLED", "false").strip().lower() == "true":
            optimized_title = optimize_title(
                planned_title,
                streamer.name,
                clip.game_name or "",
                clip.id,
            )
            if optimized_title != planned_title:
                log.info(
                    "Title optimized for %s: '%s' -> '%s'",
                    clip.id,
                    planned_title,
                    optimized_title,
                )
                planned_title = optimized_title
        cache_key = clip.channel_key or streamer.youtube_credentials or streamer.name
        existing_yt_id = check_channel_for_duplicate(yt_service, planned_title, cache_key=cache_key)
        if existing_yt_id:
            log.warning("Clip %s already on channel as %s — recording and skipping", clip.id, existing_yt_id)
            clip.youtube_id = existing_yt_id
            record_known_clip(conn, clip)
            return "duplicate", None

    video_path = download_clip(clip, cfg.tmp_dir)
    if not video_path:
        increment_fail_count(conn, clip)
        return "downloaded_fail", None

    # Detect leading silence once — used by both captioner and cropper
    silence_offset = detect_leading_silence(video_path)

    subtitle_path = None
    if captions_enabled:
        from src.captioner import generate_captions
        subtitle_path = generate_captions(video_path, cfg.tmp_dir, silence_offset=silence_offset)
        if subtitle_path:
            log.info("Generated captions for %s", clip.id)

    vertical_path = crop_to_vertical(
        video_path, cfg.tmp_dir, cfg.max_clip_duration_seconds,
        facecam=streamer.facecam,
        facecam_mode=streamer.facecam_mode,
        subtitle_path=subtitle_path,
        silence_offset=silence_offset,
    )
    thumbnail_path = None
    if not vertical_path:
        increment_fail_count(conn, clip)
        _cleanup_tmp_files(video_path, subtitle_path)
        return "processed_fail", None

    if dry_run:
        log.info("[DRY RUN] Would upload clip %s: %s", clip.id, clip.title)
        _cleanup_tmp_files(video_path, vertical_path, subtitle_path)
        return "dry_run", None

    try:
        youtube_id = upload_short(yt_service, vertical_path, clip,
                                  category_id=streamer.category_id,
                                  privacy_status=streamer.privacy_status,
                                  title_template=title_template,
                                  title_templates=title_templates,
                                  description_template=description_template,
                                  description_templates=description_templates,
                                  extra_tags=(extra_tags_global or []) + (streamer.extra_tags or []),
                                  prebuilt_title=planned_title)
    except QuotaExhaustedError:
        log.warning("YouTube quota exhausted — stopping uploads for this run")
        _cleanup_tmp_files(video_path, vertical_path, thumbnail_path, subtitle_path)
        return "quota_exhausted", None
    except ForbiddenError:
        increment_fail_count(conn, clip)
        _cleanup_tmp_files(video_path, vertical_path, thumbnail_path, subtitle_path)
        return "forbidden", None
    except AuthenticationError:
        log.error("Authentication failed — aborting uploads for this streamer")
        _cleanup_tmp_files(video_path, vertical_path, thumbnail_path, subtitle_path)
        return "auth_error", None

    if not youtube_id:
        increment_fail_count(conn, clip)
        _cleanup_tmp_files(video_path, vertical_path, thumbnail_path, subtitle_path)
        return "upload_fail", None

    # Record to DB immediately after upload succeeds — before verify/thumbnail.
    # A phantom DB entry is trivially cleanable; a duplicate upload is not.
    clip.youtube_id = youtube_id
    insert_clip(conn, clip)
    log.info("Uploaded clip %s -> YouTube %s", clip.id, youtube_id)

    # --- Instagram Upload (independent, failure does not block YouTube) ---
    if ig_credentials and cfg.instagram_enabled:
        try:
            ig_media_id = upload_reel(
                vertical_path, clip, ig_credentials,
                caption_template=ig_caption_template,
                caption_templates=ig_caption_templates,
                hashtags=ig_hashtags,
                prebuilt_title=planned_title,
            )
            if ig_media_id:
                update_instagram_id(conn, clip.id, ig_media_id)
                log.info("Uploaded clip %s -> Instagram %s", clip.id, ig_media_id)
        except InstagramAuthError:
            log.error("Instagram auth failed for %s", clip.id)
        except InstagramRateLimitError:
            log.warning("Instagram rate limit hit, skipping remaining IG uploads")
            if isinstance(ig_rate_limited_state, list) and ig_rate_limited_state:
                ig_rate_limited_state[0] = True
        except Exception:
            log.exception("Instagram upload failed for %s", clip.id)

    if thumbnail_enabled:
        thumbnail_path = extract_thumbnail(
            vertical_path,
            cfg.tmp_dir,
            samples=thumbnail_samples,
            width=thumbnail_width,
        )
        if thumbnail_path:
            set_thumbnail(yt_service, youtube_id, thumbnail_path)

    _cleanup_tmp_files(video_path, vertical_path, thumbnail_path, subtitle_path)
    return "uploaded", youtube_id


def _process_streamer(streamer, twitch, cfg, conn, log, dry_run,
                      client_secrets_file, title_template, title_templates,
                      description_template, description_templates,
                      extra_tags_global, thumbnail_enabled, thumbnail_samples,
                      thumbnail_width, captions_enabled=False,
                      ig_caption_template=None, ig_caption_templates=None,
                      ig_hashtags=None):
    """Process all clips for a single streamer.

    Returns a tuple of
    (fetched, filtered, downloaded, processed, uploaded, failed, quota_exhausted, skip_reason).
    """
    name = streamer.name
    twitch_id = streamer.twitch_id
    channel_key = streamer.youtube_credentials or name
    # Per-streamer captions override
    if streamer.captions is not None:
        captions_enabled = streamer.captions
    log.info("=== Processing streamer: %s ===", name)

    fetched = filtered = downloaded = processed = uploaded = failed = 0
    skip_reason = None

    def _finalize_and_return(quota_exhausted: bool = False):
        if cfg.analytics_enabled and not dry_run:
            try:
                synced = _sync_streamer_metrics(
                    conn,
                    name,
                    client_secrets_file,
                    streamer.youtube_credentials,
                    cfg.analytics_min_age_hours,
                    cfg.analytics_sync_interval_hours,
                    cfg.analytics_max_videos_per_run,
                )
                if synced:
                    log.info("Synced analytics for %d videos for %s", synced, name)
            except Exception:
                log.exception("Analytics sync failed for %s", name)
        return fetched, filtered, downloaded, processed, uploaded, failed, quota_exhausted, skip_reason

    try:
        clips = twitch.fetch_clips(twitch_id, cfg.clip_lookback_hours)
    except Exception:
        log.exception("Failed to fetch clips for %s", name)
        skip_reason = "fetch_error"
        return _finalize_and_return(False)

    if not clips:
        log.info("No clips found for %s", name)
        skip_reason = "no_clips"
        return _finalize_and_return(False)

    fetched = len(clips)

    for c in clips:
        c.streamer = name
        c.channel_key = channel_key

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
    max_duration = cfg.max_clip_duration_seconds
    if new_clips:
        too_long = [c for c in new_clips if c.duration > max_duration]
        if too_long:
            log.info("Skipping %d clips over %ds before max_clips_per_streamer cap", len(too_long), max_duration)
        new_clips = [c for c in new_clips if c.duration <= max_duration]
    new_clips = new_clips[:cfg.max_clips_per_streamer]
    filtered = len(new_clips)
    log.info("%d new clips after dedup (from %d ranked)", len(new_clips), len(ranked))

    if not new_clips:
        skip_reason = "no_new_clips"
        return _finalize_and_return(False)

    # Upload scheduling: check BEFORE game name fetch to avoid wasted API calls
    recent = recent_upload_count(conn, name, cfg.upload_spacing_hours, channel_key=channel_key)
    uploads_remaining = max(cfg.max_uploads_per_window - recent, 0)
    if uploads_remaining == 0:
        log.info("Skipping uploads for %s: %d uploaded in last %dh", name, recent, cfg.upload_spacing_hours)
        skip_reason = "spacing_limited"
        return _finalize_and_return(False)

    game_ids = [c.game_id for c in new_clips]
    try:
        game_names = twitch.get_game_names(game_ids)
    except Exception:
        log.warning("Failed to resolve game names, continuing without")
        game_names = {}
    for c in new_clips:
        c.game_name = game_names.get(c.game_id, "")

    yt_service = None
    if not dry_run:
        try:
            yt_service = get_authenticated_service(
                client_secrets_file,
                streamer.youtube_credentials,
            )
        except Exception as e:
            log.exception("Failed to authenticate YouTube for %s", name)
            raise RuntimeError(f"YouTube authentication failed for streamer '{name}'") from e

    quota_exhausted = False
    consecutive_403s = 0
    ig_rate_limited_state = [False]
    for clip in new_clips:
        if uploads_remaining <= 0:
            break

        result, _ = _process_single_clip(
            clip, yt_service, conn, cfg, streamer, log, dry_run,
            title_template, title_templates, description_template,
            description_templates, extra_tags_global,
            thumbnail_enabled, thumbnail_samples, thumbnail_width,
            captions_enabled=captions_enabled,
            ig_credentials=streamer.instagram_credentials if not ig_rate_limited_state[0] else None,
            ig_caption_template=ig_caption_template,
            ig_caption_templates=ig_caption_templates,
            ig_hashtags=ig_hashtags,
            ig_rate_limited_state=ig_rate_limited_state,
        )

        if result == "downloaded_fail":
            # Download failed — nothing was downloaded or processed
            failed += 1
        elif result == "processed_fail":
            # Downloaded but crop/processing failed
            downloaded += 1
            failed += 1
        elif result == "dry_run":
            downloaded += 1
            processed += 1
            uploaded += 1
        elif result == "duplicate":
            pass  # dedup happens before download — no resources consumed
        elif result == "quota_exhausted":
            downloaded += 1
            processed += 1
            quota_exhausted = True
            break
        elif result == "auth_error":
            downloaded += 1
            processed += 1
            failed += 1
            break
        elif result == "forbidden":
            downloaded += 1
            processed += 1
            failed += 1
            consecutive_403s += 1
            if consecutive_403s >= 3:
                log.warning("3 consecutive upload failures for %s — skipping remaining clips", name)
                break
        elif result == "upload_fail":
            downloaded += 1
            processed += 1
            failed += 1
            consecutive_403s = 0
        elif result == "uploaded":
            downloaded += 1
            processed += 1
            uploaded += 1
            uploads_remaining -= 1
            consecutive_403s = 0

    update_streamer_stats(conn, name)

    return _finalize_and_return(quota_exhausted)


def _run_pipeline_inner(cfg: PipelineConfig, streamers: list[StreamerConfig], raw_config: dict, conn, log, dry_run: bool = False):

    twitch_client_id = os.environ.get("TWITCH_CLIENT_ID")
    twitch_client_secret = os.environ.get("TWITCH_CLIENT_SECRET")
    if not twitch_client_id or not twitch_client_secret:
        log.error("TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET environment variables must be set")
        raise ValueError("TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET must be set")

    twitch = TwitchClient(twitch_client_id, twitch_client_secret)
    youtube_cfg = raw_config.get("youtube") or {}
    client_secrets_file = youtube_cfg.get("client_secrets_file")
    if not dry_run and not client_secrets_file:
        raise ValueError("Missing youtube.client_secrets_file in config.yaml")
    title_template = youtube_cfg.get("title_template")
    title_templates = youtube_cfg.get("title_templates")
    description_template = youtube_cfg.get("description_template")
    description_templates = youtube_cfg.get("description_templates")
    thumbnail_enabled = bool(youtube_cfg.get("thumbnail_enabled", False))
    thumbnail_samples = int(youtube_cfg.get("thumbnail_samples", 8))
    thumbnail_width = int(youtube_cfg.get("thumbnail_width", 1280))
    extra_tags_global = youtube_cfg.get("extra_tags") or []
    captions_enabled = cfg.captions_enabled
    if captions_enabled and not os.environ.get("DEEPGRAM_API_KEY"):
        log.warning("captions_enabled=True but DEEPGRAM_API_KEY not set — captions will be skipped")

    if isinstance(title_templates, str):
        title_templates = [title_templates]
    if isinstance(description_templates, str):
        description_templates = [description_templates]
    if isinstance(extra_tags_global, str):
        extra_tags_global = [extra_tags_global]

    ig_cfg = raw_config.get("instagram") or {}
    ig_caption_template = ig_cfg.get("caption_template")
    ig_caption_templates = ig_cfg.get("caption_templates")
    ig_hashtags = ig_cfg.get("hashtags")
    if isinstance(ig_caption_templates, str):
        ig_caption_templates = [ig_caption_templates]
    if isinstance(ig_hashtags, str):
        ig_hashtags = [ig_hashtags]

    total_fetched = 0
    total_filtered = 0
    total_downloaded = 0
    total_processed = 0
    total_uploaded = 0
    total_failed = 0
    from src.db import finish_pipeline_run, insert_pipeline_run

    started_at = datetime.now(UTC).isoformat()
    trigger = os.environ.get("PIPELINE_TRIGGER", "local")
    run_id = insert_pipeline_run(conn, started_at, trigger)
    streamer_results = []

    def _totals() -> dict:
        return {
            "fetched": total_fetched,
            "filtered": total_filtered,
            "downloaded": total_downloaded,
            "processed": total_processed,
            "uploaded": total_uploaded,
            "failed": total_failed,
        }

    try:
        for streamer in streamers:
            fetched, filtered, downloaded, processed, uploaded, failed, quota_exhausted, skip_reason = _process_streamer(
                streamer, twitch, cfg, conn, log, dry_run,
                client_secrets_file, title_template, title_templates,
                description_template, description_templates,
                extra_tags_global, thumbnail_enabled, thumbnail_samples,
                thumbnail_width, captions_enabled=captions_enabled,
                ig_caption_template=ig_caption_template,
                ig_caption_templates=ig_caption_templates,
                ig_hashtags=ig_hashtags,
            )
            total_fetched += fetched
            total_filtered += filtered
            total_downloaded += downloaded
            total_processed += processed
            total_uploaded += uploaded
            total_failed += failed
            streamer_results.append({
                "streamer": streamer.name,
                "fetched": fetched,
                "filtered": filtered,
                "downloaded": downloaded,
                "processed": processed,
                "uploaded": uploaded,
                "failed": failed,
                "skip_reason": skip_reason,
            })
            if quota_exhausted:
                break

        log.info(
            "Pipeline complete: fetched=%d filtered=%d downloaded=%d processed=%d uploaded=%d failed=%d",
            total_fetched, total_filtered, total_downloaded, total_processed, total_uploaded, total_failed,
        )
        finish_pipeline_run(conn, run_id, datetime.now(UTC).isoformat(), _totals(), streamer_results)
    except Exception:
        finish_pipeline_run(conn, run_id, datetime.now(UTC).isoformat(), _totals(), streamer_results)
        raise

    return {
        "run_id": run_id,
        "totals": _totals(),
        "streamer_results": streamer_results,
    }


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

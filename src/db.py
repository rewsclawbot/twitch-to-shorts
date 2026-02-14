import os
import sqlite3
from datetime import UTC, datetime, timedelta

from src.models import Clip


def get_connection(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        init_schema(conn)
    except Exception:
        conn.close()
        raise
    return conn


def init_schema(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS clips (
            clip_id TEXT PRIMARY KEY,
            streamer TEXT NOT NULL,
            channel_key TEXT,
            title TEXT,
            title_variant TEXT,
            view_count INTEGER,
            created_at TEXT,
            game_name TEXT,
            posted_at TEXT,
            youtube_id TEXT,
            fail_count INTEGER DEFAULT 0,
            last_failed_at TEXT,
            yt_views INTEGER,
            yt_estimated_minutes_watched REAL,
            yt_avg_view_duration REAL,
            yt_avg_view_percentage REAL,
            yt_impressions INTEGER,
            yt_impressions_ctr REAL,
            yt_last_sync TEXT
        );

        CREATE TABLE IF NOT EXISTS streamer_stats (
            streamer TEXT PRIMARY KEY,
            avg_views_30d REAL DEFAULT 0,
            clip_count_30d INTEGER DEFAULT 0,
            last_updated TEXT
        );

        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            trigger TEXT,
            total_fetched INTEGER DEFAULT 0,
            total_filtered INTEGER DEFAULT 0,
            total_downloaded INTEGER DEFAULT 0,
            total_processed INTEGER DEFAULT 0,
            total_uploaded INTEGER DEFAULT 0,
            total_failed INTEGER DEFAULT 0,
            streamer_details TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_clips_streamer ON clips(streamer);
        CREATE INDEX IF NOT EXISTS idx_clips_posted ON clips(posted_at);
        CREATE INDEX IF NOT EXISTS idx_runs_started ON pipeline_runs(started_at);
    """)
    # Migration: add columns if missing (existing cached DBs may lack them)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(clips)").fetchall()}
    if "fail_count" not in cols:
        conn.execute("ALTER TABLE clips ADD COLUMN fail_count INTEGER DEFAULT 0")
    if "channel_key" not in cols:
        conn.execute("ALTER TABLE clips ADD COLUMN channel_key TEXT")
    if "title_variant" not in cols:
        conn.execute("ALTER TABLE clips ADD COLUMN title_variant TEXT")
    if "game_name" not in cols:
        conn.execute("ALTER TABLE clips ADD COLUMN game_name TEXT")
    if "last_failed_at" not in cols:
        conn.execute("ALTER TABLE clips ADD COLUMN last_failed_at TEXT")
    # Index on channel_key must be created AFTER the migration adds the column
    conn.execute("CREATE INDEX IF NOT EXISTS idx_clips_channel ON clips(channel_key)")
    if "yt_views" not in cols:
        conn.execute("ALTER TABLE clips ADD COLUMN yt_views INTEGER")
    if "yt_estimated_minutes_watched" not in cols:
        conn.execute("ALTER TABLE clips ADD COLUMN yt_estimated_minutes_watched REAL")
    if "yt_avg_view_duration" not in cols:
        conn.execute("ALTER TABLE clips ADD COLUMN yt_avg_view_duration REAL")
    if "yt_avg_view_percentage" not in cols:
        conn.execute("ALTER TABLE clips ADD COLUMN yt_avg_view_percentage REAL")
    if "yt_impressions" not in cols:
        conn.execute("ALTER TABLE clips ADD COLUMN yt_impressions INTEGER")
    if "yt_impressions_ctr" not in cols:
        conn.execute("ALTER TABLE clips ADD COLUMN yt_impressions_ctr REAL")
    if "yt_last_sync" not in cols:
        conn.execute("ALTER TABLE clips ADD COLUMN yt_last_sync TEXT")
    if "duration" not in cols:
        conn.execute("ALTER TABLE clips ADD COLUMN duration REAL")
    if "vod_id" not in cols:
        conn.execute("ALTER TABLE clips ADD COLUMN vod_id TEXT")
    if "vod_offset" not in cols:
        conn.execute("ALTER TABLE clips ADD COLUMN vod_offset INTEGER")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_clips_vod ON clips(vod_id, vod_offset)")
    if "instagram_id" not in cols:
        conn.execute("ALTER TABLE clips ADD COLUMN instagram_id TEXT")


def clip_overlaps(conn: sqlite3.Connection, streamer: str, created_at: str, window_seconds: int = 30, exclude_clip_id: str | None = None) -> bool:
    """Check if a clip from the same streamer exists within window_seconds of created_at."""
    # Coarse pre-filter using ISO string comparison to narrow the scan,
    # then precise julianday() check on the reduced set.
    try:
        dt = datetime.fromisoformat(created_at)
        lower = (dt - timedelta(seconds=window_seconds)).isoformat()
        upper = (dt + timedelta(seconds=window_seconds)).isoformat()
    except (ValueError, TypeError):
        lower = ""
        upper = "9999-12-31T23:59:59"
    query = """SELECT 1 FROM clips WHERE streamer = ?
           AND created_at >= ? AND created_at <= ?
           AND ABS(julianday(created_at) - julianday(?)) * 86400 < ?"""
    params: list = [streamer, lower, upper, created_at, window_seconds]
    if exclude_clip_id:
        query += " AND clip_id != ?"
        params.append(exclude_clip_id)
    row = conn.execute(query, params).fetchone()
    return row is not None


def vod_overlaps(
    conn: sqlite3.Connection,
    vod_id: str | None,
    vod_offset: int | None,
    duration: float,
    exclude_clip_id: str | None = None,
) -> bool:
    """Check if any existing clip overlaps the given VOD time range.

    Returns False immediately if vod_id is None (VOD deleted — fall back to created_at dedup).
    Uses standard interval overlap: A overlaps B iff A.start < B.end AND B.start < A.end.
    """
    if vod_id is None or vod_offset is None:
        return False
    query = """SELECT 1 FROM clips
               WHERE vod_id = ?
                 AND vod_offset IS NOT NULL
                 AND duration IS NOT NULL
                 AND ? < vod_offset + duration
                 AND vod_offset < ? + ?"""
    params: list = [vod_id, vod_offset, vod_offset, duration]
    if exclude_clip_id:
        query += " AND clip_id != ?"
        params.append(exclude_clip_id)
    row = conn.execute(query, params).fetchone()
    return row is not None


def recent_upload_count(
    conn: sqlite3.Connection,
    streamer: str,
    hours: int = 4,
    channel_key: str | None = None,
) -> int:
    """Count clips uploaded for a streamer/channel within the last N hours."""
    cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    if channel_key:
        row = conn.execute(
            """SELECT COUNT(*) as cnt
               FROM clips
               WHERE posted_at >= ?
                 AND youtube_id IS NOT NULL
                 AND (channel_key = ? OR (channel_key IS NULL AND streamer = ?))""",
            (cutoff, channel_key, streamer),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM clips WHERE streamer = ? AND posted_at >= ? AND youtube_id IS NOT NULL",
            (streamer, cutoff),
        ).fetchone()
    return row["cnt"] if row else 0


def insert_pipeline_run(conn: sqlite3.Connection, started_at: str, trigger: str = "local") -> int:
    """Insert a new pipeline run row and return its id."""
    cur = conn.execute(
        "INSERT INTO pipeline_runs (started_at, trigger) VALUES (?, ?)",
        (started_at, trigger),
    )
    conn.commit()
    row_id = cur.lastrowid
    if row_id is None:
        raise RuntimeError("Failed to insert pipeline run row")
    return int(row_id)


def finish_pipeline_run(conn: sqlite3.Connection, run_id: int, finished_at: str, totals: dict, streamer_details: list[dict]):
    """Update a pipeline run with final totals and per-streamer details."""
    import json

    conn.execute(
        """UPDATE pipeline_runs
           SET finished_at=?, total_fetched=?, total_filtered=?,
               total_downloaded=?, total_processed=?,
               total_uploaded=?, total_failed=?, streamer_details=?
           WHERE id=?""",
        (
            finished_at,
            totals.get("fetched", 0),
            totals.get("filtered", 0),
            totals.get("downloaded", 0),
            totals.get("processed", 0),
            totals.get("uploaded", 0),
            totals.get("failed", 0),
            json.dumps(streamer_details),
            run_id,
        ),
    )
    conn.commit()


def get_todays_runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return all pipeline runs that started today (UTC)."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    return conn.execute(
        "SELECT * FROM pipeline_runs WHERE started_at >= ? ORDER BY started_at",
        (today + "T00:00:00",),
    ).fetchall()


def insert_clip(conn: sqlite3.Connection, clip: Clip):
    conn.execute(
        """INSERT INTO clips (clip_id, streamer, channel_key, title, title_variant, view_count, created_at, game_name, posted_at, youtube_id, duration, vod_id, vod_offset, instagram_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(clip_id) DO UPDATE SET
               youtube_id = excluded.youtube_id,
               posted_at = excluded.posted_at,
               view_count = excluded.view_count,
               title = excluded.title,
               title_variant = COALESCE(NULLIF(excluded.title_variant, ''), clips.title_variant),
               channel_key = excluded.channel_key,
               game_name = COALESCE(NULLIF(excluded.game_name, ''), clips.game_name),
               duration = COALESCE(excluded.duration, clips.duration),
               vod_id = COALESCE(excluded.vod_id, clips.vod_id),
               vod_offset = COALESCE(excluded.vod_offset, clips.vod_offset),
               instagram_id = COALESCE(excluded.instagram_id, clips.instagram_id)""",
        (clip.id, clip.streamer, clip.channel_key, clip.title, getattr(clip, "title_variant", ""), clip.view_count,
         clip.created_at, clip.game_name, datetime.now(UTC).isoformat(), clip.youtube_id,
         clip.duration, getattr(clip, 'vod_id', None), getattr(clip, 'vod_offset', None),
         getattr(clip, 'instagram_id', None)),
    )
    conn.commit()


def record_known_clip(conn: sqlite3.Connection, clip: Clip):
    """Record a clip that's already on YouTube (duplicate). Does not set posted_at."""
    conn.execute(
        """INSERT INTO clips (clip_id, streamer, channel_key, title, title_variant, view_count, created_at, game_name, youtube_id, duration, vod_id, vod_offset, instagram_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(clip_id) DO UPDATE SET
               youtube_id = COALESCE(clips.youtube_id, excluded.youtube_id),
               view_count = excluded.view_count,
               title = excluded.title,
               title_variant = COALESCE(NULLIF(excluded.title_variant, ''), clips.title_variant),
               channel_key = excluded.channel_key,
               game_name = COALESCE(NULLIF(excluded.game_name, ''), clips.game_name),
               duration = COALESCE(excluded.duration, clips.duration),
               vod_id = COALESCE(excluded.vod_id, clips.vod_id),
               vod_offset = COALESCE(excluded.vod_offset, clips.vod_offset),
               instagram_id = COALESCE(excluded.instagram_id, clips.instagram_id)""",
        (clip.id, clip.streamer, clip.channel_key, clip.title, getattr(clip, "title_variant", ""), clip.view_count, clip.created_at, clip.game_name, clip.youtube_id,
         clip.duration, getattr(clip, 'vod_id', None), getattr(clip, 'vod_offset', None),
         getattr(clip, 'instagram_id', None)),
    )
    conn.commit()


def get_streamer_stats(conn: sqlite3.Connection, streamer: str) -> dict | None:
    row = conn.execute("SELECT * FROM streamer_stats WHERE streamer = ?", (streamer,)).fetchone()
    return dict(row) if row else None


def update_streamer_stats(conn: sqlite3.Connection, streamer: str):
    cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    row = conn.execute(
        "SELECT AVG(view_count) as avg_views, COUNT(*) as cnt FROM clips WHERE streamer = ? AND created_at >= ?",
        (streamer, cutoff),
    ).fetchone()
    avg_views = row["avg_views"] or 0
    count = row["cnt"] or 0
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """INSERT INTO streamer_stats (streamer, avg_views_30d, clip_count_30d, last_updated)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(streamer) DO UPDATE SET
               avg_views_30d = excluded.avg_views_30d,
               clip_count_30d = excluded.clip_count_30d,
               last_updated = excluded.last_updated""",
        (streamer, avg_views, count, now),
    )
    conn.commit()


def increment_fail_count(conn: sqlite3.Connection, clip: Clip):
    """Record a processing failure. Upserts clip row and increments fail_count."""
    failed_at = datetime.now(UTC).isoformat()
    conn.execute(
        """INSERT INTO clips (clip_id, streamer, channel_key, title, created_at, game_name, fail_count, last_failed_at)
           VALUES (?, ?, ?, ?, ?, ?, 1, ?)
           ON CONFLICT(clip_id) DO UPDATE SET
               fail_count = fail_count + 1,
               last_failed_at = excluded.last_failed_at,
               channel_key = COALESCE(excluded.channel_key, clips.channel_key),
               title = COALESCE(excluded.title, clips.title),
               game_name = COALESCE(NULLIF(excluded.game_name, ''), clips.game_name),
               created_at = COALESCE(clips.created_at, excluded.created_at)""",
        (clip.id, clip.streamer, clip.channel_key, clip.title, clip.created_at, clip.game_name, failed_at),
    )
    conn.commit()


def update_last_failed_at(conn: sqlite3.Connection, clip_id: str, failed_at: str | None = None):
    """Update last_failed_at for a clip (uses current UTC time by default)."""
    effective_failed_at = failed_at or datetime.now(UTC).isoformat()
    conn.execute(
        "UPDATE clips SET last_failed_at = ? WHERE clip_id = ?",
        (effective_failed_at, clip_id),
    )
    conn.commit()


def get_clips_for_metrics(
    conn: sqlite3.Connection,
    streamer: str,
    min_age_hours: int,
    sync_interval_hours: int,
    limit: int,
) -> list[sqlite3.Row]:
    now = datetime.now(UTC)
    min_posted_at = (now - timedelta(hours=min_age_hours)).isoformat()
    min_sync = (now - timedelta(hours=sync_interval_hours)).isoformat()
    rows = conn.execute(
        """SELECT clip_id, youtube_id, posted_at
           FROM clips
           WHERE streamer = ?
             AND youtube_id IS NOT NULL
             AND posted_at <= ?
             AND (yt_last_sync IS NULL OR yt_last_sync <= ?)
           ORDER BY posted_at DESC
           LIMIT ?""",
        (streamer, min_posted_at, min_sync, limit),
    ).fetchall()
    return rows


def update_youtube_metrics(conn: sqlite3.Connection, youtube_id: str, metrics: dict):
    conn.execute(
        """UPDATE clips
           SET yt_views = CASE WHEN ? IS NULL THEN yt_views
                               ELSE MAX(?, COALESCE(yt_views, 0)) END,
               yt_estimated_minutes_watched = CASE WHEN ? IS NULL THEN yt_estimated_minutes_watched
                                                    ELSE MAX(?, COALESCE(yt_estimated_minutes_watched, 0)) END,
               yt_avg_view_duration = COALESCE(?, yt_avg_view_duration),
               yt_avg_view_percentage = COALESCE(?, yt_avg_view_percentage),
               yt_impressions = CASE WHEN ? IS NULL THEN yt_impressions
                                     ELSE MAX(?, COALESCE(yt_impressions, 0)) END,
               yt_impressions_ctr = COALESCE(?, yt_impressions_ctr),
               yt_last_sync = ?
           WHERE youtube_id = ?""",
        (
            metrics.get("yt_views"),
            metrics.get("yt_views"),
            metrics.get("yt_estimated_minutes_watched"),
            metrics.get("yt_estimated_minutes_watched"),
            metrics.get("yt_avg_view_duration"),
            metrics.get("yt_avg_view_percentage"),
            metrics.get("yt_impressions"),
            metrics.get("yt_impressions"),
            metrics.get("yt_impressions_ctr"),
            metrics.get("yt_last_sync"),
            youtube_id,
        ),
    )
    conn.commit()


def update_youtube_reach_metrics(
    conn: sqlite3.Connection,
    youtube_id: str,
    impressions: int | None,
    impressions_ctr: float | None,
    synced_at: str,
):
    conn.execute(
        """UPDATE clips
           SET yt_impressions = CASE WHEN ? IS NULL THEN yt_impressions
                                     ELSE MAX(?, COALESCE(yt_impressions, 0)) END,
               yt_impressions_ctr = COALESCE(?, yt_impressions_ctr),
               yt_last_sync = ?
           WHERE youtube_id = ?""",
        (impressions, impressions, impressions_ctr, synced_at, youtube_id),
    )
    conn.commit()


def touch_youtube_metrics_sync(conn: sqlite3.Connection, youtube_id: str, synced_at: str):
    conn.execute(
        "UPDATE clips SET yt_last_sync = ? WHERE youtube_id = ?",
        (synced_at, youtube_id),
    )
    conn.commit()


def get_streamer_performance_multiplier(conn: sqlite3.Connection, streamer: str) -> float:
    """Compute a performance multiplier from past YouTube analytics for this streamer.

    Returns a value centered on 1.0:
    - >1.0 if past uploads outperform baseline (avg CTR > 2%)
    - <1.0 if past uploads underperform baseline
    - 1.0 if no analytics data available (no effect on scoring)
    """
    row = conn.execute(
        """SELECT AVG(yt_impressions_ctr) as avg_ctr, COUNT(*) as cnt
           FROM clips
           WHERE streamer = ? AND yt_impressions_ctr IS NOT NULL""",
        (streamer,),
    ).fetchone()
    if not row or row["cnt"] < 20:
        return 1.0
    avg_ctr_raw = row["avg_ctr"]
    if not isinstance(avg_ctr_raw, (int, float)) or avg_ctr_raw <= 0:
        return 1.0
    avg_ctr = float(avg_ctr_raw)
    # Baseline CTR for Shorts is ~2%. Scale linearly: 2% -> 1.0, 4% -> 1.5, 1% -> 0.75
    # Clamped to [0.5, 2.0] to avoid extreme swings
    baseline_ctr = 0.02
    multiplier = 0.5 + 0.5 * (avg_ctr / baseline_ctr)
    return max(0.5, min(2.0, multiplier))


def get_title_variant_performance(
    conn: sqlite3.Connection,
    streamer: str | None = None,
    min_uploads: int = 5,
    min_samples: int | None = None,
) -> dict[str, float]:
    """Return per-title-variant CTR multipliers relative to this streamer's baseline."""
    threshold = min_uploads if min_samples is None else min_samples
    streamer_filter = ""
    params: list[object] = []
    if streamer:
        streamer_filter = "AND streamer = ?"
        params.append(streamer)
    baseline = conn.execute(
        """SELECT AVG(yt_impressions_ctr) as avg_ctr, COUNT(*) as cnt
           FROM clips
           WHERE 1=1
             {streamer_filter}
             AND title_variant IS NOT NULL
             AND title_variant != ''
             AND yt_impressions_ctr IS NOT NULL""".format(streamer_filter=streamer_filter),
        params,
    ).fetchone()
    if not baseline or baseline["cnt"] < threshold:
        return {}
    avg_ctr_raw = baseline["avg_ctr"]
    if not isinstance(avg_ctr_raw, (int, float)) or avg_ctr_raw <= 0:
        return {}
    avg_ctr = float(avg_ctr_raw)
    row_params = list(params)
    rows = conn.execute(
        """SELECT title_variant, AVG(yt_impressions_ctr) as variant_ctr, COUNT(*) as cnt
           FROM clips
           WHERE 1=1
             {streamer_filter}
             AND title_variant IS NOT NULL
             AND title_variant != ''
             AND yt_impressions_ctr IS NOT NULL
           GROUP BY title_variant""".format(streamer_filter=streamer_filter),
        row_params,
    ).fetchall()
    multipliers: dict[str, float] = {}
    for row in rows:
        variant = row["title_variant"]
        if not isinstance(variant, str) or row["cnt"] < threshold:
            continue
        variant_ctr = row["variant_ctr"]
        if not isinstance(variant_ctr, (int, float)) or variant_ctr <= 0:
            continue
        multipliers[variant] = max(0.5, min(2.0, float(variant_ctr) / avg_ctr))
    return multipliers


def get_game_performance(
    conn: sqlite3.Connection,
    streamer: str | None = None,
    min_uploads: int = 5,
    min_samples: int | None = None,
) -> dict[str, float]:
    """Return per-game CTR multipliers relative to this streamer's baseline."""
    threshold = min_uploads if min_samples is None else min_samples
    streamer_filter = ""
    params: list[object] = []
    if streamer:
        streamer_filter = "AND streamer = ?"
        params.append(streamer)
    baseline = conn.execute(
        """SELECT AVG(yt_impressions_ctr) as avg_ctr, COUNT(*) as cnt
           FROM clips
           WHERE 1=1
             {streamer_filter}
             AND game_name IS NOT NULL
             AND game_name != ''
             AND yt_impressions_ctr IS NOT NULL""".format(streamer_filter=streamer_filter),
        params,
    ).fetchone()
    if not baseline or baseline["cnt"] < threshold:
        return {}
    avg_ctr_raw = baseline["avg_ctr"]
    if not isinstance(avg_ctr_raw, (int, float)) or avg_ctr_raw <= 0:
        return {}
    avg_ctr = float(avg_ctr_raw)
    row_params = list(params)
    rows = conn.execute(
        """SELECT game_name, AVG(yt_impressions_ctr) as game_ctr, COUNT(*) as cnt
           FROM clips
           WHERE 1=1
             {streamer_filter}
             AND game_name IS NOT NULL
             AND game_name != ''
             AND yt_impressions_ctr IS NOT NULL
           GROUP BY game_name""".format(streamer_filter=streamer_filter),
        row_params,
    ).fetchall()
    multipliers: dict[str, float] = {}
    for row in rows:
        game_name = row["game_name"]
        if not isinstance(game_name, str) or row["cnt"] < threshold:
            continue
        game_ctr = row["game_ctr"]
        if not isinstance(game_ctr, (int, float)) or game_ctr <= 0:
            continue
        multipliers[game_name] = max(0.5, min(2.0, float(game_ctr) / avg_ctr))
    return multipliers


def update_instagram_id(conn: sqlite3.Connection, clip_id: str, instagram_id: str):
    """Set the Instagram media ID for a clip after successful upload."""
    conn.execute(
        "UPDATE clips SET instagram_id = ? WHERE clip_id = ?",
        (instagram_id, clip_id),
    )
    conn.commit()


def recent_instagram_upload_count(conn: sqlite3.Connection, streamer: str, hours: int = 24) -> int:
    """Count clips uploaded to Instagram within the last N hours."""
    cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM clips WHERE streamer = ? AND posted_at >= ? AND instagram_id IS NOT NULL",
        (streamer, cutoff),
    ).fetchone()
    return row["cnt"] if row else 0

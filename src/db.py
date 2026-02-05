import sqlite3
import os
from datetime import datetime, timedelta, timezone

from src.models import Clip


def get_connection(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS clips (
            clip_id TEXT PRIMARY KEY,
            streamer TEXT NOT NULL,
            channel_key TEXT,
            title TEXT,
            view_count INTEGER,
            created_at TEXT,
            posted_at TEXT,
            youtube_id TEXT,
            fail_count INTEGER DEFAULT 0,
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

        CREATE INDEX IF NOT EXISTS idx_clips_streamer ON clips(streamer);
        CREATE INDEX IF NOT EXISTS idx_clips_channel ON clips(channel_key);
        CREATE INDEX IF NOT EXISTS idx_clips_posted ON clips(posted_at);
    """)
    # Migration: add fail_count column if missing (existing DBs)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(clips)").fetchall()}
    if "fail_count" not in cols:
        conn.execute("ALTER TABLE clips ADD COLUMN fail_count INTEGER DEFAULT 0")
    if "channel_key" not in cols:
        conn.execute("ALTER TABLE clips ADD COLUMN channel_key TEXT")
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


def recent_upload_count(
    conn: sqlite3.Connection,
    streamer: str,
    hours: int = 4,
    channel_key: str | None = None,
) -> int:
    """Count clips uploaded for a streamer/channel within the last N hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
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


def insert_clip(conn: sqlite3.Connection, clip: Clip):
    conn.execute(
        """INSERT INTO clips (clip_id, streamer, channel_key, title, view_count, created_at, posted_at, youtube_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(clip_id) DO UPDATE SET
               youtube_id = excluded.youtube_id,
               posted_at = excluded.posted_at,
               view_count = excluded.view_count,
               title = excluded.title,
               channel_key = excluded.channel_key""",
        (clip.id, clip.streamer, clip.channel_key, clip.title, clip.view_count,
         clip.created_at, datetime.now(timezone.utc).isoformat(), clip.youtube_id),
    )
    conn.commit()


def record_known_clip(conn: sqlite3.Connection, clip: Clip):
    """Record a clip that's already on YouTube (duplicate). Does not set posted_at."""
    conn.execute(
        """INSERT INTO clips (clip_id, streamer, channel_key, title, view_count, created_at, youtube_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(clip_id) DO UPDATE SET
               youtube_id = COALESCE(clips.youtube_id, excluded.youtube_id),
               view_count = excluded.view_count,
               title = excluded.title,
               channel_key = excluded.channel_key""",
        (clip.id, clip.streamer, clip.channel_key, clip.title, clip.view_count, clip.created_at, clip.youtube_id),
    )
    conn.commit()


def get_streamer_stats(conn: sqlite3.Connection, streamer: str) -> dict | None:
    row = conn.execute("SELECT * FROM streamer_stats WHERE streamer = ?", (streamer,)).fetchone()
    return dict(row) if row else None


def update_streamer_stats(conn: sqlite3.Connection, streamer: str):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    row = conn.execute(
        "SELECT AVG(view_count) as avg_views, COUNT(*) as cnt FROM clips WHERE streamer = ? AND created_at >= ?",
        (streamer, cutoff),
    ).fetchone()
    avg_views = row["avg_views"] or 0
    count = row["cnt"] or 0
    now = datetime.now(timezone.utc).isoformat()
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
    conn.execute(
        """INSERT INTO clips (clip_id, streamer, channel_key, created_at, fail_count)
           VALUES (?, ?, ?, ?, 1)
           ON CONFLICT(clip_id) DO UPDATE SET fail_count = fail_count + 1""",
        (clip.id, clip.streamer, clip.channel_key, clip.created_at),
    )
    conn.commit()


def get_clips_for_metrics(
    conn: sqlite3.Connection,
    streamer: str,
    min_age_hours: int,
    sync_interval_hours: int,
    limit: int,
) -> list[sqlite3.Row]:
    now = datetime.now(timezone.utc)
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
           SET yt_views = ?,
               yt_estimated_minutes_watched = ?,
               yt_avg_view_duration = ?,
               yt_avg_view_percentage = ?,
               yt_impressions = ?,
               yt_impressions_ctr = ?,
               yt_last_sync = ?
           WHERE youtube_id = ?""",
        (
            metrics.get("yt_views"),
            metrics.get("yt_estimated_minutes_watched"),
            metrics.get("yt_avg_view_duration"),
            metrics.get("yt_avg_view_percentage"),
            metrics.get("yt_impressions"),
            metrics.get("yt_impressions_ctr"),
            metrics.get("yt_last_sync"),
            youtube_id,
        ),
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
    if not row or row["cnt"] < 3:
        return 1.0
    avg_ctr = row["avg_ctr"]
    if avg_ctr is None or avg_ctr <= 0:
        return 1.0
    # Baseline CTR for Shorts is ~2%. Scale linearly: 2% -> 1.0, 4% -> 1.5, 1% -> 0.75
    # Clamped to [0.5, 2.0] to avoid extreme swings
    baseline_ctr = 0.02
    multiplier = 0.5 + 0.5 * (avg_ctr / baseline_ctr)
    return max(0.5, min(2.0, multiplier))



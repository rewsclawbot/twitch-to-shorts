import sqlite3
import os
from datetime import datetime, timedelta, timezone


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
            title TEXT,
            view_count INTEGER,
            created_at TEXT,
            posted_at TEXT,
            youtube_id TEXT,
            fail_count INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS streamer_stats (
            streamer TEXT PRIMARY KEY,
            avg_views_30d REAL DEFAULT 0,
            clip_count_30d INTEGER DEFAULT 0,
            last_updated TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_clips_streamer ON clips(streamer);
        CREATE INDEX IF NOT EXISTS idx_clips_posted ON clips(posted_at);
    """)
    # Migration: add fail_count column if missing (existing DBs)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(clips)").fetchall()}
    if "fail_count" not in cols:
        conn.execute("ALTER TABLE clips ADD COLUMN fail_count INTEGER DEFAULT 0")


def clip_overlaps(conn: sqlite3.Connection, streamer: str, created_at: str, window_seconds: int = 30) -> bool:
    """Check if a clip from the same streamer exists within window_seconds of created_at."""
    row = conn.execute(
        """SELECT 1 FROM clips WHERE streamer = ?
           AND ABS(julianday(created_at) - julianday(?)) * 86400 < ?""",
        (streamer, created_at, window_seconds),
    ).fetchone()
    return row is not None


def recent_upload_count(conn: sqlite3.Connection, streamer: str, hours: int = 4) -> int:
    """Count clips uploaded for a streamer within the last N hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM clips WHERE streamer = ? AND posted_at >= ? AND youtube_id IS NOT NULL",
        (streamer, cutoff),
    ).fetchone()
    return row["cnt"] if row else 0


def insert_clip(conn: sqlite3.Connection, clip: dict):
    conn.execute(
        """INSERT INTO clips (clip_id, streamer, title, view_count, created_at, posted_at, youtube_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(clip_id) DO UPDATE SET
               youtube_id = excluded.youtube_id,
               posted_at = excluded.posted_at,
               view_count = excluded.view_count,
               title = excluded.title""",
        (clip["id"], clip["streamer"], clip["title"], clip["view_count"],
         clip["created_at"], datetime.now(timezone.utc).isoformat(), clip.get("youtube_id")),
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
    conn.execute(
        """INSERT INTO streamer_stats (streamer, avg_views_30d, clip_count_30d, last_updated)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(streamer) DO UPDATE SET avg_views_30d=?, clip_count_30d=?, last_updated=?""",
        (streamer, avg_views, count, datetime.now(timezone.utc).isoformat(),
         avg_views, count, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def increment_fail_count(conn: sqlite3.Connection, clip_id: str, streamer: str, created_at: str):
    """Record a processing failure. Upserts clip row and increments fail_count."""
    conn.execute(
        """INSERT INTO clips (clip_id, streamer, created_at, fail_count)
           VALUES (?, ?, ?, 1)
           ON CONFLICT(clip_id) DO UPDATE SET fail_count = fail_count + 1""",
        (clip_id, streamer, created_at),
    )
    conn.commit()



from __future__ import annotations

import sqlite3
from src.db import clip_overlaps
from src.models import Clip


def filter_new_clips(conn: sqlite3.Connection, clips: list[Clip]) -> list[Clip]:
    """Return only clips not already in the database, overlapping, or blacklisted."""
    if not clips:
        return []

    clip_ids = [c.id for c in clips]

    # Batch query: existing clip IDs (include permanently failed clips)
    placeholders = ",".join("?" for _ in clip_ids)
    existing = {
        row[0] for row in conn.execute(
            f"SELECT clip_id FROM clips WHERE clip_id IN ({placeholders})"
            f" AND (youtube_id IS NOT NULL OR fail_count >= 3)",
            clip_ids,
        ).fetchall()
    }

    return [
        c for c in clips
        if c.id not in existing
        and not clip_overlaps(conn, c.streamer, c.created_at)
    ]

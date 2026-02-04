import os
import sqlite3
from src.db import clip_overlaps
from src.models import Clip

BLOCKLIST_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "blocklist.txt")


def load_blocklist() -> set[str]:
    """Load clip IDs from blocklist file (one per line)."""
    if not os.path.exists(BLOCKLIST_PATH):
        return set()
    with open(BLOCKLIST_PATH) as f:
        return {line.strip() for line in f if line.strip() and not line.startswith("#")}


def filter_new_clips(conn: sqlite3.Connection, clips: list[Clip]) -> list[Clip]:
    """Return only clips not already in the database, overlapping, or blacklisted."""
    if not clips:
        return []

    clip_ids = [c.id for c in clips]
    blocklist = load_blocklist()

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
        and c.id not in blocklist
        and not clip_overlaps(conn, c.streamer, c.created_at)
    ]

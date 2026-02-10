import logging
import os
import sqlite3
from datetime import datetime

from src.db import clip_overlaps, vod_overlaps
from src.models import Clip

DEFAULT_BLOCKLIST_PATH = os.path.join("data", "blocklist.txt")
log = logging.getLogger(__name__)


def load_blocklist(blocklist_path: str = DEFAULT_BLOCKLIST_PATH) -> set[str]:
    """Load clip IDs from blocklist file (one per line)."""
    if not os.path.exists(blocklist_path):
        return set()
    with open(blocklist_path) as f:
        return {line.strip() for line in f if line.strip() and not line.startswith("#")}


def filter_new_clips(conn: sqlite3.Connection, clips: list[Clip], blocklist_path: str = DEFAULT_BLOCKLIST_PATH) -> list[Clip]:
    """Return only clips not already in the database, overlapping, or blacklisted."""
    if not clips:
        return []

    clip_ids = [c.id for c in clips]
    blocklist = load_blocklist(blocklist_path)

    # Batch query: existing clip IDs (include permanently failed clips)
    placeholders = ",".join("?" for _ in clip_ids)
    existing = {
        row[0] for row in conn.execute(
            f"SELECT clip_id FROM clips WHERE clip_id IN ({placeholders})"
            f" AND (youtube_id IS NOT NULL OR fail_count >= 3)",
            clip_ids,
        ).fetchall()
    }

    filtered = [
        c for c in clips
        if c.id not in existing
        and c.id not in blocklist
        and not clip_overlaps(conn, c.streamer, c.created_at, exclude_clip_id=c.id)
        and not vod_overlaps(conn, c.vod_id, c.vod_offset, c.duration, exclude_clip_id=c.id)
    ]
    return _filter_batch_overlaps(filtered)


def _filter_batch_overlaps(clips: list[Clip], window_seconds: int = 30) -> list[Clip]:
    """Remove overlaps within the current batch, keeping highest-ranked clips first."""
    accepted_by_streamer: dict[str, list[datetime]] = {}
    # VOD ranges accepted so far: {vod_id: [(offset, offset+duration), ...]}
    accepted_vod_ranges: dict[str, list[tuple[float, float]]] = {}
    result: list[Clip] = []
    for c in clips:
        # VOD-based overlap check (takes priority — more precise)
        if c.vod_id is not None and c.vod_offset is not None:
            ranges = accepted_vod_ranges.get(c.vod_id, [])
            c_start = c.vod_offset
            c_end = c.vod_offset + c.duration
            if any(c_start < end and start < c_end for start, end in ranges):
                continue
            accepted_vod_ranges.setdefault(c.vod_id, []).append((c_start, c_end))
            result.append(c)
            # Still record created_at so future clips without VOD data get checked
            try:
                created = datetime.fromisoformat(c.created_at)
                accepted_by_streamer.setdefault(c.streamer, []).append(created)
            except Exception:
                pass
            continue

        # Fallback: created_at timestamp overlap check
        try:
            created = datetime.fromisoformat(c.created_at)
        except Exception as e:
            log.warning("Invalid created_at for clip %s: %s", c.id, e)
            result.append(c)
            continue

        prior = accepted_by_streamer.setdefault(c.streamer, [])
        if any(abs((created - ts).total_seconds()) < window_seconds for ts in prior):
            continue
        prior.append(created)
        result.append(c)
    return result

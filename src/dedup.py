import logging
import os
import sqlite3
from datetime import UTC, datetime, timedelta

from src.db import clip_overlaps, vod_overlaps
from src.models import Clip

DEFAULT_BLOCKLIST_PATH = os.path.join("data", "blocklist.txt")
FAIL_THRESHOLD = 5
FAIL_RETRY_HOURS = 24
CLUSTER_BOOST_STEP = 0.1
CLUSTER_BOOST_CAP = 2.0
log = logging.getLogger(__name__)


def load_blocklist(blocklist_path: str = DEFAULT_BLOCKLIST_PATH) -> set[str]:
    """Load clip IDs from blocklist file (one per line)."""
    if not os.path.exists(blocklist_path):
        return set()
    with open(blocklist_path) as f:
        return {line.strip() for line in f if line.strip() and not line.startswith("#")}


def filter_new_clips(conn: sqlite3.Connection, clips: list[Clip], blocklist_path: str = DEFAULT_BLOCKLIST_PATH) -> list[Clip]:
    """Return only clips not already in the database, overlapping, or blacklisted.

    Clips that exist in the DB but haven't been uploaded (youtube_id IS NULL) are
    still eligible for upload. This allows the pipeline to persist clip metadata
    on fetch without blocking future upload attempts.
    """
    if not clips:
        return []

    clip_ids = [c.id for c in clips]
    blocklist = load_blocklist(blocklist_path)
    retry_cutoff = (datetime.now(UTC) - timedelta(hours=FAIL_RETRY_HOURS)).isoformat()

    # Batch query: existing clip IDs (include permanently failed clips)
    # Note: Clips with youtube_id=NULL (metadata-only) are NOT included in existing,
    # so they remain eligible for upload
    placeholders = ",".join("?" for _ in clip_ids)
    existing = {
        row[0] for row in conn.execute(
            f"SELECT clip_id FROM clips WHERE clip_id IN ({placeholders})"
            f" AND (youtube_id IS NOT NULL OR (fail_count >= ? AND COALESCE(last_failed_at, created_at, '') >= ?))",
            [*clip_ids, FAIL_THRESHOLD, retry_cutoff],
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
    # VOD cluster metadata keyed by vod_id.
    accepted_vod_ranges: dict[str, list[dict[str, object]]] = {}
    result: list[Clip] = []
    for c in clips:
        # VOD-based overlap check (takes priority — more precise)
        if c.vod_id is not None and c.vod_offset is not None:
            ranges = accepted_vod_ranges.get(c.vod_id, [])
            c_start = c.vod_offset
            c_end = c.vod_offset + c.duration
            overlap_entry = next(
                (
                    entry
                    for entry in ranges
                    if c_start < float(entry["end"]) and float(entry["start"]) < c_end
                ),
                None,
            )
            if overlap_entry:
                overlap_entry["cluster_size"] = int(overlap_entry["cluster_size"]) + 1
                overlap_entry["start"] = min(float(overlap_entry["start"]), c_start)
                overlap_entry["end"] = max(float(overlap_entry["end"]), c_end)
                continue
            accepted_vod_ranges.setdefault(c.vod_id, []).append(
                {
                    "start": c_start,
                    "end": c_end,
                    "clip": c,
                    "cluster_size": 1,
                }
            )
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
    _apply_vod_cluster_boosts(accepted_vod_ranges)
    return result


def _apply_vod_cluster_boosts(accepted_vod_ranges: dict[str, list[dict[str, object]]]) -> None:
    """Boost surviving clip scores based on overlap cluster size."""
    for entries in accepted_vod_ranges.values():
        for entry in entries:
            cluster_size = int(entry["cluster_size"])
            clip = entry["clip"]
            if not isinstance(clip, Clip):
                continue
            boost = min(CLUSTER_BOOST_CAP, 1.0 + CLUSTER_BOOST_STEP * (cluster_size - 1))
            clip.score *= boost

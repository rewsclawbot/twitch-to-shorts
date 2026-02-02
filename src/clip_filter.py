import logging
from datetime import datetime, timezone

from src.db import get_streamer_stats

log = logging.getLogger(__name__)


def compute_score(clip: dict, velocity_weight: float = 2.0) -> float:
    created = datetime.fromisoformat(clip["created_at"].replace("Z", "+00:00"))
    age_hours = max((datetime.now(timezone.utc) - created).total_seconds() / 3600, 0.1)
    velocity = clip["view_count"] / age_hours
    duration = max(clip.get("duration", 30), 1)
    density = clip["view_count"] / duration
    return density + velocity * velocity_weight


def filter_and_rank(
    conn,
    clips: list[dict],
    streamer: str,
    velocity_weight: float = 2.0,
    top_percentile: float = 0.10,
    bootstrap_top_n: int = 10,
    max_clips: int = 6,
) -> list[dict]:
    """Score clips and return top ones based on dynamic threshold."""
    if not clips:
        return []

    for c in clips:
        c["score"] = compute_score(c, velocity_weight)

    clips.sort(key=lambda c: c["score"], reverse=True)

    stats = get_streamer_stats(conn, streamer)

    if stats and stats["clip_count_30d"] > 0:
        # Dynamic threshold: keep clips scoring above the historical average scaled by percentile
        scores = [c["score"] for c in clips]
        score_threshold = sorted(scores, reverse=True)[max(int(len(scores) * top_percentile) - 1, 0)]
        result = [c for c in clips if c["score"] >= score_threshold]
    else:
        # Bootstrap: just take top N
        result = clips[:bootstrap_top_n]

    result = result[:max_clips]
    log.info("Filtered to %d clips for %s (from %d)", len(result), streamer, len(clips))
    return result

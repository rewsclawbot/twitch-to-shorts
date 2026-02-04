import logging
import math
from datetime import datetime, timezone

from src.db import get_streamer_performance_multiplier
from src.models import Clip

log = logging.getLogger(__name__)


def _title_quality(title: str) -> float:
    if not title:
        return 0.0
    text = title.strip()
    if not text:
        return 0.0
    score = 0.0
    if any(ch in text for ch in "!?"):
        score += 0.25
    alpha = [c for c in text if c.isalpha()]
    if alpha:
        upper_ratio = sum(c.isupper() for c in alpha) / len(alpha)
        if upper_ratio >= 0.6:
            score += 0.25
    length = len(text)
    if 10 <= length <= 80:
        score += 0.25
    if any(ch.isdigit() for ch in text):
        score += 0.25
    return min(score, 1.0)


def _transform_views(views: int, view_transform: str) -> float:
    if view_transform == "log":
        return math.log1p(max(views, 0))
    return float(views)


def compute_score(clip: Clip, velocity_weight: float = 2.0) -> float:
    return compute_score_with_options(clip, velocity_weight=velocity_weight)


def compute_score_with_options(
    clip: Clip,
    velocity_weight: float = 2.0,
    age_decay: str = "linear",
    view_transform: str = "linear",
    title_quality_weight: float = 0.0,
) -> float:
    created = datetime.fromisoformat(clip.created_at)
    age_hours = max((datetime.now(timezone.utc) - created).total_seconds() / 3600, 0.1)
    if age_decay == "log":
        age_term = max(math.log1p(age_hours), 0.1)
    else:
        age_term = age_hours
    views = _transform_views(clip.view_count, view_transform)
    velocity = views / age_term
    duration = max(clip.duration, 1)
    density = views / duration
    score = density + velocity * velocity_weight
    if title_quality_weight > 0:
        score *= 1.0 + title_quality_weight * _title_quality(clip.title)
    return score


def filter_and_rank(
    conn,
    clips: list[Clip],
    streamer: str,
    velocity_weight: float = 2.0,
    min_view_count: int = 0,
    age_decay: str = "linear",
    view_transform: str = "linear",
    title_quality_weight: float = 0.0,
    analytics_enabled: bool = False,
) -> list[Clip]:
    """Score and rank all clips that pass the quality floor. Returns all passing clips sorted by score."""
    if not clips:
        return []

    if min_view_count > 0:
        clips = [c for c in clips if c.view_count >= min_view_count]
        if not clips:
            return []

    for c in clips:
        c.score = compute_score_with_options(
            c,
            velocity_weight=velocity_weight,
            age_decay=age_decay,
            view_transform=view_transform,
            title_quality_weight=title_quality_weight,
        )

    if analytics_enabled:
        multiplier = get_streamer_performance_multiplier(conn, streamer)
        if multiplier != 1.0:
            log.info("Applying performance multiplier %.2f for %s", multiplier, streamer)
            for c in clips:
                c.score *= multiplier

    ranked = sorted(clips, key=lambda c: c.score, reverse=True)
    log.info("Ranked %d clips for %s (from %d fetched)", len(ranked), streamer, len(ranked))
    return ranked

import logging
import math
from datetime import UTC, datetime

from src.db import get_game_performance, get_streamer_performance_multiplier
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


def _duration_bonus(duration: float, optimal_min: int = 14, optimal_max: int = 31) -> float:
    """
    Return a multiplier bonus for clips in the optimal duration range.

    - Clips in [optimal_min, optimal_max]: 1.0 (baseline)
    - Clips shorter than optimal_min: linear penalty down to 0.7 at 0s
    - Clips longer than optimal_max: linear penalty down to 0.5 at 60s
    """
    if optimal_min <= duration <= optimal_max:
        return 1.0
    if duration < optimal_min:
        # Linear interpolation: 0.7 at 0s, 1.0 at optimal_min
        return 0.7 + (0.3 * (duration / max(optimal_min, 1)))

    # Linear interpolation: 1.0 at optimal_max, 0.5 at 60s
    max_overage = max(60 - optimal_max, 1)
    overage = min(max(duration - optimal_max, 0), max_overage)
    return 1.0 - (0.5 * (overage / max_overage))


def compute_score(
    clip: Clip,
    velocity_weight: float = 2.0,
    age_decay: str = "linear",
    view_transform: str = "linear",
    title_quality_weight: float = 0.0,
    duration_bonus_weight: float = 0.0,
    optimal_duration_min: int = 14,
    optimal_duration_max: int = 31,
    game_multipliers: dict[str, float] | None = None,
) -> float:
    created = datetime.fromisoformat(clip.created_at)
    age_hours = max((datetime.now(UTC) - created).total_seconds() / 3600, 0.1)
    age_term = max(math.log1p(age_hours), 0.1) if age_decay == "log" else age_hours
    views = _transform_views(clip.view_count, view_transform)
    velocity = views / age_term
    duration = max(clip.duration, 1)
    density = views / duration
    score = density + velocity * velocity_weight
    if duration_bonus_weight > 0:
        bonus = _duration_bonus(duration, optimal_duration_min, optimal_duration_max)
        score *= 1.0 + duration_bonus_weight * (bonus - 1.0)
    if title_quality_weight > 0:
        score *= 1.0 + title_quality_weight * _title_quality(clip.title)
    if game_multipliers:
        game_name = (clip.game_name or "").strip()
        if game_name:
            multiplier = game_multipliers.get(game_name)
            if isinstance(multiplier, (int, float)) and multiplier > 0:
                score *= float(multiplier)
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
    duration_bonus_weight: float = 0.0,
    optimal_duration_min: int = 14,
    optimal_duration_max: int = 31,
    analytics_enabled: bool = False,
) -> list[Clip]:
    """Score and rank all clips that pass the quality floor. Returns all passing clips sorted by score."""
    if not clips:
        return []

    if min_view_count > 0:
        clips = [c for c in clips if c.view_count >= min_view_count]
        if not clips:
            return []

    streamer_multiplier = 1.0
    game_multipliers: dict[str, float] | None = None
    if analytics_enabled:
        streamer_multiplier = get_streamer_performance_multiplier(conn, streamer)
        game_multipliers = get_game_performance(conn, streamer)
        if streamer_multiplier != 1.0:
            log.info("Applying performance multiplier %.2f for %s", streamer_multiplier, streamer)
        if game_multipliers:
            log.info("Applying %d game-specific multipliers for %s", len(game_multipliers), streamer)

    for c in clips:
        c.score = compute_score(
            c,
            velocity_weight=velocity_weight,
            age_decay=age_decay,
            view_transform=view_transform,
            title_quality_weight=title_quality_weight,
            duration_bonus_weight=duration_bonus_weight,
            optimal_duration_min=optimal_duration_min,
            optimal_duration_max=optimal_duration_max,
            game_multipliers=game_multipliers,
        )
        if streamer_multiplier != 1.0:
            c.score *= streamer_multiplier

    ranked = sorted(clips, key=lambda c: c.score, reverse=True)
    log.info("Ranked %d clips for %s (from %d fetched)", len(ranked), streamer, len(ranked))
    return ranked

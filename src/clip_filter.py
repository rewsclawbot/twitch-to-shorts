import logging
import math
from datetime import UTC, datetime

from src.audio_scorer import score_audio_excitement
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
    Return a multiplier bonus for clips based on YouTube Shorts algorithm research.

    Research (Nate Black, 35B Shorts views) shows 13s and 60s perform best.
    Bimodal preference: short snappy clips AND full-length clips both get bonuses.

    - Clips in [optimal_min, optimal_max]: 1.0 (primary sweet spot)
    - Clips near 60s (55-60s): 0.95 (secondary sweet spot — full-length performs well)
    - Clips in 30-50s dead zone: handled separately in compute_score (0.5x penalty)
    - Clips shorter than optimal_min: linear penalty down to 0.7 at 0s
    - Clips 51-54s: slight penalty (approaching sweet spot)
    """
    if optimal_min <= duration <= optimal_max:
        return 1.0
    if duration < optimal_min:
        return 0.7 + (0.3 * (duration / max(optimal_min, 1)))
    # Secondary sweet spot: full-length clips (55-60s)
    if 55 <= duration <= 60:
        return 0.95
    # Transition zone: 51-54s, ramping up to the 55s sweet spot
    if 50 < duration < 55:
        return 0.7 + (0.25 * ((duration - 50) / 5))
    # Between optimal_max and 30: gentle decline
    if optimal_max < duration <= 30:
        return 1.0 - (0.3 * ((duration - optimal_max) / max(30 - optimal_max, 1)))
    # 30-50s dead zone: return low but compute_score also applies 0.5x
    return 0.5


def compute_score(
    clip: Clip,
    velocity_weight: float = 2.0,
    age_decay: str = "linear",
    view_transform: str = "linear",
    title_quality_weight: float = 0.0,
    duration_bonus_weight: float = 0.0,
    audio_excitement_weight: float = 0.0,
    hook_strength_weight: float = 0.0,
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
    # Research-backed dead zone: 30-50s clips underperform; apply a strong penalty.
    if 30 <= duration <= 50:
        score *= 0.5
    if duration_bonus_weight > 0:
        bonus = _duration_bonus(duration, optimal_duration_min, optimal_duration_max)
        score *= 1.0 + duration_bonus_weight * (bonus - 1.0)
    if title_quality_weight > 0:
        score *= 1.0 + title_quality_weight * _title_quality(clip.title)
    if audio_excitement_weight > 0:
        # Use audio excitement score if available (set after download)
        audio_score = getattr(clip, 'audio_score', None)
        if audio_score is not None and isinstance(audio_score, (int, float)):
            # Audio score is 0-1, so we add weighted bonus
            score *= 1.0 + audio_excitement_weight * float(audio_score)
    if hook_strength_weight > 0:
        # Use hook strength score if available (set after download)
        hook_score = getattr(clip, 'hook_score', None)
        if hook_score is not None and isinstance(hook_score, (int, float)):
            # Hook score is 0-1, so we add weighted bonus
            score *= 1.0 + hook_strength_weight * float(hook_score)
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
    audio_excitement_weight: float = 0.0,
    hook_strength_weight: float = 0.0,
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
            audio_excitement_weight=audio_excitement_weight,
            hook_strength_weight=hook_strength_weight,
            optimal_duration_min=optimal_duration_min,
            optimal_duration_max=optimal_duration_max,
            game_multipliers=game_multipliers,
        )
        if streamer_multiplier != 1.0:
            c.score *= streamer_multiplier

    ranked = sorted(clips, key=lambda c: c.score, reverse=True)
    log.info("Ranked %d clips for %s (from %d fetched)", len(ranked), streamer, len(ranked))
    return ranked


def score_clip_audio(clip: Clip, video_path: str, tmp_dir: str) -> float:
    """Score a downloaded clip's audio excitement.
    
    This should be called after download to enhance ranking with audio features.
    Updates the clip's audio_score attribute and returns the score.
    
    Args:
        clip: Clip object to score
        video_path: Path to downloaded video file
        tmp_dir: Temporary directory for audio processing
        
    Returns:
        Audio excitement score (0.0-1.0)
    """
    try:
        audio_score = score_audio_excitement(video_path, tmp_dir)
        # Store as attribute for use in compute_score
        clip.audio_score = audio_score  # type: ignore[attr-defined]
        log.info("Audio excitement score for %s: %.3f", clip.id, audio_score)
        return audio_score
    except Exception as e:
        log.warning("Audio scoring failed for %s: %s", clip.id, e)
        clip.audio_score = 0.0  # type: ignore[attr-defined]
        return 0.0


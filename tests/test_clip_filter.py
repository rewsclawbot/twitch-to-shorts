from datetime import datetime, timezone, timedelta

import pytest

from src.clip_filter import compute_score, compute_score_with_options, filter_and_rank
from tests.conftest import make_clip


class TestComputeScore:
    def test_known_inputs_returns_expected_value(self):
        """Score = density + velocity * weight; for 1000 views, 30s, 1h age: ~2033."""
        clip = make_clip(
            view_count=1000,
            duration=30,
            created_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        )
        score = compute_score(clip, velocity_weight=2.0)
        density = 1000 / 30
        velocity = 1000 / 1.0
        expected = density + velocity * 2.0
        assert abs(score - expected) < 5  # small tolerance for sub-second timing

    def test_zero_duration_clamped_to_one(self):
        """A clip with duration=0 should not cause ZeroDivisionError; duration clamps to 1."""
        clip = make_clip(view_count=500, duration=0)
        score = compute_score(clip)
        assert score > 0
        assert score >= 500  # density = 500/1

    def test_future_created_at_clamps_age_to_minimum(self):
        """A clip created in the future should have age clamped to 0.1h."""
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        clip = make_clip(view_count=100, duration=10, created_at=future)
        score = compute_score(clip)
        # velocity = 100 / 0.1 = 1000; density = 100/10 = 10
        expected = 10 + 1000 * 2.0
        assert abs(score - expected) < 1

    def test_higher_view_count_produces_higher_score(self):
        """All else equal, more views should yield a higher score."""
        base = make_clip(view_count=100)
        popular = make_clip(view_count=10000)
        assert compute_score(popular) > compute_score(base)

    def test_velocity_weight_scales_velocity_component(self):
        """Doubling velocity_weight should increase the score."""
        clip = make_clip(view_count=500)
        score_low = compute_score(clip, velocity_weight=1.0)
        score_high = compute_score(clip, velocity_weight=4.0)
        assert score_high > score_low

    def test_log_age_decay_increases_score_for_older_clips(self):
        clip = make_clip(
            view_count=1000,
            created_at=(datetime.now(timezone.utc) - timedelta(hours=6)).isoformat(),
        )
        linear = compute_score_with_options(clip, age_decay="linear")
        log_decay = compute_score_with_options(clip, age_decay="log")
        assert log_decay > linear

    def test_log_view_transform_reduces_score(self):
        clip = make_clip(view_count=1000)
        linear = compute_score_with_options(clip, view_transform="linear")
        log_view = compute_score_with_options(clip, view_transform="log")
        assert linear > log_view

    def test_title_quality_bonus_increases_score(self):
        clip = make_clip(title="OMG!!! 1v5 CLUTCH")
        base = compute_score_with_options(clip, title_quality_weight=0.0)
        boosted = compute_score_with_options(clip, title_quality_weight=0.1)
        assert boosted > base


class TestFilterAndRank:
    def test_empty_input_returns_empty(self, conn):
        assert filter_and_rank(conn, [], "streamer") == []

    def test_bootstrap_mode_returns_top_n(self, conn):
        """With no streamer stats, should return up to bootstrap_top_n clips."""
        clips = [make_clip(clip_id=f"c{i}", view_count=100 * (i + 1)) for i in range(15)]
        result = filter_and_rank(conn, clips, "newstreamer", bootstrap_top_n=5, max_clips=10)
        assert len(result) == 5
        assert result[0].score >= result[-1].score

    def test_steady_state_uses_percentile_threshold(self, conn):
        """When streamer stats exist, clips are filtered by percentile threshold."""
        conn.execute(
            "INSERT INTO streamer_stats (streamer, avg_views_30d, clip_count_30d, last_updated) "
            "VALUES (?, ?, ?, ?)",
            ("streamer_a", 500.0, 20, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        clips = [make_clip(clip_id=f"c{i}", view_count=100 * (i + 1)) for i in range(20)]
        result = filter_and_rank(
            conn, clips, "streamer_a", top_percentile=0.10, max_clips=10
        )
        # top 10% of 20 = 2 clips
        assert len(result) == 2

    def test_max_clips_limit_respected(self, conn):
        """Output should never exceed max_clips regardless of input size."""
        clips = [make_clip(clip_id=f"c{i}", view_count=1000) for i in range(50)]
        result = filter_and_rank(conn, clips, "s", bootstrap_top_n=50, max_clips=3)
        assert len(result) == 3

    def test_clips_have_score_attribute_after_ranking(self, conn):
        """Every returned clip should have a nonzero score populated."""
        clips = [make_clip()]
        result = filter_and_rank(conn, clips, "s")
        assert result[0].score > 0
        assert isinstance(result[0].score, float)

    def test_min_view_count_filters_clips(self, conn):
        clips = [
            make_clip(clip_id="low_1", view_count=100),
            make_clip(clip_id="low_2", view_count=200),
            make_clip(clip_id="high_1", view_count=1000),
        ]
        result = filter_and_rank(conn, clips, "s", min_view_count=500, bootstrap_top_n=10)
        assert len(result) == 1
        assert result[0].id == "high_1"

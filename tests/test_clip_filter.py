from datetime import UTC, datetime, timedelta

from src.clip_filter import compute_score, filter_and_rank
from tests.conftest import make_clip


class TestComputeScore:
    def test_known_inputs_returns_expected_value(self):
        """Score = density + velocity * weight; for 1000 views, 30s, 1h age: ~2033."""
        clip = make_clip(
            view_count=1000,
            duration=30,
            created_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
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
        future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
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
            created_at=(datetime.now(UTC) - timedelta(hours=6)).isoformat(),
        )
        linear = compute_score(clip, age_decay="linear")
        log_decay = compute_score(clip, age_decay="log")
        assert log_decay > linear

    def test_log_view_transform_reduces_score(self):
        clip = make_clip(view_count=1000)
        linear = compute_score(clip, view_transform="linear")
        log_view = compute_score(clip, view_transform="log")
        assert linear > log_view

    def test_title_quality_bonus_increases_score(self):
        clip = make_clip(title="OMG!!! 1v5 CLUTCH")
        base = compute_score(clip, title_quality_weight=0.0)
        boosted = compute_score(clip, title_quality_weight=0.1)
        assert boosted > base

    def test_duration_bonus_favors_optimal_range(self):
        now = datetime.now(UTC)
        optimal = make_clip(
            clip_id="optimal",
            view_count=1000,
            duration=20,
            created_at=(now - timedelta(hours=1)).isoformat(),
        )
        long_clip = make_clip(
            clip_id="long",
            view_count=1000,
            duration=45,
            created_at=(now - timedelta(hours=1)).isoformat(),
        )
        optimal_score = compute_score(optimal, duration_bonus_weight=0.3)
        long_score = compute_score(long_clip, duration_bonus_weight=0.3)
        assert optimal_score > long_score

    def test_duration_bonus_can_flip_short_clip_advantage(self):
        now = datetime.now(UTC)
        very_short = make_clip(
            clip_id="short",
            view_count=1000,
            duration=10,
            created_at=(now - timedelta(hours=1)).isoformat(),
        )
        optimal = make_clip(
            clip_id="optimal",
            view_count=1000,
            duration=14,
            created_at=(now - timedelta(hours=1)).isoformat(),
        )

        short_without_bonus = compute_score(very_short, duration_bonus_weight=0.0)
        optimal_without_bonus = compute_score(optimal, duration_bonus_weight=0.0)
        assert short_without_bonus > optimal_without_bonus

        short_with_bonus = compute_score(very_short, duration_bonus_weight=1.0)
        optimal_with_bonus = compute_score(optimal, duration_bonus_weight=1.0)
        assert optimal_with_bonus > short_with_bonus


class TestFilterAndRank:
    def test_empty_input_returns_empty(self, conn):
        assert filter_and_rank(conn, [], "streamer") == []

    def test_returns_all_clips_sorted_by_score(self, conn):
        """Should return all clips sorted by score descending (no truncation)."""
        clips = [make_clip(clip_id=f"c{i}", view_count=100 * (i + 1)) for i in range(15)]
        result = filter_and_rank(conn, clips, "newstreamer")
        assert len(result) == 15
        for i in range(len(result) - 1):
            assert result[i].score >= result[i + 1].score

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
        result = filter_and_rank(conn, clips, "s", min_view_count=500)
        assert len(result) == 1
        assert result[0].id == "high_1"

    def test_duration_bonus_weight_changes_ranking(self, conn):
        now = datetime.now(UTC)
        clips = [
            make_clip(
                clip_id="short",
                view_count=1000,
                duration=10,
                created_at=(now - timedelta(hours=1)).isoformat(),
            ),
            make_clip(
                clip_id="optimal",
                view_count=1000,
                duration=14,
                created_at=(now - timedelta(hours=1)).isoformat(),
            ),
        ]
        ranked = filter_and_rank(conn, clips, "s", duration_bonus_weight=1.0)
        assert ranked[0].id == "optimal"

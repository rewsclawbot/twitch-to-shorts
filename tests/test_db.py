import json
from datetime import UTC, datetime, timedelta

import pytest

from src.db import (
    clip_overlaps,
    finish_pipeline_run,
    get_clips_for_metrics,
    get_game_performance,
    get_streamer_performance_multiplier,
    get_title_variant_performance,
    get_todays_runs,
    increment_fail_count,
    insert_clip,
    insert_pipeline_run,
    recent_instagram_upload_count,
    recent_upload_count,
    record_known_clip,
    touch_youtube_metrics_sync,
    update_last_failed_at,
    update_instagram_id,
    update_streamer_stats,
    update_youtube_metrics,
    update_youtube_reach_metrics,
    upsert_clip_metadata,
    vod_overlaps,
)
from tests.conftest import make_clip


class TestSchemaMigrations:
    def test_clips_table_contains_new_feedback_columns(self, conn):
        cols = {row[1] for row in conn.execute("PRAGMA table_info(clips)").fetchall()}
        assert {"title_variant", "game_name", "last_failed_at"} <= cols


class TestInsertClip:
    def test_upsert_inserts_new_clip(self, conn):
        clip = make_clip(clip_id="c1", title="Original", youtube_id="yt_1")
        insert_clip(conn, clip)

        row = conn.execute("SELECT * FROM clips WHERE clip_id = 'c1'").fetchone()
        assert row["title"] == "Original"
        assert row["youtube_id"] == "yt_1"

    def test_upsert_updates_on_conflict(self, conn):
        """Re-inserting the same clip_id updates view_count/title and youtube_id."""
        clip1 = make_clip(clip_id="c1", title="V1", view_count=100, youtube_id="yt_original")
        insert_clip(conn, clip1)

        clip2 = make_clip(clip_id="c1", title="V2", view_count=999, youtube_id="yt_updated")
        insert_clip(conn, clip2)

        row = conn.execute("SELECT * FROM clips WHERE clip_id = 'c1'").fetchone()
        assert row["title"] == "V2"
        assert row["view_count"] == 999
        assert row["youtube_id"] == "yt_updated"

    def test_stores_game_name_and_title_variant(self, conn):
        clip = make_clip(
            clip_id="c_meta",
            game_name="Valorant",
            title_variant="template_2+optimized",
            youtube_id="yt_meta",
        )
        insert_clip(conn, clip)
        row = conn.execute("SELECT game_name, title_variant FROM clips WHERE clip_id = 'c_meta'").fetchone()
        assert row["game_name"] == "Valorant"
        assert row["title_variant"] == "template_2+optimized"

    def test_upsert_preserves_existing_variant_when_new_value_empty(self, conn):
        clip1 = make_clip(
            clip_id="c_variant",
            game_name="Fortnite",
            title_variant="template_1",
            youtube_id="yt_1",
        )
        insert_clip(conn, clip1)
        clip2 = make_clip(
            clip_id="c_variant",
            game_name="",
            title_variant="",
            youtube_id="yt_2",
        )
        insert_clip(conn, clip2)
        row = conn.execute("SELECT game_name, title_variant FROM clips WHERE clip_id = 'c_variant'").fetchone()
        assert row["game_name"] == "Fortnite"
        assert row["title_variant"] == "template_1"


class TestRecordKnownClip:
    def test_record_known_clip_does_not_set_posted_at(self, conn):
        """record_known_clip should leave posted_at as NULL."""
        clip = make_clip(clip_id="dup1", youtube_id="yt_ext")
        record_known_clip(conn, clip)

        row = conn.execute("SELECT posted_at, youtube_id FROM clips WHERE clip_id = 'dup1'").fetchone()
        assert row["youtube_id"] == "yt_ext"
        assert row["posted_at"] is None

    def test_record_known_clip_does_not_overwrite_posted_at(self, conn):
        """If a clip already has a posted_at (real upload), record_known_clip should preserve it."""
        clip = make_clip(clip_id="dup2", youtube_id="yt_original")
        insert_clip(conn, clip)

        original_posted = conn.execute("SELECT posted_at FROM clips WHERE clip_id = 'dup2'").fetchone()["posted_at"]
        assert original_posted is not None

        # record_known_clip with a different youtube_id should NOT overwrite existing
        clip.youtube_id = "yt_different"
        record_known_clip(conn, clip)

        row = conn.execute("SELECT posted_at, youtube_id FROM clips WHERE clip_id = 'dup2'").fetchone()
        assert row["posted_at"] == original_posted  # preserved
        assert row["youtube_id"] == "yt_original"  # preserved (COALESCE keeps existing)

    def test_record_known_clip_sets_youtube_id_when_null(self, conn):
        """If a clip has no youtube_id, record_known_clip should set it."""
        clip = make_clip(clip_id="dup3", youtube_id=None)
        # Insert with no youtube_id via increment_fail_count to create a row without youtube_id
        from src.db import increment_fail_count
        increment_fail_count(conn, clip)

        row = conn.execute("SELECT youtube_id FROM clips WHERE clip_id = 'dup3'").fetchone()
        assert row["youtube_id"] is None

        # Now record_known_clip should fill in the youtube_id
        clip.youtube_id = "yt_new"
        record_known_clip(conn, clip)

        row = conn.execute("SELECT youtube_id FROM clips WHERE clip_id = 'dup3'").fetchone()
        assert row["youtube_id"] == "yt_new"

    def test_record_known_clip_stores_game_name_and_title_variant(self, conn):
        clip = make_clip(
            clip_id="dup_meta",
            youtube_id="yt_dup",
            game_name="Apex Legends",
            title_variant="template_0",
        )
        record_known_clip(conn, clip)
        row = conn.execute("SELECT game_name, title_variant FROM clips WHERE clip_id = 'dup_meta'").fetchone()
        assert row["game_name"] == "Apex Legends"
        assert row["title_variant"] == "template_0"


class TestIncrementFailCount:
    def test_creates_row_if_not_exists(self, conn):
        clip = make_clip(clip_id="fail_1", streamer="streamer_x",
                         created_at="2025-01-01T00:00:00+00:00")
        increment_fail_count(conn, clip)
        row = conn.execute("SELECT fail_count FROM clips WHERE clip_id = 'fail_1'").fetchone()
        assert row["fail_count"] == 1

    def test_increments_existing_row(self, conn):
        clip = make_clip(clip_id="fail_2", streamer="streamer_x",
                         created_at="2025-01-01T00:00:00+00:00")
        increment_fail_count(conn, clip)
        increment_fail_count(conn, clip)
        increment_fail_count(conn, clip)
        row = conn.execute("SELECT fail_count FROM clips WHERE clip_id = 'fail_2'").fetchone()
        assert row["fail_count"] == 3

    def test_does_not_clobber_existing_youtube_id(self, conn):
        """If a clip already has a youtube_id, incrementing fail_count should not erase it."""
        clip = make_clip(clip_id="c_yt", youtube_id="yt_abc")
        insert_clip(conn, clip)

        increment_fail_count(conn, clip)
        row = conn.execute("SELECT youtube_id, fail_count FROM clips WHERE clip_id = 'c_yt'").fetchone()
        assert row["youtube_id"] == "yt_abc"
        assert row["fail_count"] == 1

    def test_sets_last_failed_at(self, conn):
        clip = make_clip(clip_id="fail_ts")
        increment_fail_count(conn, clip)
        row = conn.execute("SELECT last_failed_at FROM clips WHERE clip_id = 'fail_ts'").fetchone()
        assert row["last_failed_at"] is not None

    def test_updates_last_failed_at_on_subsequent_failures(self, conn):
        clip = make_clip(clip_id="fail_ts_update")
        increment_fail_count(conn, clip)
        old_ts = "2020-01-01T00:00:00+00:00"
        conn.execute("UPDATE clips SET last_failed_at = ? WHERE clip_id = ?", (old_ts, clip.id))
        conn.commit()
        increment_fail_count(conn, clip)
        row = conn.execute("SELECT fail_count, last_failed_at FROM clips WHERE clip_id = ?", (clip.id,)).fetchone()
        assert row["fail_count"] == 2
        assert row["last_failed_at"] != old_ts


class TestUpdateLastFailedAt:
    def test_updates_timestamp_for_existing_clip(self, conn):
        clip = make_clip(clip_id="fail_touch")
        increment_fail_count(conn, clip)
        custom = "2026-02-14T10:00:00+00:00"
        update_last_failed_at(conn, clip.id, custom)
        row = conn.execute("SELECT last_failed_at FROM clips WHERE clip_id = ?", (clip.id,)).fetchone()
        assert row["last_failed_at"] == custom

    def test_missing_clip_is_noop(self, conn):
        update_last_failed_at(conn, "missing_clip", "2026-02-14T10:00:00+00:00")
        row = conn.execute("SELECT * FROM clips WHERE clip_id = 'missing_clip'").fetchone()
        assert row is None


class TestRecentUploadCount:
    def test_counts_recent_uploads(self, conn):
        clip = make_clip(clip_id="recent_1", youtube_id="yt_1")
        insert_clip(conn, clip)

        count = recent_upload_count(conn, "teststreamer", hours=4)
        assert count == 1

    def test_excludes_old_uploads(self, conn):
        """Clips posted more than N hours ago should not be counted."""
        old_time = (datetime.now(UTC) - timedelta(hours=10)).isoformat()
        conn.execute(
            "INSERT INTO clips (clip_id, streamer, posted_at, youtube_id) VALUES (?, ?, ?, ?)",
            ("old_1", "teststreamer", old_time, "yt_old"),
        )
        conn.commit()
        count = recent_upload_count(conn, "teststreamer", hours=4)
        assert count == 0

    def test_excludes_clips_without_youtube_id(self, conn):
        """Clips that were never uploaded (youtube_id IS NULL) should not count."""
        clip = make_clip(clip_id="no_yt", youtube_id=None)
        insert_clip(conn, clip)
        count = recent_upload_count(conn, "teststreamer", hours=4)
        assert count == 0


class TestPipelineRuns:
    def test_insert_and_finish_roundtrip(self, conn):
        started_at = datetime.now(UTC).isoformat()
        run_id = insert_pipeline_run(conn, started_at, trigger="cron")
        details = [{"streamer": "a", "uploaded": 1, "failed": 0, "skip_reason": None}]
        totals = {
            "fetched": 10,
            "filtered": 3,
            "downloaded": 2,
            "processed": 2,
            "uploaded": 1,
            "failed": 0,
        }
        finished_at = datetime.now(UTC).isoformat()
        finish_pipeline_run(conn, run_id, finished_at, totals, details)

        row = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
        assert row is not None
        assert row["started_at"] == started_at
        assert row["finished_at"] == finished_at
        assert row["trigger"] == "cron"
        assert row["total_fetched"] == 10
        assert row["total_filtered"] == 3
        assert row["total_downloaded"] == 2
        assert row["total_processed"] == 2
        assert row["total_uploaded"] == 1
        assert row["total_failed"] == 0
        assert json.loads(row["streamer_details"]) == details

    def test_get_todays_runs_filters_by_date(self, conn):
        today_start = datetime.now(UTC).replace(hour=1, minute=0, second=0, microsecond=0).isoformat()
        yesterday_start = (datetime.now(UTC) - timedelta(days=1)).replace(
            hour=23, minute=0, second=0, microsecond=0
        ).isoformat()
        insert_pipeline_run(conn, today_start, trigger="cron")
        insert_pipeline_run(conn, yesterday_start, trigger="cron")

        rows = get_todays_runs(conn)
        started = {row["started_at"] for row in rows}
        assert today_start in started
        assert yesterday_start not in started

    def test_streamer_details_json_roundtrip(self, conn):
        run_id = insert_pipeline_run(conn, datetime.now(UTC).isoformat())
        details = [
            {"streamer": "alpha", "uploaded": 0, "failed": 0, "skip_reason": "spacing_limited"},
            {"streamer": "beta", "uploaded": 1, "failed": 0, "skip_reason": None},
        ]
        finish_pipeline_run(
            conn,
            run_id,
            datetime.now(UTC).isoformat(),
            {
                "fetched": 5,
                "filtered": 2,
                "downloaded": 1,
                "processed": 1,
                "uploaded": 1,
                "failed": 0,
            },
            details,
        )
        row = conn.execute("SELECT streamer_details FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
        assert row is not None
        assert json.loads(row["streamer_details"]) == details


class TestUpdateStreamerStats:
    def test_computes_rolling_30d_averages(self, conn):
        now = datetime.now(UTC)
        for i in range(5):
            conn.execute(
                "INSERT INTO clips (clip_id, streamer, view_count, created_at) VALUES (?, ?, ?, ?)",
                (f"s{i}", "streamer_a", (i + 1) * 100, (now - timedelta(days=i)).isoformat()),
            )
        conn.commit()

        update_streamer_stats(conn, "streamer_a")
        row = conn.execute("SELECT * FROM streamer_stats WHERE streamer = 'streamer_a'").fetchone()
        assert row["clip_count_30d"] == 5
        assert row["avg_views_30d"] == pytest.approx(300.0)


class TestClipOverlaps:
    def test_detects_clips_within_30s_window(self, conn):
        base = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        conn.execute(
            "INSERT INTO clips (clip_id, streamer, created_at) VALUES (?, ?, ?)",
            ("x1", "s", base.isoformat()),
        )
        conn.commit()
        assert clip_overlaps(conn, "s", (base + timedelta(seconds=20)).isoformat()) is True

    def test_allows_clips_outside_30s_window(self, conn):
        base = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        conn.execute(
            "INSERT INTO clips (clip_id, streamer, created_at) VALUES (?, ?, ?)",
            ("x1", "s", base.isoformat()),
        )
        conn.commit()
        assert clip_overlaps(conn, "s", (base + timedelta(seconds=60)).isoformat()) is False

    def test_overlap_is_streamer_scoped(self, conn):
        base = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        conn.execute(
            "INSERT INTO clips (clip_id, streamer, created_at) VALUES (?, ?, ?)",
            ("x1", "streamer_a", base.isoformat()),
        )
        conn.commit()
        assert clip_overlaps(conn, "streamer_b", (base + timedelta(seconds=5)).isoformat()) is False


    def test_exclude_clip_id_ignores_self_match(self, conn):
        """A clip should not overlap with its own DB row."""
        base = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        conn.execute(
            "INSERT INTO clips (clip_id, streamer, created_at) VALUES (?, ?, ?)",
            ("self1", "s", base.isoformat()),
        )
        conn.commit()
        # Without exclude_clip_id, the clip matches itself
        assert clip_overlaps(conn, "s", base.isoformat()) is True
        # With exclude_clip_id, the self-match is ignored
        assert clip_overlaps(conn, "s", base.isoformat(), exclude_clip_id="self1") is False


class TestYouTubeMetrics:
    def test_get_clips_for_metrics_filters_by_age_and_sync(self, conn):
        now = datetime.now(UTC)
        recent = (now - timedelta(hours=2)).isoformat()
        old = (now - timedelta(hours=72)).isoformat()
        conn.execute(
            "INSERT INTO clips (clip_id, streamer, posted_at, youtube_id) VALUES (?, ?, ?, ?)",
            ("recent", "s", recent, "yt_recent"),
        )
        conn.execute(
            "INSERT INTO clips (clip_id, streamer, posted_at, youtube_id, yt_last_sync) VALUES (?, ?, ?, ?, ?)",
            ("old_synced", "s", old, "yt_old_synced", (now - timedelta(hours=1)).isoformat()),
        )
        conn.execute(
            "INSERT INTO clips (clip_id, streamer, posted_at, youtube_id) VALUES (?, ?, ?, ?)",
            ("old_unsynced", "s", old, "yt_old_unsynced"),
        )
        conn.commit()

        rows = get_clips_for_metrics(conn, "s", min_age_hours=48, sync_interval_hours=24, limit=10)
        ids = {row["clip_id"] for row in rows}
        assert "old_unsynced" in ids
        assert "recent" not in ids
        assert "old_synced" not in ids

    def test_update_and_touch_metrics(self, conn):
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "INSERT INTO clips (clip_id, streamer, posted_at, youtube_id) VALUES (?, ?, ?, ?)",
            ("c1", "s", now, "yt1"),
        )
        conn.commit()

        update_youtube_metrics(conn, "yt1", {
            "yt_views": 123,
            "yt_estimated_minutes_watched": 45.6,
            "yt_avg_view_duration": 30.0,
            "yt_avg_view_percentage": 75.0,
            "yt_impressions": 1000,
            "yt_impressions_ctr": 2.5,
            "yt_last_sync": now,
        })
        row = conn.execute("SELECT yt_views, yt_impressions, yt_last_sync FROM clips WHERE youtube_id = 'yt1'").fetchone()
        assert row["yt_views"] == 123
        assert row["yt_impressions"] == 1000
        assert row["yt_last_sync"] == now

        later = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        touch_youtube_metrics_sync(conn, "yt1", later)
        row = conn.execute("SELECT yt_last_sync FROM clips WHERE youtube_id = 'yt1'").fetchone()
        assert row["yt_last_sync"] == later

    def test_views_regression_prevented(self, conn):
        """Updating with a lower views count should keep the higher existing value."""
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "INSERT INTO clips (clip_id, streamer, posted_at, youtube_id) VALUES (?, ?, ?, ?)",
            ("c_reg", "s", now, "yt_reg"),
        )
        conn.commit()

        update_youtube_metrics(conn, "yt_reg", {
            "yt_views": 500,
            "yt_estimated_minutes_watched": 100.0,
            "yt_avg_view_duration": 30.0,
            "yt_avg_view_percentage": 75.0,
            "yt_impressions": 2000,
            "yt_impressions_ctr": 5.0,
            "yt_last_sync": now,
        })

        # Now update with LOWER cumulative metrics — should NOT regress
        update_youtube_metrics(conn, "yt_reg", {
            "yt_views": 200,
            "yt_estimated_minutes_watched": 50.0,
            "yt_avg_view_duration": 25.0,
            "yt_avg_view_percentage": 60.0,
            "yt_impressions": 800,
            "yt_impressions_ctr": 3.0,
            "yt_last_sync": now,
        })

        row = conn.execute(
            "SELECT yt_views, yt_estimated_minutes_watched, yt_impressions, yt_avg_view_duration, yt_avg_view_percentage FROM clips WHERE youtube_id = 'yt_reg'"
        ).fetchone()
        assert row["yt_views"] == 500  # kept higher
        assert row["yt_estimated_minutes_watched"] == pytest.approx(100.0)  # kept higher
        assert row["yt_impressions"] == 2000  # kept higher
        # Rate metrics use COALESCE — latest non-null wins
        assert row["yt_avg_view_duration"] == pytest.approx(25.0)
        assert row["yt_avg_view_percentage"] == pytest.approx(60.0)

    def test_views_increase_allowed(self, conn):
        """Updating with a higher views count should take the new value."""
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "INSERT INTO clips (clip_id, streamer, posted_at, youtube_id) VALUES (?, ?, ?, ?)",
            ("c_inc", "s", now, "yt_inc"),
        )
        conn.commit()

        update_youtube_metrics(conn, "yt_inc", {
            "yt_views": 100,
            "yt_estimated_minutes_watched": 20.0,
            "yt_impressions": 500,
            "yt_last_sync": now,
        })
        update_youtube_metrics(conn, "yt_inc", {
            "yt_views": 300,
            "yt_estimated_minutes_watched": 60.0,
            "yt_impressions": 1500,
            "yt_last_sync": now,
        })

        row = conn.execute(
            "SELECT yt_views, yt_estimated_minutes_watched, yt_impressions FROM clips WHERE youtube_id = 'yt_inc'"
        ).fetchone()
        assert row["yt_views"] == 300
        assert row["yt_estimated_minutes_watched"] == pytest.approx(60.0)
        assert row["yt_impressions"] == 1500

    def test_null_preserves_existing_metrics(self, conn):
        """Updating with None values should keep existing values (not regress to NULL)."""
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "INSERT INTO clips (clip_id, streamer, posted_at, youtube_id) VALUES (?, ?, ?, ?)",
            ("c_null", "s", now, "yt_null"),
        )
        conn.commit()

        update_youtube_metrics(conn, "yt_null", {
            "yt_views": 400,
            "yt_estimated_minutes_watched": 80.0,
            "yt_avg_view_duration": 30.0,
            "yt_avg_view_percentage": 70.0,
            "yt_impressions": 1000,
            "yt_impressions_ctr": 4.0,
            "yt_last_sync": now,
        })

        # Update with all None — nothing should change except yt_last_sync
        later = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        update_youtube_metrics(conn, "yt_null", {
            "yt_last_sync": later,
        })

        row = conn.execute(
            "SELECT yt_views, yt_estimated_minutes_watched, yt_avg_view_duration, yt_avg_view_percentage, yt_impressions, yt_impressions_ctr FROM clips WHERE youtube_id = 'yt_null'"
        ).fetchone()
        assert row["yt_views"] == 400
        assert row["yt_estimated_minutes_watched"] == pytest.approx(80.0)
        assert row["yt_avg_view_duration"] == pytest.approx(30.0)
        assert row["yt_avg_view_percentage"] == pytest.approx(70.0)
        assert row["yt_impressions"] == 1000
        assert row["yt_impressions_ctr"] == pytest.approx(4.0)


class TestPerformanceMultiplier:
    def test_returns_one_with_no_data(self, conn):
        assert get_streamer_performance_multiplier(conn, "nobody") == 1.0

    def test_returns_one_with_fewer_than_twenty_data_points(self, conn):
        for i in range(19):
            conn.execute(
                "INSERT INTO clips (clip_id, streamer, youtube_id, yt_impressions_ctr) VALUES (?, ?, ?, ?)",
                (f"pm_{i}", "s", f"yt_{i}", 0.04),
            )
        conn.commit()
        assert get_streamer_performance_multiplier(conn, "s") == 1.0

    def test_high_ctr_boosts_multiplier(self, conn):
        for i in range(20):
            conn.execute(
                "INSERT INTO clips (clip_id, streamer, youtube_id, yt_impressions_ctr) VALUES (?, ?, ?, ?)",
                (f"hi_{i}", "good", f"yt_hi_{i}", 0.04),
            )
        conn.commit()
        mult = get_streamer_performance_multiplier(conn, "good")
        assert mult > 1.0

    def test_low_ctr_reduces_multiplier(self, conn):
        for i in range(20):
            conn.execute(
                "INSERT INTO clips (clip_id, streamer, youtube_id, yt_impressions_ctr) VALUES (?, ?, ?, ?)",
                (f"lo_{i}", "poor", f"yt_lo_{i}", 0.005),
            )
        conn.commit()
        mult = get_streamer_performance_multiplier(conn, "poor")
        assert mult < 1.0

    def test_multiplier_clamped(self, conn):
        for i in range(20):
            conn.execute(
                "INSERT INTO clips (clip_id, streamer, youtube_id, yt_impressions_ctr) VALUES (?, ?, ?, ?)",
                (f"ex_{i}", "extreme", f"yt_ex_{i}", 0.20),
            )
        conn.commit()
        mult = get_streamer_performance_multiplier(conn, "extreme")
        assert mult == 2.0


class TestVariantAndGamePerformance:
    def test_title_variant_performance_returns_empty_with_insufficient_data(self, conn):
        for i in range(4):
            conn.execute(
                "INSERT INTO clips (clip_id, streamer, title_variant, yt_impressions_ctr) VALUES (?, ?, ?, ?)",
                (f"tv_small_{i}", "s", "template_0", 0.04),
            )
        conn.commit()
        assert get_title_variant_performance(conn, "s", min_samples=5) == {}

    def test_title_variant_performance_computes_multipliers(self, conn):
        for i in range(5):
            conn.execute(
                "INSERT INTO clips (clip_id, streamer, title_variant, yt_impressions_ctr) VALUES (?, ?, ?, ?)",
                (f"tv_hi_{i}", "s", "template_0", 0.08),
            )
        for i in range(5):
            conn.execute(
                "INSERT INTO clips (clip_id, streamer, title_variant, yt_impressions_ctr) VALUES (?, ?, ?, ?)",
                (f"tv_lo_{i}", "s", "template_1", 0.02),
            )
        conn.commit()
        perf = get_title_variant_performance(conn, "s", min_samples=5)
        assert perf["template_0"] == pytest.approx(1.6)
        assert perf["template_1"] == pytest.approx(0.5)  # clamped

    def test_game_performance_computes_multipliers(self, conn):
        for i in range(5):
            conn.execute(
                "INSERT INTO clips (clip_id, streamer, game_name, yt_impressions_ctr) VALUES (?, ?, ?, ?)",
                (f"gp_hi_{i}", "s", "Apex Legends", 0.09),
            )
        for i in range(5):
            conn.execute(
                "INSERT INTO clips (clip_id, streamer, game_name, yt_impressions_ctr) VALUES (?, ?, ?, ?)",
                (f"gp_lo_{i}", "s", "Other Game", 0.01),
            )
        conn.commit()
        perf = get_game_performance(conn, "s", min_samples=5)
        assert perf["Apex Legends"] > 1.0
        assert perf["Other Game"] == pytest.approx(0.5)  # clamped


class TestUpdateYoutubeReachMetrics:
    """Tests for update_youtube_reach_metrics — COALESCE-based reach metric upsert."""

    def _insert_clip_with_yt(self, conn, youtube_id, impressions=None, ctr=None):
        """Helper: insert a clip row, then optionally set initial impression values."""
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "INSERT INTO clips (clip_id, streamer, posted_at, youtube_id) VALUES (?, ?, ?, ?)",
            (f"clip_{youtube_id}", "s", now, youtube_id),
        )
        if impressions is not None or ctr is not None:
            conn.execute(
                "UPDATE clips SET yt_impressions = ?, yt_impressions_ctr = ? WHERE youtube_id = ?",
                (impressions, ctr, youtube_id),
            )
        conn.commit()

    def test_fills_null_impressions(self, conn):
        self._insert_clip_with_yt(conn, "yt_r1")
        now = datetime.now(UTC).isoformat()
        update_youtube_reach_metrics(conn, "yt_r1", impressions=100, impressions_ctr=None, synced_at=now)
        row = conn.execute("SELECT yt_impressions FROM clips WHERE youtube_id = 'yt_r1'").fetchone()
        assert row["yt_impressions"] == 100

    def test_fills_null_ctr(self, conn):
        self._insert_clip_with_yt(conn, "yt_r2")
        now = datetime.now(UTC).isoformat()
        update_youtube_reach_metrics(conn, "yt_r2", impressions=None, impressions_ctr=0.05, synced_at=now)
        row = conn.execute("SELECT yt_impressions_ctr FROM clips WHERE youtube_id = 'yt_r2'").fetchone()
        assert row["yt_impressions_ctr"] == pytest.approx(0.05)

    def test_coalesce_preserves_existing(self, conn):
        """COALESCE(?, yt_impressions) with ? = None keeps existing value."""
        self._insert_clip_with_yt(conn, "yt_r3", impressions=200)
        now = datetime.now(UTC).isoformat()
        update_youtube_reach_metrics(conn, "yt_r3", impressions=None, impressions_ctr=None, synced_at=now)
        row = conn.execute("SELECT yt_impressions FROM clips WHERE youtube_id = 'yt_r3'").fetchone()
        assert row["yt_impressions"] == 200

    def test_higher_value_overwrites(self, conn):
        """Higher impressions value overwrites existing."""
        self._insert_clip_with_yt(conn, "yt_r4", impressions=200)
        now = datetime.now(UTC).isoformat()
        update_youtube_reach_metrics(conn, "yt_r4", impressions=300, impressions_ctr=None, synced_at=now)
        row = conn.execute("SELECT yt_impressions FROM clips WHERE youtube_id = 'yt_r4'").fetchone()
        assert row["yt_impressions"] == 300

    def test_lower_value_does_not_regress(self, conn):
        """Lower impressions value should NOT overwrite existing (MAX guard)."""
        self._insert_clip_with_yt(conn, "yt_r4b", impressions=500)
        now = datetime.now(UTC).isoformat()
        update_youtube_reach_metrics(conn, "yt_r4b", impressions=200, impressions_ctr=None, synced_at=now)
        row = conn.execute("SELECT yt_impressions FROM clips WHERE youtube_id = 'yt_r4b'").fetchone()
        assert row["yt_impressions"] == 500

    def test_synced_at_always_updated(self, conn):
        """yt_last_sync is set even when impression values are both None."""
        self._insert_clip_with_yt(conn, "yt_r5")
        synced = "2026-02-10T12:00:00+00:00"
        update_youtube_reach_metrics(conn, "yt_r5", impressions=None, impressions_ctr=None, synced_at=synced)
        row = conn.execute("SELECT yt_last_sync FROM clips WHERE youtube_id = 'yt_r5'").fetchone()
        assert row["yt_last_sync"] == synced

    def test_nonexistent_youtube_id(self, conn):
        """Calling with a youtube_id not in DB should not raise."""
        now = datetime.now(UTC).isoformat()
        update_youtube_reach_metrics(conn, "yt_nonexistent", impressions=100, impressions_ctr=0.05, synced_at=now)
        row = conn.execute("SELECT * FROM clips WHERE youtube_id = 'yt_nonexistent'").fetchone()
        assert row is None


class TestTouchYoutubeMetricsSync:
    """Tests for touch_youtube_metrics_sync — updates only yt_last_sync."""

    def test_updates_last_sync_only(self, conn):
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "INSERT INTO clips (clip_id, streamer, posted_at, youtube_id, yt_views, yt_impressions) VALUES (?, ?, ?, ?, ?, ?)",
            ("c_touch", "s", now, "yt_touch", 500, 1000),
        )
        conn.commit()

        later = "2026-02-10T18:00:00+00:00"
        touch_youtube_metrics_sync(conn, "yt_touch", later)

        row = conn.execute(
            "SELECT yt_last_sync, yt_views, yt_impressions FROM clips WHERE youtube_id = 'yt_touch'"
        ).fetchone()
        assert row["yt_last_sync"] == later
        assert row["yt_views"] == 500  # untouched
        assert row["yt_impressions"] == 1000  # untouched

    def test_nonexistent_youtube_id(self, conn):
        """Calling touch on a missing youtube_id should not raise."""
        touch_youtube_metrics_sync(conn, "yt_ghost", "2026-02-10T00:00:00+00:00")
        row = conn.execute("SELECT * FROM clips WHERE youtube_id = 'yt_ghost'").fetchone()
        assert row is None


class TestVodOverlaps:
    def _insert_vod_clip(self, conn, clip_id, vod_id, vod_offset, duration):
        conn.execute(
            "INSERT INTO clips (clip_id, streamer, vod_id, vod_offset, duration) VALUES (?, ?, ?, ?, ?)",
            (clip_id, "s", vod_id, vod_offset, duration),
        )
        conn.commit()

    def test_overlapping_ranges(self, conn):
        """Clip at [100, 130] overlaps with query [120, 150]."""
        self._insert_vod_clip(conn, "v1", "vod_abc", 100, 30)
        assert vod_overlaps(conn, "vod_abc", 120, 30) is True

    def test_non_overlapping_ranges(self, conn):
        """Clip at [100, 130] does not overlap with query [130, 160] (adjacent, no overlap)."""
        self._insert_vod_clip(conn, "v1", "vod_abc", 100, 30)
        assert vod_overlaps(conn, "vod_abc", 130, 30) is False

    def test_fully_contained(self, conn):
        """Clip at [100, 160] fully contains query [120, 140]."""
        self._insert_vod_clip(conn, "v1", "vod_abc", 100, 60)
        assert vod_overlaps(conn, "vod_abc", 120, 20) is True

    def test_different_vod_id_no_overlap(self, conn):
        """Same offset but different VOD — no overlap."""
        self._insert_vod_clip(conn, "v1", "vod_abc", 100, 30)
        assert vod_overlaps(conn, "vod_xyz", 100, 30) is False

    def test_returns_false_when_vod_id_none(self, conn):
        """If vod_id is None (VOD deleted), always returns False."""
        self._insert_vod_clip(conn, "v1", "vod_abc", 100, 30)
        assert vod_overlaps(conn, None, 100, 30) is False

    def test_returns_false_when_vod_offset_none(self, conn):
        """If vod_offset is None, always returns False."""
        self._insert_vod_clip(conn, "v1", "vod_abc", 100, 30)
        assert vod_overlaps(conn, "vod_abc", None, 30) is False

    def test_exclude_clip_id(self, conn):
        """A clip should not overlap with itself."""
        self._insert_vod_clip(conn, "v1", "vod_abc", 100, 30)
        assert vod_overlaps(conn, "vod_abc", 100, 30, exclude_clip_id="v1") is False

    def test_insert_clip_stores_vod_fields(self, conn):
        """insert_clip should persist vod_id, vod_offset, and duration."""
        clip = make_clip(clip_id="vod_test", vod_id="vod_123", vod_offset=500, duration=25)
        insert_clip(conn, clip)
        row = conn.execute("SELECT vod_id, vod_offset, duration FROM clips WHERE clip_id = 'vod_test'").fetchone()
        assert row["vod_id"] == "vod_123"
        assert row["vod_offset"] == 500
        assert row["duration"] == 25

    def test_record_known_clip_stores_vod_fields(self, conn):
        """record_known_clip should persist vod_id, vod_offset, and duration."""
        clip = make_clip(clip_id="vod_known", vod_id="vod_456", vod_offset=200, duration=40, youtube_id="yt_ext")
        record_known_clip(conn, clip)
        row = conn.execute("SELECT vod_id, vod_offset, duration FROM clips WHERE clip_id = 'vod_known'").fetchone()
        assert row["vod_id"] == "vod_456"
        assert row["vod_offset"] == 200
        assert row["duration"] == 40


class TestUpdateInstagramId:
    def test_sets_instagram_id(self, conn):
        clip = make_clip(clip_id="ig_1", youtube_id="yt_1")
        insert_clip(conn, clip)
        update_instagram_id(conn, "ig_1", "ig_media_123")
        row = conn.execute("SELECT instagram_id FROM clips WHERE clip_id = 'ig_1'").fetchone()
        assert row["instagram_id"] == "ig_media_123"

    def test_does_not_affect_youtube_id(self, conn):
        clip = make_clip(clip_id="ig_2", youtube_id="yt_2")
        insert_clip(conn, clip)
        update_instagram_id(conn, "ig_2", "ig_media_456")
        row = conn.execute("SELECT youtube_id, instagram_id FROM clips WHERE clip_id = 'ig_2'").fetchone()
        assert row["youtube_id"] == "yt_2"
        assert row["instagram_id"] == "ig_media_456"


class TestRecentInstagramUploadCount:
    def test_counts_recent_instagram_uploads(self, conn):
        clip = make_clip(clip_id="ig_cnt_1", youtube_id="yt_1", instagram_id="ig_1")
        insert_clip(conn, clip)
        count = recent_instagram_upload_count(conn, "teststreamer", hours=24)
        assert count == 1

    def test_excludes_clips_without_instagram_id(self, conn):
        clip = make_clip(clip_id="ig_cnt_2", youtube_id="yt_1")
        insert_clip(conn, clip)
        count = recent_instagram_upload_count(conn, "teststreamer", hours=24)
        assert count == 0

    def test_excludes_old_uploads(self, conn):
        old_time = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
        conn.execute(
            "INSERT INTO clips (clip_id, streamer, posted_at, instagram_id) VALUES (?, ?, ?, ?)",
            ("ig_old", "teststreamer", old_time, "ig_old_1"),
        )
        conn.commit()
        count = recent_instagram_upload_count(conn, "teststreamer", hours=24)
        assert count == 0


class TestInsertClipWithInstagramId:
    def test_insert_preserves_instagram_id(self, conn):
        clip = make_clip(clip_id="ig_ins_1", youtube_id="yt_1", instagram_id="ig_1")
        insert_clip(conn, clip)
        row = conn.execute("SELECT instagram_id FROM clips WHERE clip_id = 'ig_ins_1'").fetchone()
        assert row["instagram_id"] == "ig_1"

    def test_coalesce_preserves_existing_instagram_id(self, conn):
        """Re-inserting without instagram_id should NOT overwrite existing."""
        clip1 = make_clip(clip_id="ig_ins_2", youtube_id="yt_1", instagram_id="ig_1")
        insert_clip(conn, clip1)
        clip2 = make_clip(clip_id="ig_ins_2", youtube_id="yt_2")  # no instagram_id
        insert_clip(conn, clip2)
        row = conn.execute("SELECT instagram_id FROM clips WHERE clip_id = 'ig_ins_2'").fetchone()
        assert row["instagram_id"] == "ig_1"  # preserved


class TestUpsertClipMetadata:
    """Tests for upsert_clip_metadata — persist clips fetched from Twitch without uploading."""

    def test_inserts_new_clip_without_youtube_id(self, conn):
        """Should insert basic metadata without setting youtube_id or processing fields."""
        clip = make_clip(clip_id="meta_1", title="Fetched Clip", view_count=500, game_name="Valorant")
        upsert_clip_metadata(conn, clip)

        row = conn.execute("SELECT * FROM clips WHERE clip_id = 'meta_1'").fetchone()
        assert row["title"] == "Fetched Clip"
        assert row["view_count"] == 500
        assert row["game_name"] == "Valorant"
        assert row["youtube_id"] is None  # Not uploaded yet
        assert row["posted_at"] is None  # Not uploaded yet

    def test_updates_view_count_on_conflict(self, conn):
        """Re-upserting should update view_count with newer value."""
        clip1 = make_clip(clip_id="meta_2", view_count=100)
        upsert_clip_metadata(conn, clip1)

        clip2 = make_clip(clip_id="meta_2", view_count=250)
        upsert_clip_metadata(conn, clip2)

        row = conn.execute("SELECT view_count FROM clips WHERE clip_id = 'meta_2'").fetchone()
        assert row["view_count"] == 250

    def test_updates_title_on_conflict(self, conn):
        """Re-upserting should update title with newer value."""
        clip1 = make_clip(clip_id="meta_3", title="Old Title")
        upsert_clip_metadata(conn, clip1)

        clip2 = make_clip(clip_id="meta_3", title="Updated Title")
        upsert_clip_metadata(conn, clip2)

        row = conn.execute("SELECT title FROM clips WHERE clip_id = 'meta_3'").fetchone()
        assert row["title"] == "Updated Title"

    def test_preserves_existing_youtube_id(self, conn):
        """Should NOT overwrite youtube_id if clip was already uploaded."""
        clip = make_clip(clip_id="meta_4", youtube_id="yt_existing")
        insert_clip(conn, clip)

        # Re-upsert metadata without youtube_id
        clip_update = make_clip(clip_id="meta_4", view_count=999)
        upsert_clip_metadata(conn, clip_update)

        row = conn.execute("SELECT youtube_id, view_count FROM clips WHERE clip_id = 'meta_4'").fetchone()
        assert row["youtube_id"] == "yt_existing"  # preserved
        assert row["view_count"] == 999  # updated

    def test_preserves_existing_posted_at(self, conn):
        """Should NOT overwrite posted_at if clip was already uploaded."""
        clip = make_clip(clip_id="meta_5", youtube_id="yt_5")
        insert_clip(conn, clip)

        original_posted = conn.execute("SELECT posted_at FROM clips WHERE clip_id = 'meta_5'").fetchone()["posted_at"]
        assert original_posted is not None

        # Re-upsert metadata
        clip_update = make_clip(clip_id="meta_5", view_count=777)
        upsert_clip_metadata(conn, clip_update)

        row = conn.execute("SELECT posted_at FROM clips WHERE clip_id = 'meta_5'").fetchone()
        assert row["posted_at"] == original_posted  # preserved

    def test_stores_vod_fields(self, conn):
        """Should persist vod_id, vod_offset, and duration."""
        clip = make_clip(clip_id="meta_vod", vod_id="vod_789", vod_offset=300, duration=25)
        upsert_clip_metadata(conn, clip)

        row = conn.execute("SELECT vod_id, vod_offset, duration FROM clips WHERE clip_id = 'meta_vod'").fetchone()
        assert row["vod_id"] == "vod_789"
        assert row["vod_offset"] == 300
        assert row["duration"] == 25

    def test_updates_game_name_when_provided(self, conn):
        """Should update game_name with newer value when provided."""
        clip1 = make_clip(clip_id="meta_6", game_name="")
        upsert_clip_metadata(conn, clip1)

        clip2 = make_clip(clip_id="meta_6", game_name="Apex Legends")
        upsert_clip_metadata(conn, clip2)

        row = conn.execute("SELECT game_name FROM clips WHERE clip_id = 'meta_6'").fetchone()
        assert row["game_name"] == "Apex Legends"

    def test_preserves_game_name_when_empty_string(self, conn):
        """Should NOT overwrite existing game_name with empty string."""
        clip1 = make_clip(clip_id="meta_7", game_name="Fortnite")
        upsert_clip_metadata(conn, clip1)

        clip2 = make_clip(clip_id="meta_7", game_name="")
        upsert_clip_metadata(conn, clip2)

        row = conn.execute("SELECT game_name FROM clips WHERE clip_id = 'meta_7'").fetchone()
        assert row["game_name"] == "Fortnite"  # preserved

    def test_does_not_set_title_variant(self, conn):
        """upsert_clip_metadata should NOT set title_variant (upload-time field)."""
        clip = make_clip(clip_id="meta_8", title_variant="template_1")
        upsert_clip_metadata(conn, clip)

        row = conn.execute("SELECT title_variant FROM clips WHERE clip_id = 'meta_8'").fetchone()
        # title_variant is only set during insert_clip or record_known_clip
        assert row["title_variant"] is None or row["title_variant"] == ""

    def test_clip_eligible_for_upload_after_metadata_insert(self, conn):
        """Clips with only metadata (no youtube_id) should remain eligible for upload."""
        from src.dedup import filter_new_clips

        clip = make_clip(clip_id="meta_eligible", view_count=500)
        upsert_clip_metadata(conn, clip)

        # Check that clip is NOT filtered out by filter_new_clips
        candidates = [make_clip(clip_id="meta_eligible", view_count=500)]
        result = filter_new_clips(conn, candidates)

        # Since youtube_id IS NULL, clip should still be eligible
        assert len(result) == 1
        assert result[0].id == "meta_eligible"

    def test_uploaded_clip_not_eligible_after_insert_clip(self, conn):
        """Clips with youtube_id should NOT be eligible after insert_clip."""
        from src.dedup import filter_new_clips

        clip = make_clip(clip_id="meta_uploaded", youtube_id="yt_meta")
        insert_clip(conn, clip)

        # Try to filter the same clip
        candidates = [make_clip(clip_id="meta_uploaded")]
        result = filter_new_clips(conn, candidates)

        # Clip has youtube_id, so should be filtered out
        assert len(result) == 0

from datetime import datetime, timezone, timedelta

import pytest

from src.db import (
    insert_clip,
    record_known_clip,
    increment_fail_count,
    recent_upload_count,
    update_streamer_stats,
    clip_overlaps,
    get_clips_for_metrics,
    update_youtube_metrics,
    touch_youtube_metrics_sync,
    get_streamer_performance_multiplier,
)
from tests.conftest import make_clip


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


class TestRecentUploadCount:
    def test_counts_recent_uploads(self, conn):
        clip = make_clip(clip_id="recent_1", youtube_id="yt_1")
        insert_clip(conn, clip)

        count = recent_upload_count(conn, "teststreamer", hours=4)
        assert count == 1

    def test_excludes_old_uploads(self, conn):
        """Clips posted more than N hours ago should not be counted."""
        old_time = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
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


class TestUpdateStreamerStats:
    def test_computes_rolling_30d_averages(self, conn):
        now = datetime.now(timezone.utc)
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
        base = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        conn.execute(
            "INSERT INTO clips (clip_id, streamer, created_at) VALUES (?, ?, ?)",
            ("x1", "s", base.isoformat()),
        )
        conn.commit()
        assert clip_overlaps(conn, "s", (base + timedelta(seconds=20)).isoformat()) is True

    def test_allows_clips_outside_30s_window(self, conn):
        base = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        conn.execute(
            "INSERT INTO clips (clip_id, streamer, created_at) VALUES (?, ?, ?)",
            ("x1", "s", base.isoformat()),
        )
        conn.commit()
        assert clip_overlaps(conn, "s", (base + timedelta(seconds=60)).isoformat()) is False

    def test_overlap_is_streamer_scoped(self, conn):
        base = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        conn.execute(
            "INSERT INTO clips (clip_id, streamer, created_at) VALUES (?, ?, ?)",
            ("x1", "streamer_a", base.isoformat()),
        )
        conn.commit()
        assert clip_overlaps(conn, "streamer_b", (base + timedelta(seconds=5)).isoformat()) is False


    def test_exclude_clip_id_ignores_self_match(self, conn):
        """A clip should not overlap with its own DB row."""
        base = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
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
        now = datetime.now(timezone.utc)
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
        now = datetime.now(timezone.utc).isoformat()
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

        later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        touch_youtube_metrics_sync(conn, "yt1", later)
        row = conn.execute("SELECT yt_last_sync FROM clips WHERE youtube_id = 'yt1'").fetchone()
        assert row["yt_last_sync"] == later


class TestPerformanceMultiplier:
    def test_returns_one_with_no_data(self, conn):
        assert get_streamer_performance_multiplier(conn, "nobody") == 1.0

    def test_returns_one_with_fewer_than_three_data_points(self, conn):
        now = datetime.now(timezone.utc).isoformat()
        for i in range(2):
            conn.execute(
                "INSERT INTO clips (clip_id, streamer, youtube_id, yt_impressions_ctr) VALUES (?, ?, ?, ?)",
                (f"pm_{i}", "s", f"yt_{i}", 0.04),
            )
        conn.commit()
        assert get_streamer_performance_multiplier(conn, "s") == 1.0

    def test_high_ctr_boosts_multiplier(self, conn):
        for i in range(5):
            conn.execute(
                "INSERT INTO clips (clip_id, streamer, youtube_id, yt_impressions_ctr) VALUES (?, ?, ?, ?)",
                (f"hi_{i}", "good", f"yt_hi_{i}", 0.04),
            )
        conn.commit()
        mult = get_streamer_performance_multiplier(conn, "good")
        assert mult > 1.0

    def test_low_ctr_reduces_multiplier(self, conn):
        for i in range(5):
            conn.execute(
                "INSERT INTO clips (clip_id, streamer, youtube_id, yt_impressions_ctr) VALUES (?, ?, ?, ?)",
                (f"lo_{i}", "poor", f"yt_lo_{i}", 0.005),
            )
        conn.commit()
        mult = get_streamer_performance_multiplier(conn, "poor")
        assert mult < 1.0

    def test_multiplier_clamped(self, conn):
        for i in range(5):
            conn.execute(
                "INSERT INTO clips (clip_id, streamer, youtube_id, yt_impressions_ctr) VALUES (?, ?, ?, ?)",
                (f"ex_{i}", "extreme", f"yt_ex_{i}", 0.20),
            )
        conn.commit()
        mult = get_streamer_performance_multiplier(conn, "extreme")
        assert mult == 2.0

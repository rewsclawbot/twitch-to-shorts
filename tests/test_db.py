from datetime import datetime, timezone, timedelta

import pytest

from src.db import (
    insert_clip,
    increment_fail_count,
    recent_upload_count,
    update_streamer_stats,
    clip_overlaps,
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

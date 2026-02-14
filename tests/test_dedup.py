from datetime import UTC, datetime, timedelta

import pytest

from src.db import increment_fail_count, insert_clip
from src.dedup import filter_new_clips
from tests.conftest import make_clip


class TestFilterNewClips:
    def test_empty_input_returns_empty(self, conn):
        assert filter_new_clips(conn, []) == []

    def test_removes_already_existing_clip_ids(self, conn):
        """Clips whose IDs are already in the database should be excluded."""
        base_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        existing = make_clip(clip_id="existing_1", view_count=500, youtube_id="yt_abc",
                             created_at=base_time.isoformat())
        insert_clip(conn, existing)

        candidates = [
            make_clip(clip_id="existing_1", created_at=base_time.isoformat()),
            # Different timestamp so it doesn't get caught by overlap detection
            make_clip(clip_id="brand_new", created_at=(base_time + timedelta(minutes=10)).isoformat()),
        ]
        result = filter_new_clips(conn, candidates)
        assert len(result) == 1
        assert result[0].id == "brand_new"

    def test_removes_overlapping_timestamps(self, conn):
        """A clip within 30s of an existing clip from the same streamer is rejected."""
        base_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        existing = make_clip(
            clip_id="old_clip",
            streamer="streamer_x",
            created_at=base_time.isoformat(),
        )
        insert_clip(conn, existing)

        overlapping = make_clip(
            clip_id="new_clip",
            streamer="streamer_x",
            created_at=(base_time + timedelta(seconds=15)).isoformat(),
        )
        result = filter_new_clips(conn, [overlapping])
        assert len(result) == 0

    def test_batch_overlap_within_same_run(self, conn):
        """Two new clips in the same batch within 30s should not both pass."""
        base_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        first = make_clip(
            clip_id="batch_1",
            streamer="streamer_x",
            created_at=base_time.isoformat(),
        )
        second = make_clip(
            clip_id="batch_2",
            streamer="streamer_x",
            created_at=(base_time + timedelta(seconds=20)).isoformat(),
        )
        result = filter_new_clips(conn, [first, second])
        assert len(result) == 1
        assert result[0].id == "batch_1"

    def test_passes_through_genuinely_new_clips(self, conn):
        """A clip with a new ID and no timestamp overlap should pass through."""
        base_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        existing = make_clip(
            clip_id="old_clip",
            streamer="streamer_x",
            created_at=base_time.isoformat(),
        )
        insert_clip(conn, existing)

        new_clip = make_clip(
            clip_id="totally_new",
            streamer="streamer_x",
            created_at=(base_time + timedelta(minutes=10)).isoformat(),
        )
        result = filter_new_clips(conn, [new_clip])
        assert len(result) == 1
        assert result[0].id == "totally_new"

    def test_failed_clip_can_retry(self, conn):
        """A clip recorded via increment_fail_count should still pass filter_new_clips."""
        base_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        clip = make_clip(
            clip_id="retry_me",
            streamer="streamer_x",
            created_at=base_time.isoformat(),
        )
        # Simulate a prior failure — this inserts a DB row with created_at
        increment_fail_count(conn, clip)

        # The same clip should still pass through filter_new_clips (fail_count < 5)
        result = filter_new_clips(conn, [clip])
        assert len(result) == 1
        assert result[0].id == "retry_me"

    def test_failed_clip_blocked_at_threshold_within_24h(self, conn):
        base_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        clip = make_clip(
            clip_id="fail_blocked",
            streamer="streamer_x",
            created_at=base_time.isoformat(),
        )
        conn.execute(
            "INSERT INTO clips (clip_id, streamer, created_at, fail_count, last_failed_at) VALUES (?, ?, ?, ?, ?)",
            (clip.id, clip.streamer, clip.created_at, 5, datetime.now(UTC).isoformat()),
        )
        conn.commit()
        assert filter_new_clips(conn, [clip]) == []

    def test_failed_clip_can_retry_after_24h(self, conn):
        base_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        clip = make_clip(
            clip_id="fail_retry_24h",
            streamer="streamer_x",
            created_at=base_time.isoformat(),
        )
        old_failure = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
        conn.execute(
            "INSERT INTO clips (clip_id, streamer, created_at, fail_count, last_failed_at) VALUES (?, ?, ?, ?, ?)",
            (clip.id, clip.streamer, clip.created_at, 5, old_failure),
        )
        conn.commit()
        result = filter_new_clips(conn, [clip])
        assert len(result) == 1
        assert result[0].id == "fail_retry_24h"

    def test_different_streamer_same_timestamp_passes(self, conn):
        """Overlap detection is scoped to the same streamer."""
        base_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        existing = make_clip(
            clip_id="old_clip",
            streamer="streamer_a",
            created_at=base_time.isoformat(),
        )
        insert_clip(conn, existing)

        new_clip = make_clip(
            clip_id="new_clip",
            streamer="streamer_b",
            created_at=(base_time + timedelta(seconds=5)).isoformat(),
        )
        result = filter_new_clips(conn, [new_clip])
        assert len(result) == 1

    def test_vod_overlap_filters_duplicate_from_db(self, conn):
        """A new clip overlapping a DB clip's VOD range should be filtered out."""
        base_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        existing = make_clip(
            clip_id="db_clip",
            streamer="streamer_x",
            created_at=base_time.isoformat(),
            vod_id="vod_abc",
            vod_offset=100,
            duration=30,
        )
        insert_clip(conn, existing)

        # New clip overlaps: [120, 150] overlaps [100, 130]
        new_clip = make_clip(
            clip_id="new_vod_dup",
            streamer="streamer_x",
            created_at=(base_time + timedelta(minutes=5)).isoformat(),
            vod_id="vod_abc",
            vod_offset=120,
            duration=30,
        )
        result = filter_new_clips(conn, [new_clip])
        assert len(result) == 0

    def test_vod_no_overlap_passes(self, conn):
        """A new clip adjacent to (but not overlapping) a DB clip's VOD range should pass."""
        base_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        existing = make_clip(
            clip_id="db_clip",
            streamer="streamer_x",
            created_at=base_time.isoformat(),
            vod_id="vod_abc",
            vod_offset=100,
            duration=30,
        )
        insert_clip(conn, existing)

        # New clip at [130, 160] — adjacent, no overlap
        new_clip = make_clip(
            clip_id="new_adjacent",
            streamer="streamer_x",
            created_at=(base_time + timedelta(minutes=5)).isoformat(),
            vod_id="vod_abc",
            vod_offset=130,
            duration=30,
        )
        result = filter_new_clips(conn, [new_clip])
        assert len(result) == 1

    def test_vod_none_falls_back_to_created_at(self, conn):
        """Clips without vod_id should still use created_at dedup."""
        base_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        existing = make_clip(
            clip_id="db_clip",
            streamer="streamer_x",
            created_at=base_time.isoformat(),
        )
        insert_clip(conn, existing)

        # Within 30s created_at window, no VOD data
        new_clip = make_clip(
            clip_id="no_vod",
            streamer="streamer_x",
            created_at=(base_time + timedelta(seconds=15)).isoformat(),
        )
        result = filter_new_clips(conn, [new_clip])
        assert len(result) == 0

    def test_batch_vod_overlap_keeps_first(self, conn):
        """Within a batch, two clips sharing VOD range should keep only the first (highest-ranked)."""
        base_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        clip_a = make_clip(
            clip_id="batch_vod_1",
            streamer="streamer_x",
            created_at=base_time.isoformat(),
            vod_id="vod_abc",
            vod_offset=100,
            duration=30,
        )
        clip_b = make_clip(
            clip_id="batch_vod_2",
            streamer="streamer_x",
            created_at=(base_time + timedelta(minutes=3)).isoformat(),
            vod_id="vod_abc",
            vod_offset=110,
            duration=30,
        )
        result = filter_new_clips(conn, [clip_a, clip_b])
        assert len(result) == 1
        assert result[0].id == "batch_vod_1"

    def test_batch_vod_different_vods_both_pass(self, conn):
        """Clips from different VODs should both pass batch dedup."""
        base_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        clip_a = make_clip(
            clip_id="vod1_clip",
            streamer="streamer_x",
            created_at=base_time.isoformat(),
            vod_id="vod_abc",
            vod_offset=100,
            duration=30,
        )
        clip_b = make_clip(
            clip_id="vod2_clip",
            streamer="streamer_x",
            created_at=(base_time + timedelta(minutes=3)).isoformat(),
            vod_id="vod_xyz",
            vod_offset=100,
            duration=30,
        )
        result = filter_new_clips(conn, [clip_a, clip_b])
        assert len(result) == 2

    def test_vod_overlap_cluster_boost_applied_to_survivor(self, conn):
        base_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        keeper = make_clip(
            clip_id="cluster_keep",
            streamer="streamer_x",
            created_at=base_time.isoformat(),
            vod_id="vod_cluster",
            vod_offset=100,
            duration=30,
        )
        keeper.score = 10.0
        overlapped = make_clip(
            clip_id="cluster_drop",
            streamer="streamer_x",
            created_at=(base_time + timedelta(seconds=10)).isoformat(),
            vod_id="vod_cluster",
            vod_offset=110,
            duration=30,
        )
        overlapped.score = 9.0
        other = make_clip(
            clip_id="other",
            streamer="streamer_x",
            created_at=(base_time + timedelta(minutes=5)).isoformat(),
            vod_id="vod_other",
            vod_offset=100,
            duration=30,
        )
        other.score = 7.0

        result = filter_new_clips(conn, [keeper, overlapped, other])
        assert [c.id for c in result] == ["cluster_keep", "other"]
        assert result[0].score == pytest.approx(11.0)  # 10 * (1 + 0.1 * (2 - 1))
        assert result[1].score == 7.0

    def test_vod_overlap_cluster_boost_capped_at_2x(self, conn):
        base_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        keeper = make_clip(
            clip_id="cluster_cap_keep",
            streamer="streamer_x",
            created_at=base_time.isoformat(),
            vod_id="vod_cap",
            vod_offset=100,
            duration=30,
        )
        keeper.score = 10.0
        overlaps = [
            make_clip(
                clip_id=f"cluster_cap_{i}",
                streamer="streamer_x",
                created_at=(base_time + timedelta(seconds=i)).isoformat(),
                vod_id="vod_cap",
                vod_offset=100 + i,
                duration=30,
            )
            for i in range(1, 15)
        ]
        for c in overlaps:
            c.score = 5.0

        result = filter_new_clips(conn, [keeper, *overlaps])
        assert len(result) == 1
        assert result[0].id == "cluster_cap_keep"
        assert result[0].score == pytest.approx(20.0)

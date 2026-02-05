from datetime import datetime, timezone, timedelta

import pytest

from src.dedup import filter_new_clips
from src.db import insert_clip, increment_fail_count
from tests.conftest import make_clip


class TestFilterNewClips:
    def test_empty_input_returns_empty(self, conn):
        assert filter_new_clips(conn, []) == []

    def test_removes_already_existing_clip_ids(self, conn):
        """Clips whose IDs are already in the database should be excluded."""
        base_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
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
        base_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
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

    def test_passes_through_genuinely_new_clips(self, conn):
        """A clip with a new ID and no timestamp overlap should pass through."""
        base_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
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
        base_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        clip = make_clip(
            clip_id="retry_me",
            streamer="streamer_x",
            created_at=base_time.isoformat(),
        )
        # Simulate a prior failure — this inserts a DB row with created_at
        increment_fail_count(conn, clip)

        # The same clip should still pass through filter_new_clips (fail_count < 3)
        result = filter_new_clips(conn, [clip])
        assert len(result) == 1
        assert result[0].id == "retry_me"

    def test_different_streamer_same_timestamp_passes(self, conn):
        """Overlap detection is scoped to the same streamer."""
        base_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
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

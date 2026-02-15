"""Tests for clip queue persistence."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from src.db import get_connection
from src.db_queue import (
    dequeue_top_clips,
    enqueue_clips,
    expire_old_queue,
    get_queue_stats,
    mark_clip_uploaded,
)
from src.models import Clip


@pytest.fixture
def conn():
    """In-memory database for testing."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from src.db import init_schema
    init_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def sample_clips():
    """Create sample clips for testing."""
    return [
        Clip(
            id="clip1",
            url="https://example.com/1",
            title="Amazing Play",
            view_count=1000,
            created_at="2026-01-15T12:00:00Z",
            duration=30.0,
            game_id="game1",
            streamer="streamer1",
            channel_key="channel1",
            game_name="Test Game",
            score=0.95,
        ),
        Clip(
            id="clip2",
            url="https://example.com/2",
            title="Nice Moment",
            view_count=500,
            created_at="2026-01-15T13:00:00Z",
            duration=25.0,
            game_id="game1",
            streamer="streamer1",
            channel_key="channel1",
            game_name="Test Game",
            score=0.75,
        ),
        Clip(
            id="clip3",
            url="https://example.com/3",
            title="Epic Win",
            view_count=2000,
            created_at="2026-01-15T14:00:00Z",
            duration=45.0,
            game_id="game2",
            streamer="streamer2",
            channel_key="channel2",
            game_name="Other Game",
            score=0.85,
        ),
    ]


class TestEnqueueClips:
    """Test enqueueing clips."""

    def test_enqueue_single_clip(self, conn, sample_clips):
        clips_with_scores = [(sample_clips[0], sample_clips[0].score)]
        enqueue_clips(conn, clips_with_scores)

        row = conn.execute("SELECT * FROM clip_queue WHERE clip_id = ?", ("clip1",)).fetchone()
        assert row is not None
        assert row["clip_id"] == "clip1"
        assert row["streamer"] == "streamer1"
        assert row["score"] == 0.95
        assert row["status"] == "pending"

    def test_enqueue_multiple_clips(self, conn, sample_clips):
        clips_with_scores = [(c, c.score) for c in sample_clips]
        enqueue_clips(conn, clips_with_scores)

        rows = conn.execute("SELECT COUNT(*) as cnt FROM clip_queue").fetchone()
        assert rows["cnt"] == 3

    def test_enqueue_updates_existing(self, conn, sample_clips):
        # Enqueue with one score
        clip = sample_clips[0]
        enqueue_clips(conn, [(clip, 0.5)])

        # Re-enqueue with different score
        enqueue_clips(conn, [(clip, 0.95)])

        row = conn.execute("SELECT score FROM clip_queue WHERE clip_id = ?", ("clip1",)).fetchone()
        assert row["score"] == 0.95  # Updated


class TestDequeueClips:
    """Test dequeueing clips."""

    def test_dequeue_top_clips(self, conn, sample_clips):
        # Enqueue all clips
        clips_with_scores = [(c, c.score) for c in sample_clips]
        enqueue_clips(conn, clips_with_scores)

        # Dequeue top 2
        dequeued = dequeue_top_clips(conn, limit=2)
        assert len(dequeued) == 2
        # Should be ordered by score descending
        assert dequeued[0].id == "clip1"  # score 0.95
        assert dequeued[1].id == "clip3"  # score 0.85

    def test_dequeue_by_streamer(self, conn, sample_clips):
        clips_with_scores = [(c, c.score) for c in sample_clips]
        enqueue_clips(conn, clips_with_scores)

        # Dequeue only streamer1 clips
        dequeued = dequeue_top_clips(conn, limit=10, streamer="streamer1")
        assert len(dequeued) == 2
        assert all(c.streamer == "streamer1" for c in dequeued)

    def test_dequeue_empty_queue(self, conn):
        dequeued = dequeue_top_clips(conn, limit=5)
        assert len(dequeued) == 0

    def test_dequeue_preserves_clip_data(self, conn, sample_clips):
        clip = sample_clips[0]
        enqueue_clips(conn, [(clip, clip.score)])

        dequeued = dequeue_top_clips(conn, limit=1)
        assert len(dequeued) == 1
        restored = dequeued[0]
        assert restored.id == clip.id
        assert restored.url == clip.url
        assert restored.title == clip.title
        assert restored.view_count == clip.view_count
        assert restored.duration == clip.duration
        assert restored.game_name == clip.game_name


class TestMarkUploaded:
    """Test marking clips as uploaded."""

    def test_mark_uploaded(self, conn, sample_clips):
        clips_with_scores = [(c, c.score) for c in sample_clips]
        enqueue_clips(conn, clips_with_scores)

        mark_clip_uploaded(conn, "clip1")

        row = conn.execute("SELECT status FROM clip_queue WHERE clip_id = ?", ("clip1",)).fetchone()
        assert row["status"] == "uploaded"

    def test_mark_uploaded_filters_from_dequeue(self, conn, sample_clips):
        clips_with_scores = [(c, c.score) for c in sample_clips]
        enqueue_clips(conn, clips_with_scores)

        mark_clip_uploaded(conn, "clip1")

        # Should not return uploaded clips
        dequeued = dequeue_top_clips(conn, limit=10)
        assert len(dequeued) == 2
        assert all(c.id != "clip1" for c in dequeued)


class TestExpireOldQueue:
    """Test expiring old clips."""

    def test_expire_old_clips(self, conn, sample_clips):
        # Enqueue clips with old timestamp
        old_time = (datetime.now(UTC) - timedelta(hours=100)).isoformat()
        conn.execute(
            "INSERT INTO clip_queue (clip_id, streamer, score, queued_at, status, clip_data) VALUES (?, ?, ?, ?, ?, ?)",
            ("old_clip", "streamer1", 0.8, old_time, "pending", "{}"),
        )

        # Enqueue a fresh clip
        clips_with_scores = [(sample_clips[0], sample_clips[0].score)]
        enqueue_clips(conn, clips_with_scores)

        # Expire clips older than 72 hours
        expired_count = expire_old_queue(conn, max_age_hours=72)
        assert expired_count == 1

        # Old clip should be expired
        row = conn.execute("SELECT status FROM clip_queue WHERE clip_id = ?", ("old_clip",)).fetchone()
        assert row["status"] == "expired"

        # Fresh clip should still be pending
        row = conn.execute("SELECT status FROM clip_queue WHERE clip_id = ?", ("clip1",)).fetchone()
        assert row["status"] == "pending"

    def test_expire_no_old_clips(self, conn, sample_clips):
        clips_with_scores = [(c, c.score) for c in sample_clips]
        enqueue_clips(conn, clips_with_scores)

        expired_count = expire_old_queue(conn, max_age_hours=72)
        assert expired_count == 0


class TestQueueStats:
    """Test queue statistics."""

    def test_get_queue_stats(self, conn, sample_clips):
        clips_with_scores = [(c, c.score) for c in sample_clips]
        enqueue_clips(conn, clips_with_scores)

        # Mark one as uploaded, expire another
        mark_clip_uploaded(conn, "clip1")
        conn.execute("UPDATE clip_queue SET status = 'expired' WHERE clip_id = ?", ("clip2",))
        conn.commit()

        stats = get_queue_stats(conn)
        assert stats["pending"] == 1
        assert stats["uploaded"] == 1
        assert stats["expired"] == 1

    def test_get_queue_stats_by_streamer(self, conn, sample_clips):
        clips_with_scores = [(c, c.score) for c in sample_clips]
        enqueue_clips(conn, clips_with_scores)

        stats = get_queue_stats(conn, streamer="streamer1")
        assert stats["pending"] == 2  # clip1 and clip2

        stats = get_queue_stats(conn, streamer="streamer2")
        assert stats["pending"] == 1  # clip3

    def test_get_queue_stats_empty(self, conn):
        stats = get_queue_stats(conn)
        assert stats["pending"] == 0
        assert stats["uploaded"] == 0
        assert stats["expired"] == 0

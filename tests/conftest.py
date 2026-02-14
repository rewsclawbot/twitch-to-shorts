import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from src.db import init_schema
from src.models import Clip


@pytest.fixture
def conn():
    """In-memory SQLite connection with full schema initialized."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    yield c
    c.close()


def make_clip(
    clip_id="clip_1",
    streamer="teststreamer",
    title="Great play",
    view_count=1000,
    duration=30,
    created_at=None,
    game_id="12345",
    game_name="",
    title_variant="",
    youtube_id=None,
    vod_id=None,
    vod_offset=None,
    instagram_id=None,
) -> Clip:
    """Factory for Clip dataclass instances."""
    if created_at is None:
        created_at = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    return Clip(
        id=clip_id,
        url=f"https://clips.twitch.tv/{clip_id}",
        title=title,
        view_count=view_count,
        created_at=created_at,
        duration=duration,
        game_id=game_id,
        game_name=game_name,
        title_variant=title_variant,
        streamer=streamer,
        youtube_id=youtube_id,
        instagram_id=instagram_id,
        vod_id=vod_id,
        vod_offset=vod_offset,
    )

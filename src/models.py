from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Clip:
    id: str
    url: str
    title: str
    view_count: int
    created_at: str
    thumbnail_url: str
    duration: float
    broadcaster_name: str
    game_id: str = ""
    streamer: str = ""
    game_name: str = ""
    score: float = 0.0
    youtube_id: str | None = None


@dataclass
class FacecamConfig:
    x: float = 0.0
    y: float = 0.75
    w: float = 0.25
    h: float = 0.25
    output_w: int = 420


@dataclass
class StreamerConfig:
    name: str
    twitch_id: str
    youtube_credentials: str
    facecam: FacecamConfig | None = None
    privacy_status: str = "public"
    category_id: str = "20"


@dataclass
class PipelineConfig:
    max_clips_per_streamer: int = 6
    max_clip_duration_seconds: int = 60
    velocity_weight: float = 2.0
    top_percentile: float = 0.10
    bootstrap_top_n: int = 10
    clip_lookback_hours: int = 24
    data_dir: str = "data"
    tmp_dir: str = "data/tmp"
    db_path: str = "data/clips.db"
    log_file: str = "data/pipeline.log"
    upload_spacing_hours: int = 4
    max_uploads_per_window: int = 1

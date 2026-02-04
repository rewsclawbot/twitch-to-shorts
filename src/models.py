from dataclasses import dataclass


@dataclass
class Clip:
    id: str
    url: str
    title: str
    view_count: int
    created_at: str
    duration: float
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
    facecam_mode: str = "auto"
    privacy_status: str = "public"
    category_id: str = "20"
    extra_tags: list[str] | None = None


@dataclass
class PipelineConfig:
    max_clips_per_streamer: int = 6
    max_clip_duration_seconds: int = 60
    velocity_weight: float = 2.0
    top_percentile: float = 0.10
    bootstrap_top_n: int = 10
    clip_lookback_hours: int = 168
    min_view_count: int = 0
    age_decay: str = "linear"
    view_transform: str = "linear"
    title_quality_weight: float = 0.0
    tmp_dir: str = "data/tmp"
    db_path: str = "data/clips.db"
    log_file: str = "data/pipeline.log"
    upload_spacing_hours: int = 4
    max_uploads_per_window: int = 1
    analytics_enabled: bool = False
    analytics_min_age_hours: int = 48
    analytics_sync_interval_hours: int = 24
    analytics_max_videos_per_run: int = 20

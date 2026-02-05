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
    channel_key: str = ""
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
    clip_lookback_hours: int = 168
    min_view_count: int = 300
    age_decay: str = "log"
    view_transform: str = "linear"
    title_quality_weight: float = 0.05
    tmp_dir: str = "data/tmp"
    db_path: str = "data/clips.db"
    log_file: str = "data/pipeline.log"
    upload_spacing_hours: int = 2
    max_uploads_per_window: int = 1
    analytics_enabled: bool = False
    analytics_min_age_hours: int = 48
    analytics_sync_interval_hours: int = 24
    analytics_max_videos_per_run: int = 20

    def __post_init__(self):
        errors: list[str] = []
        int_fields = [
            ("max_clips_per_streamer", self.max_clips_per_streamer),
            ("max_clip_duration_seconds", self.max_clip_duration_seconds),
            ("clip_lookback_hours", self.clip_lookback_hours),
            ("min_view_count", self.min_view_count),
            ("upload_spacing_hours", self.upload_spacing_hours),
            ("max_uploads_per_window", self.max_uploads_per_window),
            ("analytics_min_age_hours", self.analytics_min_age_hours),
            ("analytics_sync_interval_hours", self.analytics_sync_interval_hours),
            ("analytics_max_videos_per_run", self.analytics_max_videos_per_run),
        ]
        for name, value in int_fields:
            if not isinstance(value, int):
                try:
                    setattr(self, name, int(value))
                except (TypeError, ValueError):
                    errors.append(f"{name} must be an integer, got {value!r}")
                    continue
            if getattr(self, name) < 0:
                errors.append(f"{name} must be non-negative, got {getattr(self, name)}")

        float_fields = [
            ("velocity_weight", self.velocity_weight),
            ("title_quality_weight", self.title_quality_weight),
        ]
        for name, value in float_fields:
            if not isinstance(value, (int, float)):
                try:
                    setattr(self, name, float(value))
                except (TypeError, ValueError):
                    errors.append(f"{name} must be a number, got {value!r}")
                    continue
            if getattr(self, name) < 0:
                errors.append(f"{name} must be non-negative, got {getattr(self, name)}")

        if self.age_decay not in ("linear", "log"):
            errors.append(f"age_decay must be 'linear' or 'log', got {self.age_decay!r}")
        if self.view_transform not in ("linear", "log"):
            errors.append(f"view_transform must be 'linear' or 'log', got {self.view_transform!r}")

        if errors:
            raise ValueError("Invalid PipelineConfig:\n" + "\n".join(f"- {e}" for e in errors))

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
    title_variant: str = ""
    score: float = 0.0
    youtube_id: str | None = None
    instagram_id: str | None = None
    vod_id: str | None = None
    vod_offset: int | None = None


@dataclass
class CaptionWord:
    word: str
    start: float
    end: float
    confidence: float = 0.0


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
    captions: bool | None = None
    instagram_credentials: str | None = None


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
    duration_bonus_weight: float = 0.0
    audio_excitement_weight: float = 0.15
    hook_strength_weight: float = 0.0
    optimal_duration_min: int = 14
    optimal_duration_max: int = 31
    tmp_dir: str = "data/tmp"
    db_path: str = "data/clips.db"
    log_file: str = "data/pipeline.log"
    upload_spacing_hours: int = 2
    max_uploads_per_window: int = 1
    analytics_enabled: bool = False
    captions_enabled: bool = False
    instagram_enabled: bool = False
    analytics_min_age_hours: int = 48
    analytics_sync_interval_hours: int = 24
    analytics_max_videos_per_run: int = 20
    peak_action_trim: bool = True
    loop_optimize: bool = True
    context_overlay: bool = True
    smart_trim: bool = False
    smart_trim_target_duration: int = 15
    min_visual_quality: float = 0.3
    force_upload: bool = False
    posting_schedule: dict | None = None
    trending_boost_enabled: bool = False
    max_daily_uploads: int = 4
    rate_limit_lockfile: str = "data/upload_ratelimit.json"

    def __post_init__(self):
        errors: list[str] = []
        int_fields = [
            ("max_clips_per_streamer", self.max_clips_per_streamer),
            ("max_clip_duration_seconds", self.max_clip_duration_seconds),
            ("clip_lookback_hours", self.clip_lookback_hours),
            ("min_view_count", self.min_view_count),
            ("optimal_duration_min", self.optimal_duration_min),
            ("optimal_duration_max", self.optimal_duration_max),
            ("upload_spacing_hours", self.upload_spacing_hours),
            ("max_uploads_per_window", self.max_uploads_per_window),
            ("analytics_min_age_hours", self.analytics_min_age_hours),
            ("analytics_sync_interval_hours", self.analytics_sync_interval_hours),
            ("analytics_max_videos_per_run", self.analytics_max_videos_per_run),
            ("smart_trim_target_duration", self.smart_trim_target_duration),
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
            ("duration_bonus_weight", self.duration_bonus_weight),
            ("audio_excitement_weight", self.audio_excitement_weight),
            ("hook_strength_weight", self.hook_strength_weight),
            ("min_visual_quality", self.min_visual_quality),
        ]
        for name, numeric_value in float_fields:
            if not isinstance(numeric_value, (int, float)):
                try:
                    setattr(self, name, float(numeric_value))
                except (TypeError, ValueError):
                    errors.append(f"{name} must be a number, got {numeric_value!r}")
                    continue
            if getattr(self, name) < 0:
                errors.append(f"{name} must be non-negative, got {getattr(self, name)}")

        self.captions_enabled = bool(self.captions_enabled)
        self.instagram_enabled = bool(self.instagram_enabled)
        self.peak_action_trim = bool(self.peak_action_trim)
        self.loop_optimize = bool(self.loop_optimize)
        self.context_overlay = bool(self.context_overlay)
        self.smart_trim = bool(self.smart_trim)
        self.force_upload = bool(self.force_upload)
        self.trending_boost_enabled = bool(self.trending_boost_enabled)

        if self.age_decay not in ("linear", "log"):
            errors.append(f"age_decay must be 'linear' or 'log', got {self.age_decay!r}")
        if self.view_transform not in ("linear", "log"):
            errors.append(f"view_transform must be 'linear' or 'log', got {self.view_transform!r}")
        if self.optimal_duration_min > self.optimal_duration_max:
            errors.append(
                "optimal_duration_min must be <= optimal_duration_max, got "
                f"{self.optimal_duration_min} > {self.optimal_duration_max}"
            )
        if self.smart_trim_target_duration <= 0:
            errors.append(
                "smart_trim_target_duration must be > 0, got "
                f"{self.smart_trim_target_duration}"
            )
        if not 0.0 <= self.min_visual_quality <= 1.0:
            errors.append(
                "min_visual_quality must be between 0 and 1, got "
                f"{self.min_visual_quality}"
            )

        if errors:
            raise ValueError("Invalid PipelineConfig:\n" + "\n".join(f"- {e}" for e in errors))

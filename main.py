import argparse

import yaml
from dotenv import load_dotenv

from src import pipeline as pipeline_runner
from src.models import FacecamConfig, PipelineConfig, StreamerConfig

load_dotenv()


def load_config(path: str = "config.yaml") -> tuple[PipelineConfig, list[StreamerConfig], dict]:
    """Load config.yaml and return typed objects plus the raw dict for non-modeled keys."""
    with open(path) as f:
        raw = yaml.safe_load(f)

    pipeline_dict = raw.get("pipeline", {})
    captions_cfg = raw.get("captions", {})
    if "captions_enabled" not in pipeline_dict and captions_cfg.get("enabled"):
        pipeline_dict["captions_enabled"] = captions_cfg["enabled"]
    pipeline = PipelineConfig(**pipeline_dict)

    streamers: list[StreamerConfig] = []
    for streamer_cfg in raw.get("streamers", []):
        streamer_cfg = dict(streamer_cfg)
        facecam_data = streamer_cfg.pop("facecam", None)
        facecam = FacecamConfig(**facecam_data) if facecam_data else None
        streamers.append(StreamerConfig(facecam=facecam, **streamer_cfg))

    return pipeline, streamers, raw


def main():
    parser = argparse.ArgumentParser(description="Twitch-to-Shorts pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Run full pipeline but skip YouTube upload")
    args = parser.parse_args()

    pipeline_cfg, streamers, raw_config = load_config()
    pipeline_runner.run(pipeline_cfg, streamers, raw_config, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

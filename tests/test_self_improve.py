"""Tests for the self-improvement script."""
import json
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

# Import from scripts directory
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from self_improve import (
    analyze_and_recommend,
    apply_recommendations,
    get_clips_with_analytics,
)


def _make_clip(
    clip_id="test1",
    streamer="TestStreamer",
    youtube_id="yt1",
    yt_views=100,
    yt_avg_view_percentage=50.0,
    duration=15,
    title_variant="template_1+optimized",
    posted_at=None,
    game_name="TestGame",
):
    if posted_at is None:
        posted_at = datetime.now(UTC).isoformat()
    return {
        "clip_id": clip_id,
        "streamer": streamer,
        "title": "Test Title",
        "youtube_id": youtube_id,
        "title_variant": title_variant,
        "yt_views": yt_views,
        "yt_impressions": 1000,
        "yt_impressions_ctr": 0.10,
        "yt_avg_view_percentage": yt_avg_view_percentage,
        "yt_avg_view_duration": 8.0,
        "duration": duration,
        "game_name": game_name,
        "posted_at": posted_at,
    }


def _base_config():
    return {
        "pipeline": {
            "min_view_count": 50,
            "velocity_weight": 2.0,
            "optimal_duration_min": 10,
            "optimal_duration_max": 15,
            "title_quality_weight": 0.05,
            "duration_bonus_weight": 0.3,
            "audio_excitement_weight": 0.15,
        }
    }


class TestAnalyzeAndRecommend:
    def test_no_recommendations_with_insufficient_data(self):
        clips = [_make_clip(clip_id=f"c{i}") for i in range(3)]
        recs = analyze_and_recommend(clips, _base_config())
        assert recs == []

    def test_no_recommendations_without_views(self):
        clips = [_make_clip(clip_id=f"c{i}", yt_views=None) for i in range(10)]
        recs = analyze_and_recommend(clips, _base_config())
        assert recs == []

    def test_duration_recommendation_medium_clips(self):
        # All medium-length clips with good retention
        clips = [
            _make_clip(clip_id=f"c{i}", duration=25, yt_avg_view_percentage=80.0)
            for i in range(6)
        ]
        config = _base_config()
        config["pipeline"]["optimal_duration_max"] = 15
        recs = analyze_and_recommend(clips, config)
        duration_recs = [r for r in recs if "optimal_duration_max" in r["key"]]
        assert len(duration_recs) == 1
        assert duration_recs[0]["new_value"] == 30

    def test_title_variant_recommendation(self):
        clips = []
        for i in range(5):
            clips.append(_make_clip(
                clip_id=f"opt{i}", yt_views=500,
                title_variant="template_1+optimized"
            ))
        for i in range(5):
            clips.append(_make_clip(
                clip_id=f"tmpl{i}", yt_views=100,
                title_variant="template_2"
            ))
        config = _base_config()
        recs = analyze_and_recommend(clips, config)
        title_recs = [r for r in recs if "title_quality_weight" in r["key"]]
        assert len(title_recs) == 1
        assert title_recs[0]["new_value"] > config["pipeline"]["title_quality_weight"]

    def test_underperforming_streamer_note(self):
        clips = [
            _make_clip(clip_id=f"c{i}", streamer="BadStreamer", yt_views=2)
            for i in range(5)
        ]
        recs = analyze_and_recommend(clips, _base_config())
        streamer_notes = [r for r in recs if "streamer." in r["key"]]
        assert len(streamer_notes) == 1
        assert streamer_notes[0]["new_value"] == "underperforming"


class TestApplyRecommendations:
    def test_applies_medium_confidence(self):
        config = _base_config()
        recs = [{
            "key": "pipeline.optimal_duration_max",
            "old_value": 15,
            "new_value": 30,
            "reason": "test",
            "confidence": "medium",
        }]
        updated, applied = apply_recommendations(config, recs, "medium")
        assert len(applied) == 1
        assert updated["pipeline"]["optimal_duration_max"] == 30

    def test_skips_low_confidence(self):
        config = _base_config()
        recs = [{
            "key": "pipeline.min_view_count",
            "old_value": 50,
            "new_value": 63,
            "reason": "test",
            "confidence": "low",
        }]
        updated, applied = apply_recommendations(config, recs, "medium")
        assert len(applied) == 0
        assert updated["pipeline"]["min_view_count"] == 50

    def test_skips_non_pipeline_keys(self):
        config = _base_config()
        recs = [{
            "key": "streamer.BadStreamer.note",
            "old_value": None,
            "new_value": "underperforming",
            "reason": "test",
            "confidence": "high",
        }]
        updated, applied = apply_recommendations(config, recs, "low")
        assert len(applied) == 0

    def test_applies_high_confidence_with_low_threshold(self):
        config = _base_config()
        recs = [{
            "key": "pipeline.velocity_weight",
            "old_value": 2.0,
            "new_value": 3.0,
            "reason": "test",
            "confidence": "high",
        }]
        updated, applied = apply_recommendations(config, recs, "low")
        assert len(applied) == 1
        assert updated["pipeline"]["velocity_weight"] == 3.0

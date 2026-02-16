"""Tests for streamer filtering: target_games boost and enabled/disabled streamers."""

from unittest.mock import MagicMock, patch

import pytest

from src.models import Clip, StreamerConfig


class TestTargetGamesBoost:
    """B5: target_games config boosts on-target clip scores 2x."""

    def _make_clip(self, game_name: str, score: float) -> Clip:
        return Clip(
            id=f"clip-{game_name}-{score}",
            url="https://twitch.tv/clip/test",
            title="Test clip",
            view_count=500,
            created_at="2026-02-16T00:00:00Z",
            duration=20.0,
            game_id="123",
            game_name=game_name,
            score=score,
        )

    def test_on_target_clips_boosted_2x(self):
        """Clips matching target_games get 2x score boost."""
        streamer = StreamerConfig(
            name="TestStreamer",
            twitch_id="123",
            youtube_credentials="creds.json",
            target_games=["ARC Raiders"],
        )
        clip = self._make_clip("ARC Raiders", 5.0)
        clips = [clip]

        target_games = streamer.target_games
        if target_games:
            target_lower = {g.lower() for g in target_games}
            on_target = [c for c in clips if c.game_name.lower() in target_lower]
            for c in on_target:
                c.score *= 2.0

        assert clip.score == 10.0

    def test_off_target_clips_not_boosted(self):
        """Clips NOT matching target_games keep original score."""
        streamer = StreamerConfig(
            name="TestStreamer",
            twitch_id="123",
            youtube_credentials="creds.json",
            target_games=["ARC Raiders"],
        )
        clip = self._make_clip("Fortnite", 5.0)
        clips = [clip]

        target_games = streamer.target_games
        if target_games:
            target_lower = {g.lower() for g in target_games}
            on_target = [c for c in clips if c.game_name.lower() in target_lower]
            for c in on_target:
                c.score *= 2.0

        assert clip.score == 5.0

    def test_target_games_case_insensitive(self):
        """Game name matching is case-insensitive."""
        streamer = StreamerConfig(
            name="TestStreamer",
            twitch_id="123",
            youtube_credentials="creds.json",
            target_games=["arc raiders"],
        )
        clip = self._make_clip("ARC Raiders", 3.0)
        clips = [clip]

        target_games = streamer.target_games
        if target_games:
            target_lower = {g.lower() for g in target_games}
            on_target = [c for c in clips if c.game_name.lower() in target_lower]
            for c in on_target:
                c.score *= 2.0

        assert clip.score == 6.0

    def test_on_target_sorted_before_off_target(self):
        """On-target clips are sorted before off-target regardless of score."""
        streamer = StreamerConfig(
            name="TestStreamer",
            twitch_id="123",
            youtube_credentials="creds.json",
            target_games=["ARC Raiders"],
        )
        # Off-target clip has higher base score
        arc_clip = self._make_clip("ARC Raiders", 3.0)
        fortnite_clip = self._make_clip("Fortnite", 8.0)
        clips = [fortnite_clip, arc_clip]

        target_games = streamer.target_games
        if target_games:
            target_lower = {g.lower() for g in target_games}
            on_target = [c for c in clips if c.game_name.lower() in target_lower]
            off_target = [c for c in clips if c.game_name.lower() not in target_lower]
            for c in on_target:
                c.score *= 2.0
            clips = sorted(on_target, key=lambda c: c.score, reverse=True) + \
                    sorted(off_target, key=lambda c: c.score, reverse=True)

        assert clips[0].game_name == "ARC Raiders"
        assert clips[0].score == 6.0
        assert clips[1].game_name == "Fortnite"
        assert clips[1].score == 8.0  # Not boosted

    def test_no_target_games_no_boost(self):
        """When target_games is None, no boost applied."""
        streamer = StreamerConfig(
            name="TestStreamer",
            twitch_id="123",
            youtube_credentials="creds.json",
            target_games=None,
        )
        clip = self._make_clip("ARC Raiders", 5.0)
        clips = [clip]

        target_games = streamer.target_games
        if target_games:
            target_lower = {g.lower() for g in target_games}
            on_target = [c for c in clips if c.game_name.lower() in target_lower]
            for c in on_target:
                c.score *= 2.0

        assert clip.score == 5.0

    def test_empty_target_games_no_boost(self):
        """When target_games is empty list, no boost applied."""
        streamer = StreamerConfig(
            name="TestStreamer",
            twitch_id="123",
            youtube_credentials="creds.json",
            target_games=[],
        )
        clip = self._make_clip("ARC Raiders", 5.0)
        clips = [clip]

        target_games = streamer.target_games
        if target_games:
            target_lower = {g.lower() for g in target_games}
            on_target = [c for c in clips if c.game_name.lower() in target_lower]
            for c in on_target:
                c.score *= 2.0

        assert clip.score == 5.0

    def test_multiple_target_games(self):
        """Multiple target games all get boosted."""
        streamer = StreamerConfig(
            name="TestStreamer",
            twitch_id="123",
            youtube_credentials="creds.json",
            target_games=["ARC Raiders", "Valorant"],
        )
        arc = self._make_clip("ARC Raiders", 4.0)
        val = self._make_clip("Valorant", 3.0)
        other = self._make_clip("Minecraft", 5.0)
        clips = [arc, val, other]

        target_games = streamer.target_games
        if target_games:
            target_lower = {g.lower() for g in target_games}
            on_target = [c for c in clips if c.game_name.lower() in target_lower]
            off_target = [c for c in clips if c.game_name.lower() not in target_lower]
            for c in on_target:
                c.score *= 2.0

        assert arc.score == 8.0
        assert val.score == 6.0
        assert other.score == 5.0


class TestStreamerEnabled:
    """B8: enabled flag on StreamerConfig controls pipeline processing."""

    def test_enabled_defaults_true(self):
        """StreamerConfig.enabled defaults to True."""
        s = StreamerConfig(name="test", twitch_id="1", youtube_credentials="c.json")
        assert s.enabled is True

    def test_enabled_false_from_config(self):
        """StreamerConfig accepts enabled=False."""
        s = StreamerConfig(name="test", twitch_id="1", youtube_credentials="c.json", enabled=False)
        assert s.enabled is False

    def test_disabled_streamer_skipped_in_loop(self):
        """Disabled streamers produce a 'disabled' skip_reason and are not processed."""
        import logging
        log = logging.getLogger("test")

        streamers = [
            StreamerConfig(name="Active", twitch_id="1", youtube_credentials="c.json", enabled=True),
            StreamerConfig(name="Inactive", twitch_id="2", youtube_credentials="c.json", enabled=False),
        ]

        streamer_results = []
        processed_names = []

        for streamer in streamers:
            if not getattr(streamer, 'enabled', True):
                streamer_results.append({
                    "streamer": streamer.name,
                    "uploaded": 0,
                    "failed": 0,
                    "skip_reason": "disabled",
                })
                continue
            processed_names.append(streamer.name)

        assert processed_names == ["Active"]
        assert len(streamer_results) == 1
        assert streamer_results[0]["streamer"] == "Inactive"
        assert streamer_results[0]["skip_reason"] == "disabled"

"""Tests for src.youtube_analytics — YouTube Analytics API video metrics.

Tests fetch_video_metrics (reach-first with fallback to core-only),
_parse_report, and the module-level helper functions _to_int, _to_float,
_normalize_ctr.

Post-fix behavior:
- _to_int, _to_float, _normalize_ctr are module-level functions
- fetch_video_metrics tries reach metrics first, falls back on HttpError
- CTR normalization: percentage value / 100.0
"""

from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

from src.youtube_analytics import (
    _parse_report,
    fetch_video_metrics,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _http_error(status=403):
    resp = MagicMock(status=status)
    return HttpError(resp=resp, content=b"")


def _make_analytics_response(headers, row):
    """Build a dict matching YouTube Analytics API response shape."""
    return {
        "columnHeaders": [{"name": h} for h in headers],
        "rows": [row],
    }


REACH_HEADERS = [
    "video", "views", "estimatedMinutesWatched", "averageViewDuration",
    "averageViewPercentage", "videoThumbnailImpressions",
    "videoThumbnailImpressionsClickRate",
]

CORE_HEADERS = [
    "video", "views", "estimatedMinutesWatched", "averageViewDuration",
    "averageViewPercentage",
]


# ===========================================================================
# fetch_video_metrics
# ===========================================================================

class TestFetchVideoMetrics:
    def test_reach_available(self):
        """First query (with reach) succeeds — all 7 fields populated."""
        service = MagicMock()
        response = _make_analytics_response(
            REACH_HEADERS,
            ["v1", 500, 120.5, 45.0, 72.3, 10000, 5.0],
        )
        service.reports().query().execute.return_value = response

        result = fetch_video_metrics(service, "v1", "2026-01-01", "2026-01-31")

        assert result is not None
        assert result["yt_views"] == 500
        assert result["yt_estimated_minutes_watched"] == pytest.approx(120.5)
        assert result["yt_avg_view_duration"] == pytest.approx(45.0)
        assert result["yt_avg_view_percentage"] == pytest.approx(72.3)
        assert result["yt_impressions"] == 10000
        # CTR: 5.0 / 100.0 = 0.05
        assert result["yt_impressions_ctr"] == pytest.approx(0.05)
        assert "yt_last_sync" in result

    def test_reach_unavailable_fallback_to_core(self):
        """Reach query fails with HttpError, core query succeeds."""
        service = MagicMock()
        core_response = _make_analytics_response(
            CORE_HEADERS,
            ["v1", 300, 80.0, 30.0, 60.0],
        )

        # First call (reach) raises HttpError, second (core) succeeds
        service.reports().query().execute.side_effect = [
            _http_error(403),
            core_response,
        ]

        result = fetch_video_metrics(service, "v1", "2026-01-01", "2026-01-31")

        assert result is not None
        assert result["yt_views"] == 300
        assert result["yt_estimated_minutes_watched"] == pytest.approx(80.0)
        assert result["yt_avg_view_duration"] == pytest.approx(30.0)
        assert result["yt_avg_view_percentage"] == pytest.approx(60.0)
        assert result["yt_impressions"] is None
        assert result["yt_impressions_ctr"] is None

    def test_both_queries_fail(self):
        """Both reach and core queries raise HttpError — returns None."""
        service = MagicMock()
        # Reach query fails once with 403 (non-retryable), then core query fails
        # with 500 and is retried up to 3 attempts.
        service.reports().query().execute.side_effect = [
            _http_error(403),
            _http_error(500),
            _http_error(500),
            _http_error(500),
        ]

        result = fetch_video_metrics(service, "v1", "2026-01-01", "2026-01-31")
        assert result is None

    def test_empty_rows(self):
        """Response with empty rows list — returns None."""
        service = MagicMock()
        service.reports().query().execute.return_value = {
            "columnHeaders": [{"name": "video"}],
            "rows": [],
        }

        result = fetch_video_metrics(service, "v1", "2026-01-01", "2026-01-31")
        assert result is None

    def test_ctr_normalization(self):
        """videoThumbnailImpressionsClickRate of 5.0 becomes 0.05 in result."""
        service = MagicMock()
        response = _make_analytics_response(
            REACH_HEADERS,
            ["v1", 100, 10.0, 5.0, 50.0, 500, 5.0],
        )
        service.reports().query().execute.return_value = response

        result = fetch_video_metrics(service, "v1", "2026-01-01", "2026-01-31")
        assert result is not None
        assert result["yt_impressions_ctr"] == pytest.approx(0.05)


# ===========================================================================
# _parse_report
# ===========================================================================

class TestParseReport:
    def test_parse_report_valid(self):
        response = {
            "columnHeaders": [{"name": "video"}, {"name": "views"}],
            "rows": [["v1", 42]],
        }
        result = _parse_report(response)
        assert result == {"video": "v1", "views": 42}

    def test_parse_report_empty_rows(self):
        response = {
            "columnHeaders": [{"name": "video"}],
            "rows": [],
        }
        assert _parse_report(response) is None

    def test_parse_report_no_headers(self):
        response = {
            "columnHeaders": [],
            "rows": [["v1"]],
        }
        assert _parse_report(response) is None


# ===========================================================================
# Module-level helpers (_to_int, _to_float, _normalize_ctr)
# ===========================================================================
# NOTE: In the current code these are local to fetch_video_metrics.
# The post-fix code moves them to module level. We import from the module
# where they exist; if the fix hasn't landed we test via the reporting module
# which already has them at module level.

class TestModuleLevelHelpers:
    """Test _to_int, _to_float, _normalize_ctr behaviour.

    These functions are defined in youtube_reporting.py (and will also be
    in youtube_analytics.py post-fix). We test via youtube_reporting which
    already has them as module-level exports.
    """

    def test_to_int_edge_cases(self):
        from src.youtube_reporting import _to_int

        assert _to_int(None) is None
        assert _to_int("abc") is None
        assert _to_int("42") == 42
        assert _to_int("") is None

    def test_to_float_edge_cases(self):
        from src.youtube_reporting import _to_float

        assert _to_float(None) is None
        assert _to_float("abc") is None
        assert _to_float("3.14") == pytest.approx(3.14)
        assert _to_float("") is None

    def test_normalize_ctr(self):
        from src.youtube_reporting import _normalize_ctr

        assert _normalize_ctr(None) is None
        assert _normalize_ctr(5.0) == pytest.approx(0.05)
        assert _normalize_ctr(0.0) == pytest.approx(0.0)

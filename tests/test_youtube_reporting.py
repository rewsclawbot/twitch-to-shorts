"""Tests for src.youtube_reporting — YouTube Reporting API reach metrics.

Tests the fetch_reach_metrics pipeline, _ensure_job, _filter_reports,
date parsers, and _iter_report_rows against the post-fix behavior:
- CSV columns: video_thumbnail_impressions / video_thumbnail_impressions_ctr
- _filter_reports SKIPS reports with missing/unparseable dates
- _iter_report_rows catches Exception (not just HttpError)
- _list_jobs/_list_reports use bounded loops (for _ in range(100))
- fetch_reach_metrics deduplicates by (video_id, date)
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError

from src.youtube_reporting import (
    _ensure_job,
    _filter_reports,
    _iter_report_rows,
    _parse_iso_date,
    _parse_report_date,
    _parse_rfc3339_date,
    fetch_reach_metrics,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service_stub():
    """Return a MagicMock shaped like a YouTube Reporting API service."""
    return MagicMock()


def _http_error(status=403):
    resp = MagicMock(status=status)
    return HttpError(resp=resp, content=b"")


def _make_row(video_id, row_date, impressions, ctr):
    """Build a CSV-row dict matching the post-fix column names."""
    row = {"video_id": video_id, "date": row_date}
    if impressions is not None:
        row["video_thumbnail_impressions"] = str(impressions)
    if ctr is not None:
        row["video_thumbnail_impressions_ctr"] = str(ctr)
    return row


# ===========================================================================
# fetch_reach_metrics
# ===========================================================================

class TestFetchReachMetrics:
    def test_empty_video_ids_returns_empty(self):
        service = _make_service_stub()
        result = fetch_reach_metrics(service, set(), "2026-01-01", "2026-01-31")
        assert result == {}

    @patch("src.youtube_reporting._ensure_job", return_value=None)
    def test_no_job_returns_empty(self, _mock_ensure):
        service = _make_service_stub()
        result = fetch_reach_metrics(service, {"v1"}, "2026-01-01", "2026-01-31")
        assert result == {}

    @patch("src.youtube_reporting._list_reports", return_value=[])
    @patch("src.youtube_reporting._ensure_job", return_value="job-1")
    def test_no_reports_returns_empty(self, _mock_ensure, _mock_reports):
        service = _make_service_stub()
        result = fetch_reach_metrics(service, {"v1"}, "2026-01-01", "2026-01-31")
        assert result == {}

    @patch("src.youtube_reporting._ensure_job", return_value="job-1")
    def test_invalid_dates_returns_empty(self, _mock_ensure):
        service = _make_service_stub()
        result = fetch_reach_metrics(service, {"v1"}, "garbage", "2026-01-31")
        assert result == {}

    @patch("src.youtube_reporting._iter_report_rows")
    @patch("src.youtube_reporting._filter_reports")
    @patch("src.youtube_reporting._list_reports")
    @patch("src.youtube_reporting._ensure_job", return_value="job-1")
    def test_single_video_single_report(self, _mock_ensure, _mock_list, mock_filter,
                                         mock_iter):
        mock_filter.return_value = [{"downloadUrl": "http://example.com/report"}]
        mock_iter.return_value = iter([
            _make_row("v1", "20260115", 100, "5.0"),
        ])
        service = _make_service_stub()
        result = fetch_reach_metrics(service, {"v1"}, "2026-01-01", "2026-01-31")

        assert "v1" in result
        assert result["v1"]["yt_impressions"] == 100
        # CTR: 5.0 / 100.0 = 0.05
        assert result["v1"]["yt_impressions_ctr"] == pytest.approx(0.05)

    @patch("src.youtube_reporting._iter_report_rows")
    @patch("src.youtube_reporting._filter_reports")
    @patch("src.youtube_reporting._list_reports")
    @patch("src.youtube_reporting._ensure_job", return_value="job-1")
    def test_weighted_ctr_across_rows(self, _mock_ensure, _mock_list, mock_filter,
                                       mock_iter):
        """Two rows for same video: imp=100/ctr=4.0 and imp=200/ctr=8.0.
        Weighted CTR = (100*0.04 + 200*0.08) / 300 = 0.06667"""
        mock_filter.return_value = [{"downloadUrl": "http://example.com/r1"}]
        mock_iter.return_value = iter([
            _make_row("v1", "20260110", 100, "4.0"),
            _make_row("v1", "20260111", 200, "8.0"),
        ])
        service = _make_service_stub()
        result = fetch_reach_metrics(service, {"v1"}, "2026-01-01", "2026-01-31")

        assert result["v1"]["yt_impressions"] == 300
        expected_ctr = (100 * 0.04 + 200 * 0.08) / 300
        assert result["v1"]["yt_impressions_ctr"] == pytest.approx(expected_ctr, rel=1e-4)

    @patch("src.youtube_reporting._iter_report_rows")
    @patch("src.youtube_reporting._filter_reports")
    @patch("src.youtube_reporting._list_reports")
    @patch("src.youtube_reporting._ensure_job", return_value="job-1")
    def test_row_outside_date_range_excluded(self, _mock_ensure, _mock_list,
                                              mock_filter, mock_iter):
        mock_filter.return_value = [{"downloadUrl": "http://example.com/r1"}]
        # Row date 2025-12-01 is before start_date 2026-01-01
        mock_iter.return_value = iter([
            _make_row("v1", "20251201", 100, "5.0"),
        ])
        service = _make_service_stub()
        result = fetch_reach_metrics(service, {"v1"}, "2026-01-01", "2026-01-31")
        # Video should not be in results because its only row was out of range
        assert "v1" not in result

    @patch("src.youtube_reporting._iter_report_rows")
    @patch("src.youtube_reporting._filter_reports")
    @patch("src.youtube_reporting._list_reports")
    @patch("src.youtube_reporting._ensure_job", return_value="job-1")
    def test_zero_impressions_excluded_from_results(self, _mock_ensure, _mock_list,
                                                     mock_filter, mock_iter):
        mock_filter.return_value = [{"downloadUrl": "http://example.com/r1"}]
        mock_iter.return_value = iter([
            _make_row("v1", "20260110", 0, "5.0"),
        ])
        service = _make_service_stub()
        result = fetch_reach_metrics(service, {"v1"}, "2026-01-01", "2026-01-31")
        assert "v1" not in result

    @patch("src.youtube_reporting._iter_report_rows")
    @patch("src.youtube_reporting._filter_reports")
    @patch("src.youtube_reporting._list_reports")
    @patch("src.youtube_reporting._ensure_job", return_value="job-1")
    def test_missing_impressions_field_skipped(self, _mock_ensure, _mock_list,
                                                mock_filter, mock_iter):
        mock_filter.return_value = [{"downloadUrl": "http://example.com/r1"}]
        # Row has no video_thumbnail_impressions key
        mock_iter.return_value = iter([
            {"video_id": "v1", "date": "20260110", "video_thumbnail_impressions_ctr": "5.0"},
        ])
        service = _make_service_stub()
        result = fetch_reach_metrics(service, {"v1"}, "2026-01-01", "2026-01-31")
        assert "v1" not in result

    @patch("src.youtube_reporting._iter_report_rows")
    @patch("src.youtube_reporting._filter_reports")
    @patch("src.youtube_reporting._list_reports")
    @patch("src.youtube_reporting._ensure_job", return_value="job-1")
    def test_dedup_by_video_date(self, _mock_ensure, _mock_list, mock_filter,
                                  mock_iter):
        """Two reports yield the same (video_id, date) row — only counted once."""
        report1 = {"downloadUrl": "http://example.com/r1"}
        report2 = {"downloadUrl": "http://example.com/r2"}
        mock_filter.return_value = [report1, report2]

        # Both reports contain the same row for v1 on 20260110
        calls = [0]

        def side_effect(service, url):
            calls[0] += 1
            yield _make_row("v1", "20260110", 100, "5.0")

        mock_iter.side_effect = side_effect

        service = _make_service_stub()
        result = fetch_reach_metrics(service, {"v1"}, "2026-01-01", "2026-01-31")

        # Despite two reports yielding the same row, impressions should be 100, not 200
        assert result["v1"]["yt_impressions"] == 100
        assert mock_iter.call_count == 2


# ===========================================================================
# _ensure_job
# ===========================================================================

class TestEnsureJob:
    def test_existing_job_returned(self):
        service = _make_service_stub()
        service.jobs().list().execute.return_value = {
            "jobs": [{"id": "existing-job", "reportTypeId": "channel_reach_basic_a1"}],
        }
        result = _ensure_job(service, "channel_reach_basic_a1")
        assert result == "existing-job"

    def test_creates_new_job(self):
        service = _make_service_stub()
        # No matching job
        service.jobs().list().execute.return_value = {"jobs": []}
        # create returns new ID
        service.jobs().create().execute.return_value = {"id": "new-job-id"}

        result = _ensure_job(service, "channel_reach_basic_a1")
        assert result == "new-job-id"

    def test_create_job_http_error(self):
        service = _make_service_stub()
        service.jobs().list().execute.return_value = {"jobs": []}
        service.jobs().create().execute.side_effect = _http_error(403)

        result = _ensure_job(service, "channel_reach_basic_a1")
        assert result is None


# ===========================================================================
# _filter_reports
# ===========================================================================

class TestFilterReports:
    def test_reports_in_range_included(self):
        reports = [
            {"startTime": "2026-01-10T00:00:00Z", "endTime": "2026-01-20T00:00:00Z"},
        ]
        result = _filter_reports(reports, date(2026, 1, 1), date(2026, 1, 31))
        assert len(result) == 1

    def test_reports_outside_range_excluded(self):
        reports = [
            # Entirely before range
            {"startTime": "2025-12-01T00:00:00Z", "endTime": "2025-12-15T00:00:00Z"},
            # Entirely after range
            {"startTime": "2026-03-01T00:00:00Z", "endTime": "2026-03-15T00:00:00Z"},
        ]
        result = _filter_reports(reports, date(2026, 1, 1), date(2026, 1, 31))
        assert len(result) == 0

    def test_reports_with_missing_dates_excluded(self):
        """Report with no startTime/endTime is SKIPPED, not included."""
        reports = [
            {"startTime": None, "endTime": None},
            {},
            {"startTime": "2026-01-10T00:00:00Z"},  # missing endTime
        ]
        result = _filter_reports(reports, date(2026, 1, 1), date(2026, 1, 31))
        assert len(result) == 0


# ===========================================================================
# Date parsers
# ===========================================================================

class TestDateParsers:
    def test_parse_iso_date_valid(self):
        assert _parse_iso_date("2026-01-15") == date(2026, 1, 15)

    def test_parse_iso_date_invalid(self):
        assert _parse_iso_date("garbage") is None

    def test_parse_rfc3339_date_z_suffix(self):
        assert _parse_rfc3339_date("2026-01-15T00:00:00Z") == date(2026, 1, 15)

    def test_parse_rfc3339_date_none(self):
        assert _parse_rfc3339_date(None) is None

    def test_parse_report_date_compact(self):
        assert _parse_report_date("20260115") == date(2026, 1, 15)

    def test_parse_report_date_iso(self):
        assert _parse_report_date("2026-01-15") == date(2026, 1, 15)

    def test_parse_report_date_none(self):
        assert _parse_report_date(None) is None


# ===========================================================================
# _iter_report_rows
# ===========================================================================

class TestIterReportRows:
    @patch("src.youtube_reporting.MediaIoBaseDownload")
    def test_catches_non_http_errors(self, mock_downloader_cls):
        """If download/parse raises a generic Exception, yield nothing, don't crash."""
        # MediaIoBaseDownload constructor raises a non-HttpError exception
        mock_downloader_cls.side_effect = Exception("network timeout")

        service = _make_service_stub()
        rows = list(_iter_report_rows(service, "http://example.com/report"))
        assert rows == []

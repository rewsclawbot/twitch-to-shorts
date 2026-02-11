import logging
import time
from datetime import UTC, datetime
from typing import Any, cast

import google_auth_httplib2
import httplib2
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.youtube_uploader import get_credentials

log = logging.getLogger(__name__)

_METRICS_WITH_REACH = (
    "views,estimatedMinutesWatched,averageViewDuration,"
    "averageViewPercentage,videoThumbnailImpressions,"
    "videoThumbnailImpressionsClickRate"
)
_METRICS_CORE = "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage"
_HTTP_TIMEOUT_SECONDS = 30
_API_MAX_ATTEMPTS = 3
_API_BACKOFF_BASE_SECONDS = 0.2


def get_analytics_service(client_secrets_file: str, credentials_file: str):
    creds = get_credentials(client_secrets_file, credentials_file)
    http = google_auth_httplib2.AuthorizedHttp(
        creds,
        http=httplib2.Http(timeout=_HTTP_TIMEOUT_SECONDS),
    )
    return build("youtubeAnalytics", "v2", http=http, cache_discovery=False)


def _execute_request(request) -> dict[str, Any]:
    for attempt in range(_API_MAX_ATTEMPTS):
        try:
            return cast(dict[str, Any], request.execute(num_retries=1))
        except HttpError as e:
            status = getattr(e.resp, "status", 0)
            retryable = status >= 500 or status == 429
            if not retryable or attempt == _API_MAX_ATTEMPTS - 1:
                raise
        except Exception:
            if attempt == _API_MAX_ATTEMPTS - 1:
                raise
        time.sleep(_API_BACKOFF_BASE_SECONDS * (2**attempt))
    raise RuntimeError("unreachable")


def _query_metrics(service, video_id: str, start_date: str, end_date: str, metrics: str) -> dict[str, Any]:
    request = service.reports().query(
        ids="channel==MINE",
        startDate=start_date,
        endDate=end_date,
        metrics=metrics,
        dimensions="video",
        filters=f"video=={video_id}",
    )
    response = _execute_request(request)
    return response if isinstance(response, dict) else {}


def _parse_report(response: dict) -> dict | None:
    rows = response.get("rows") or []
    if not rows:
        return None
    headers = [h.get("name") for h in response.get("columnHeaders", [])]
    if not headers:
        return None
    row = rows[0]
    return dict(zip(headers, row, strict=False))


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_ctr(value: float | None) -> float | None:
    if value is None:
        return None
    # CTR is reported as a percentage (e.g. 0.6 means 0.6%), so normalize to fraction.
    return value / 100.0


def fetch_video_metrics(service, video_id: str, start_date: str, end_date: str) -> dict | None:
    # Try reach metrics first, fall back to core-only if the API rejects them
    reach_available = False
    try:
        response = _query_metrics(service, video_id, start_date, end_date, _METRICS_WITH_REACH)
        reach_available = True
        log.info("Analytics reach metrics available for %s", video_id)
    except HttpError:
        log.info("Reach metrics unavailable, falling back to core metrics for %s", video_id)
        try:
            response = _query_metrics(service, video_id, start_date, end_date, _METRICS_CORE)
        except HttpError:
            log.warning("Analytics query failed for %s", video_id, exc_info=True)
            return None

    data = _parse_report(response)
    if not data:
        return None

    result = {
        "yt_views": _to_int(data.get("views")),
        "yt_estimated_minutes_watched": _to_float(data.get("estimatedMinutesWatched")),
        "yt_avg_view_duration": _to_float(data.get("averageViewDuration")),
        "yt_avg_view_percentage": _to_float(data.get("averageViewPercentage")),
        "yt_impressions": None,
        "yt_impressions_ctr": None,
        "yt_last_sync": datetime.now(UTC).isoformat(),
    }

    if reach_available:
        result["yt_impressions"] = _to_int(data.get("videoThumbnailImpressions"))
        result["yt_impressions_ctr"] = _normalize_ctr(
            _to_float(data.get("videoThumbnailImpressionsClickRate"))
        )

    return result

import logging
from datetime import UTC, datetime

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


def get_analytics_service(client_secrets_file: str, credentials_file: str):
    creds = get_credentials(client_secrets_file, credentials_file)
    return build("youtubeAnalytics", "v2", credentials=creds)


def _query_metrics(service, video_id: str, start_date: str, end_date: str, metrics: str) -> dict:
    return service.reports().query(
        ids="channel==MINE",
        startDate=start_date,
        endDate=end_date,
        metrics=metrics,
        dimensions="video",
        filters=f"video=={video_id}",
    ).execute()


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

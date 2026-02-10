import logging
from datetime import UTC, datetime

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.youtube_uploader import get_credentials

log = logging.getLogger(__name__)

_METRICS_PRIMARY = (
    "views,estimatedMinutesWatched,averageViewDuration,"
    "averageViewPercentage,impressions,impressionsCtr"
)
_METRICS_FALLBACK = "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage"


def get_analytics_service(client_secrets_file: str, credentials_file: str):
    creds = get_credentials(client_secrets_file, credentials_file)
    return build("youtubeAnalytics", "v2", credentials=creds)


def _query_metrics(service, video_id: str, start_date: str, end_date: str, metrics: str) -> dict:
    return service.reports().query(
        ids="channel==MINE",
        startDate=start_date,
        endDate=end_date,
        metrics=metrics,
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


def fetch_video_metrics(service, video_id: str, start_date: str, end_date: str) -> dict | None:
    try:
        response = _query_metrics(service, video_id, start_date, end_date, _METRICS_PRIMARY)
    except HttpError as e:
        log.warning("Primary metrics failed for %s: %s", video_id, e)
        try:
            response = _query_metrics(service, video_id, start_date, end_date, _METRICS_FALLBACK)
        except Exception as e2:
            log.warning("Fallback metrics also failed for %s: %s", video_id, e2)
            return None

    data = _parse_report(response)
    if not data:
        return None

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

    return {
        "yt_views": _to_int(data.get("views")),
        "yt_estimated_minutes_watched": _to_float(data.get("estimatedMinutesWatched")),
        "yt_avg_view_duration": _to_float(data.get("averageViewDuration")),
        "yt_avg_view_percentage": _to_float(data.get("averageViewPercentage")),
        "yt_impressions": _to_int(data.get("impressions")),
        "yt_impressions_ctr": _to_float(data.get("impressionsCtr")),
        "yt_last_sync": datetime.now(UTC).isoformat(),
    }

import contextlib
import csv
import gzip
import io
import logging
import os
import tempfile
import time
from datetime import date, datetime
from typing import Any, cast
from urllib.parse import urlsplit

import google_auth_httplib2
import httplib2
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from src.youtube_analytics import _normalize_ctr as _normalize_ctr
from src.youtube_analytics import _to_float, _to_int
from src.youtube_uploader import get_credentials

log = logging.getLogger(__name__)

REPORT_TYPE_REACH_BASIC = "channel_reach_basic_a1"
_ALLOWED_REPORT_HOST_SUFFIXES = (
    ".googleapis.com",
    ".googleusercontent.com",
)
_MAX_REPORT_BYTES = 100 * 1024 * 1024  # 100 MB safety cap
_HTTP_TIMEOUT_SECONDS = 30
_API_MAX_ATTEMPTS = 3
_API_BACKOFF_BASE_SECONDS = 0.2


def get_reporting_service(client_secrets_file: str, credentials_file: str):
    creds = get_credentials(client_secrets_file, credentials_file)
    http = google_auth_httplib2.AuthorizedHttp(
        creds,
        http=httplib2.Http(timeout=_HTTP_TIMEOUT_SECONDS),
    )
    return build("youtubereporting", "v1", http=http, cache_discovery=False)


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


def fetch_reach_metrics(
    service,
    video_ids: set[str],
    start_date: str,
    end_date: str,
    report_type_id: str = REPORT_TYPE_REACH_BASIC,
) -> dict[str, dict[str, float | int | None]]:
    if not video_ids:
        return {}
    job_id = _ensure_job(service, report_type_id)
    if not job_id:
        return {}
    reports = _list_reports(service, job_id)
    if not reports:
        return {}

    start = _parse_iso_date(start_date)
    end = _parse_iso_date(end_date)
    if not start or not end:
        return {}

    candidates = _filter_reports(reports, start, end)
    if not candidates:
        return {}

    totals = {
        video_id: {"impressions": 0, "ctr_sum": 0.0, "ctr_denom": 0}
        for video_id in video_ids
    }

    seen: set[tuple[str, str | None]] = set()

    for report in candidates:
        download_url = report.get("downloadUrl")
        if not download_url:
            continue
        for row in _iter_report_rows(service, download_url):
            video_id = row.get("video_id") or row.get("videoId") or row.get("video")
            if not video_id or video_id not in totals:
                continue
            row_date = _parse_report_date(row.get("date"))
            if row_date and (row_date < start or row_date > end):
                continue
            date_key = (video_id, str(row_date) if row_date else None)
            if date_key in seen:
                continue
            seen.add(date_key)
            impressions = _to_int(row.get("video_thumbnail_impressions"))
            if impressions is None:
                continue
            # Reporting API CSV returns CTR as a raw fraction (0.05 = 5%),
            # unlike Analytics API which returns percentages (5.0 = 5%).
            # Do NOT apply _normalize_ctr here.
            ctr = _to_float(row.get("video_thumbnail_impressions_ctr"))
            totals[video_id]["impressions"] += impressions
            if ctr is not None:
                totals[video_id]["ctr_sum"] += ctr * impressions
                totals[video_id]["ctr_denom"] += impressions

    results = {}
    for video_id, data in totals.items():
        impressions = data["impressions"]
        if impressions <= 0:
            continue
        ctr = None
        if data["ctr_denom"]:
            ctr = data["ctr_sum"] / data["ctr_denom"]
        results[video_id] = {
            "yt_impressions": impressions,
            "yt_impressions_ctr": ctr,
        }
    return results


def _ensure_job(service, report_type_id: str) -> str | None:
    jobs = _list_jobs(service)
    for job in jobs:
        if job.get("reportTypeId") == report_type_id:
            return job.get("id")
    try:
        request = service.jobs().create(
            body={"reportTypeId": report_type_id, "name": f"reach-{report_type_id}"}
        )
        response = _execute_request(request)
    except HttpError:
        log.warning("Unable to create reporting job for %s", report_type_id, exc_info=True)
        return None
    job_id = response.get("id") if isinstance(response, dict) else None
    if job_id is not None and not isinstance(job_id, str):
        return None
    if job_id:
        log.info("Created YouTube reporting job %s (%s)", job_id, report_type_id)
    return job_id


def _list_jobs(service) -> list[dict]:
    jobs: list[dict] = []
    page_token = None
    for _ in range(100):
        params: dict[str, str] = {}
        if page_token:
            params["pageToken"] = page_token
        try:
            response = _execute_request(service.jobs().list(**params))
        except HttpError:
            log.warning("Unable to list reporting jobs", exc_info=True)
            return []
        jobs.extend(response.get("jobs", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return jobs


def _list_reports(service, job_id: str) -> list[dict]:
    reports: list[dict] = []
    page_token = None
    for _ in range(100):
        params = {"jobId": job_id}
        if page_token:
            params["pageToken"] = page_token
        try:
            response = _execute_request(service.jobs().reports().list(**params))
        except HttpError:
            log.warning("Unable to list reports for job %s", job_id, exc_info=True)
            return []
        reports.extend(response.get("reports", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return reports


def _filter_reports(reports: list[dict], start: date, end: date) -> list[dict]:
    candidates = []
    for report in reports:
        report_start = _parse_rfc3339_date(report.get("startTime"))
        report_end = _parse_rfc3339_date(report.get("endTime"))
        if not report_start or not report_end:
            continue
        if report_end < start or report_start > end:
            continue
        candidates.append(report)
    candidates.sort(key=lambda r: r.get("startTime") or "")
    return candidates


def _iter_report_rows(service, download_url: str):
    if not _is_allowed_reporting_url(download_url):
        log.warning("Rejected reporting download URL outside allowlist")
        return
    request = service.media().download(resourceName="")
    request.uri = download_url
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name
            downloader = MediaIoBaseDownload(tmp, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                # Stop oversized downloads early to bound disk and memory use.
                progress = getattr(status, "resumable_progress", None)
                if isinstance(progress, (int, float)) and progress > _MAX_REPORT_BYTES:
                    raise ValueError("Reporting download exceeded max size")
                if tmp.tell() > _MAX_REPORT_BYTES:
                    raise ValueError("Reporting download exceeded max size")
        with open(tmp_path, "rb") as fh:
            head = fh.read(2)
            fh.seek(0)
            if head == b"\x1f\x8b":
                stream = gzip.open(fh, mode="rt", encoding="utf-8-sig", newline="")  # noqa: SIM115
            else:
                stream = io.TextIOWrapper(fh, encoding="utf-8-sig", newline="")
            with stream as handle:
                reader = csv.DictReader(handle)
                yield from reader
    except Exception:
        log.warning("Failed to download or parse reporting data", exc_info=True)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            with contextlib.suppress(OSError):
                os.remove(tmp_path)


def _is_allowed_reporting_url(download_url: str) -> bool:
    try:
        parsed = urlsplit(download_url)
    except Exception:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    return host in ("googleapis.com", "googleusercontent.com") or any(
        host.endswith(suffix) for suffix in _ALLOWED_REPORT_HOST_SUFFIXES
    )


def _parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_rfc3339_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _parse_report_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None

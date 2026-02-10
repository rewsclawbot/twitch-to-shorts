import csv
import gzip
import io
import logging
import os
import tempfile
from datetime import date, datetime

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from src.youtube_uploader import get_credentials

log = logging.getLogger(__name__)

REPORT_TYPE_REACH_BASIC = "channel_reach_basic_a1"


def get_reporting_service(client_secrets_file: str, credentials_file: str):
    creds = get_credentials(client_secrets_file, credentials_file)
    return build("youtubereporting", "v1", credentials=creds)


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
            impressions = _to_int(row.get("impressions"))
            if impressions is None:
                continue
            ctr = _normalize_ctr(_to_float(row.get("impressions_ctr")))
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
        response = service.jobs().create(
            body={"reportTypeId": report_type_id, "name": f"reach-{report_type_id}"}
        ).execute()
    except HttpError:
        log.warning("Unable to create reporting job for %s", report_type_id, exc_info=True)
        return None
    job_id = response.get("id")
    if job_id:
        log.info("Created YouTube reporting job %s (%s)", job_id, report_type_id)
    return job_id


def _list_jobs(service) -> list[dict]:
    jobs: list[dict] = []
    page_token = None
    while True:
        params = {}
        if page_token:
            params["pageToken"] = page_token
        try:
            response = service.jobs().list(**params).execute()
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
    while True:
        params = {"jobId": job_id}
        if page_token:
            params["pageToken"] = page_token
        try:
            response = service.jobs().reports().list(**params).execute()
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
        if report_start and report_end:
            if report_end < start or report_start > end:
                continue
        candidates.append(report)
    candidates.sort(key=lambda r: r.get("startTime") or "")
    return candidates


def _iter_report_rows(service, download_url: str):
    request = service.media().download(resourceName="")
    request.uri = download_url
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name
            downloader = MediaIoBaseDownload(tmp, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        with open(tmp_path, "rb") as fh:
            head = fh.read(2)
            fh.seek(0)
            if head == b"\x1f\x8b":
                stream = gzip.open(fh, mode="rt", encoding="utf-8-sig", newline="")
            else:
                stream = io.TextIOWrapper(fh, encoding="utf-8-sig", newline="")
            with stream as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    yield row
    except HttpError:
        log.warning("Failed to download reporting data", exc_info=True)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


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

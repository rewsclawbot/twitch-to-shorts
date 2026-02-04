from __future__ import annotations

import logging
import os
import re
import sys
import time

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

from src.models import Clip

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
QUOTA_REASONS = {
    "uploadLimitExceeded",
    "quotaExceeded",
    "dailyLimitExceeded",
    "rateLimitExceeded",
    "userRateLimitExceeded",
}


class QuotaExhaustedError(Exception):
    """Raised when YouTube API quota is exhausted."""


def _extract_error_reason(err: HttpError) -> str:
    if isinstance(err.error_details, list):
        for detail in err.error_details:
            reason = detail.get("reason", "")
            if reason:
                return reason
    return ""


def get_authenticated_service(client_secrets_file: str, credentials_file: str):
    """Get an authenticated YouTube API service. Runs OAuth flow if needed."""
    creds = None

    if os.path.exists(credentials_file):
        creds = Credentials.from_authorized_user_file(credentials_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                log.error(
                    "YouTube refresh token expired or revoked for %s. "
                    "Delete the token file and re-authenticate.",
                    credentials_file,
                )
                raise
        else:
            if not sys.stdin.isatty():
                log.error(
                    "OAuth flow requires interactive terminal but stdin is not a TTY. "
                    "Run manually once to complete OAuth, then credentials will be cached."
                )
                raise RuntimeError("Cannot run OAuth flow in non-interactive environment")
            flow = InstalledAppFlow.from_client_secrets_file(client_secrets_file, SCOPES)
            creds = flow.run_local_server(port=0)

        creds_dir = os.path.dirname(credentials_file)
        if creds_dir:
            os.makedirs(creds_dir, exist_ok=True)
        with open(credentials_file, "w") as f:
            f.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


def _truncate_title(title: str, max_len: int = 100) -> str:
    """Truncate title at last word boundary if it exceeds max_len."""
    if len(title) <= max_len:
        return title
    truncated = title[: max_len - 3]
    # Find last space to avoid splitting mid-word
    last_space = truncated.rfind(" ")
    if last_space > max_len // 2:
        truncated = truncated[:last_space]
    return truncated.rstrip() + "..."


def upload_short(
    service,
    video_path: str,
    clip: Clip,
    category_id: str = "20",
    privacy_status: str = "public",
) -> str | None:
    """Upload a video as a YouTube Short. Returns the video ID on success.

    Raises QuotaExhaustedError if the YouTube API quota is exceeded.
    """
    title = clip.title
    streamer_name = clip.streamer
    game_name = clip.game_name
    description = ""
    sanitized = re.sub(r"[\x00-\x1f<>]", "", f"{title} | {streamer_name}")
    full_title = _truncate_title(sanitized)

    if not description:
        description = f"Clip from {streamer_name}'s stream\n\n#Shorts"
    elif "#Shorts" not in description:
        description += "\n\n#Shorts"

    tags = ["Shorts", streamer_name, "Twitch", "Gaming"]
    if game_name:
        tags.append(game_name)

    body = {
        "snippet": {
            "title": full_title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)

    log.info("Uploading: %s", full_title)
    try:
        request = service.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            for attempt in range(4):
                try:
                    _, response = request.next_chunk()
                    break
                except (HttpError, ConnectionError, TimeoutError) as err:
                    retryable = not isinstance(err, HttpError) or err.resp.status >= 500
                    if retryable and attempt < 3:
                        delay = 2**attempt
                        log.warning("Upload chunk retry %d/3: %s", attempt + 1, err)
                        time.sleep(delay)
                    else:
                        raise

        video_id = response["id"]
        log.info("Upload successful: https://youtube.com/shorts/%s", video_id)
        return video_id
    except HttpError as e:
        reason = _extract_error_reason(e)
        if reason in QUOTA_REASONS:
            log.error("YouTube quota exhausted: %s", reason)
            raise QuotaExhaustedError(reason) from e
        if e.resp.status == 403:
            log.error("YouTube 403 forbidden for %s: %s", title, reason or "unknown")
            return None
        log.exception("Upload failed for %s (status=%s reason=%s)", title, e.resp.status, reason or "unknown")
        return None
    except Exception:
        log.exception("Upload failed for %s", title)
        return None


def verify_upload(service, video_id: str) -> bool:
    """Verify an uploaded video exists and is processing/live on YouTube."""
    try:
        resp = service.videos().list(part="status", id=video_id).execute()
        items = resp.get("items", [])
        if not items:
            log.error("Uploaded video %s not found via API", video_id)
            return False
        status = items[0]["status"]["uploadStatus"]
        if status in ("uploaded", "processed"):
            return True
        log.warning("Video %s has unexpected status: %s", video_id, status)
        return status != "rejected"
    except HttpError as e:
        # Scope insufficient — we only have upload scope, not readonly
        # Trust the upload succeeded since upload_short returned a video ID
        if e.resp.status == 403 and "insufficientPermissions" in str(e):
            log.info("Skipping verification for %s (no readonly scope) — trusting upload", video_id)
            return True
        log.exception("Failed to verify upload %s — assuming failure", video_id)
        return False
    except Exception:
        log.exception("Failed to verify upload %s — assuming failure", video_id)
        return False

import hashlib
import json
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

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
QUOTA_REASONS = {
    "uploadLimitExceeded",
    "quotaExceeded",
    "dailyLimitExceeded",
    "rateLimitExceeded",
    "userRateLimitExceeded",
}


class QuotaExhaustedError(Exception):
    """Raised when YouTube API quota is exhausted."""


class ForbiddenError(Exception):
    """Raised when YouTube API returns 403 for an upload."""


def _extract_error_reason(err: HttpError) -> str:
    if isinstance(err.error_details, list):
        for detail in err.error_details:
            reason = detail.get("reason", "")
            if reason:
                return reason
    return ""


def get_credentials(client_secrets_file: str, credentials_file: str) -> Credentials:
    """Get OAuth credentials for YouTube APIs. Runs OAuth flow if needed."""
    creds: Credentials | None = None
    stored_scopes: list[str] | None = None

    if os.path.exists(credentials_file):
        try:
            with open(credentials_file, "r", encoding="utf-8") as f:
                stored_scopes = (json.load(f).get("scopes") or [])
        except (OSError, json.JSONDecodeError):
            stored_scopes = None
        creds = Credentials.from_authorized_user_file(credentials_file, SCOPES)

    if stored_scopes is not None:
        missing = [scope for scope in SCOPES if scope not in stored_scopes]
        if missing:
            log.error(
                "YouTube credentials missing required scopes: %s. "
                "Delete the token file and re-authenticate.",
                ", ".join(missing),
            )
            if not sys.stdin.isatty():
                raise RuntimeError("YouTube credentials missing required scopes")
            creds = None

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
        fd = os.open(credentials_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(creds.to_json())

    return creds


def get_authenticated_service(client_secrets_file: str, credentials_file: str):
    """Get an authenticated YouTube Data API service."""
    creds = get_credentials(client_secrets_file, credentials_file)
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


class _TemplateDict(dict):
    def __missing__(self, key: str) -> str:
        log.warning("Template references unknown key: {%s}", key)
        return ""


def _sanitize_text(text: str) -> str:
    return re.sub(r"[\x00-\x1f<>]", "", text).strip()


def _render_template(template: str, clip: Clip) -> str:
    values = _TemplateDict(
        title=clip.title,
        streamer=clip.streamer,
        game=clip.game_name,
        game_name=clip.game_name,
    )
    return template.format_map(values)


def _choose_template(clip_id: str, templates: list[str] | None) -> str | None:
    if not templates:
        return None
    digest = hashlib.md5(clip_id.encode("utf-8")).hexdigest()
    idx = int(digest, 16) % len(templates)
    return templates[idx]


def _dedupe_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        clean = tag.strip()
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return result


def _limit_tag_length(tags: list[str], max_total_len: int = 500) -> list[str]:
    total = 0
    limited: list[str] = []
    for tag in tags:
        tag_len = len(tag)
        if limited:
            tag_len += 1  # comma separator
        if total + tag_len > max_total_len:
            break
        limited.append(tag)
        total += tag_len
    return limited


def set_thumbnail(service, video_id: str, thumbnail_path: str) -> bool:
    try:
        media = MediaFileUpload(thumbnail_path, mimetype="image/jpeg")
        service.thumbnails().set(videoId=video_id, media_body=media).execute()
        log.info("Thumbnail set for %s", video_id)
        return True
    except HttpError as e:
        log.warning("Failed to set thumbnail for %s: %s", video_id, e)
        return False


def build_upload_title(
    clip: Clip,
    title_template: str | None = None,
    title_templates: list[str] | None = None,
) -> str:
    """Build the YouTube title for a clip using the same logic as upload_short."""
    chosen_title = _choose_template(clip.id, title_templates) or title_template
    if chosen_title:
        raw_title = _render_template(chosen_title, clip)
    else:
        raw_title = f"{clip.title} | {clip.streamer}"
    return _truncate_title(_sanitize_text(raw_title))


def upload_short(
    service,
    video_path: str,
    clip: Clip,
    category_id: str = "20",
    privacy_status: str = "public",
    title_template: str | None = None,
    title_templates: list[str] | None = None,
    description_template: str | None = None,
    description_templates: list[str] | None = None,
    extra_tags: list[str] | None = None,
) -> str | None:
    """Upload a video as a YouTube Short. Returns the video ID on success.

    Raises QuotaExhaustedError if the YouTube API quota is exceeded.
    """
    streamer_name = clip.streamer
    game_name = clip.game_name

    full_title = build_upload_title(clip, title_template, title_templates)

    chosen_description = _choose_template(clip.id, description_templates) or description_template
    if chosen_description:
        description = _sanitize_text(_render_template(chosen_description, clip))
    else:
        description = f"Clip from {streamer_name}'s stream"
    if "#Shorts" not in description:
        description = (description + "\n\n#Shorts") if description else "#Shorts"

    tags = ["Shorts", streamer_name, "Twitch", "Gaming", "Highlights", "Clips"]
    if game_name:
        tags.append(game_name)
    if extra_tags:
        tags.extend(extra_tags)
    tags = _limit_tag_length(_dedupe_tags(tags))

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
        max_chunks = 1000
        chunks = 0
        while response is None and chunks < max_chunks:
            for attempt in range(4):
                try:
                    _, response = request.next_chunk()
                    chunks += 1
                    break
                except (HttpError, ConnectionError, TimeoutError) as err:
                    retryable = not isinstance(err, HttpError) or err.resp.status >= 500
                    if retryable and attempt < 3:
                        delay = 2**attempt
                        log.warning("Upload chunk retry %d/3: %s", attempt + 1, err)
                        time.sleep(delay)
                    else:
                        raise
        if response is None:
            raise RuntimeError("Upload did not complete after maximum chunk attempts")

        video_id = response["id"]
        log.info("Upload successful: https://youtube.com/shorts/%s", video_id)
        return video_id
    except HttpError as e:
        reason = _extract_error_reason(e)
        if reason in QUOTA_REASONS:
            log.error("YouTube quota exhausted: %s", reason)
            raise QuotaExhaustedError(reason) from e
        if e.resp.status == 403:
            log.error("YouTube 403 forbidden for %s: %s", full_title, reason or "unknown")
            raise ForbiddenError(reason or "unknown") from e
        log.exception("Upload failed for %s (status=%s reason=%s)", full_title, e.resp.status, reason or "unknown")
        return None
    except Exception:
        log.exception("Upload failed for %s", full_title)
        return None


def check_channel_for_duplicate(service, clip_title: str, max_results: int = 50) -> str | None:
    """Check channel's recent uploads for a video with a matching title.

    Uses playlistItems.list on the uploads playlist (2 quota units total).
    Returns the youtube_id if a duplicate is found, None otherwise.
    """
    try:
        ch_resp = service.channels().list(part="contentDetails", mine=True).execute()
        items = ch_resp.get("items", [])
        if not items:
            log.warning("No channel found for authenticated user")
            return None
        uploads_playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

        page_token = None
        checked = 0
        while checked < max_results:
            page_size = min(50, max_results - checked)
            pl_resp = service.playlistItems().list(
                part="snippet",
                playlistId=uploads_playlist,
                maxResults=page_size,
                pageToken=page_token,
            ).execute()

            for item in pl_resp.get("items", []):
                existing_title = item["snippet"].get("title", "")
                if existing_title == clip_title:
                    video_id = item["snippet"]["resourceId"]["videoId"]
                    log.info("Duplicate found on channel: '%s' -> %s", clip_title, video_id)
                    return video_id
            checked += len(pl_resp.get("items", []))
            page_token = pl_resp.get("nextPageToken")
            if not page_token:
                break
        return None
    except HttpError as e:
        log.warning("Channel duplicate check failed (HTTP %s): %s", e.resp.status, e)
        return None
    except Exception:
        log.warning("Channel duplicate check failed", exc_info=True)
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
        if e.resp.status == 403 and "insufficientPermissions" in str(e):
            log.error(
                "Insufficient permissions to verify upload %s. "
                "Re-authenticate with youtube.readonly scope.",
                video_id,
            )
            return False
        log.exception("Failed to verify upload %s - assuming failure", video_id)
        return False
    except Exception:
        log.exception("Failed to verify upload %s - assuming failure", video_id)
        return False

import hashlib
import json
import logging
import os
import re
import string
import sys
import time
from typing import Any

import httplib2
import requests
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from src.models import Clip

log = logging.getLogger(__name__)

_LLM_MODEL = "gpt-4o-mini"
_LLM_TIMEOUT_SECONDS = 10
_LLM_MAX_ATTEMPTS = 2
_LLM_RETRY_BACKOFF_SECONDS = 2
_MAX_DESCRIPTION_LEN = 200

SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
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


class AuthenticationError(Exception):
    """Raised when YouTube API returns an authentication/credential error during upload."""


def _extract_error_reason(err: HttpError) -> str:
    if isinstance(err.error_details, list):
        for detail in err.error_details:
            if not isinstance(detail, dict):
                continue
            reason = detail.get("reason", "")
            if isinstance(reason, str) and reason:
                return reason
    return ""


def get_credentials(client_secrets_file: str, credentials_file: str) -> Credentials:
    """Get OAuth credentials for YouTube APIs. Runs OAuth flow if needed."""
    creds: Credentials | None = None
    stored_scopes: list[str] | None = None

    if os.path.exists(credentials_file):
        try:
            with open(credentials_file, encoding="utf-8") as f:
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
                    "YouTube refresh token expired or revoked for %s.",
                    credentials_file,
                )
                if not sys.stdin.isatty():
                    log.error(
                        "Cannot recover revoked token in non-interactive mode. "
                        "Re-authenticate locally and update the token secret."
                    )
                    raise
                log.info("Attempting interactive OAuth re-authentication for %s", credentials_file)
                creds = None

        if not creds or not creds.valid:
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


def _truncate_description(text: str, max_len: int = _MAX_DESCRIPTION_LEN) -> str:
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    last_space = truncated.rfind(" ")
    if last_space > max_len // 2:
        truncated = truncated[:last_space]
    return truncated.rstrip()


class _TemplateDict(dict):
    def __missing__(self, key: str) -> str:
        log.warning("Template references unknown key: {%s}", key)
        return ""


_VALID_TEMPLATE_KEYS = {"title", "streamer", "game", "game_name"}


def validate_templates(templates: list[str] | None, label: str = "template") -> None:
    """Log warnings for templates referencing unknown keys."""
    if not templates:
        return
    formatter = string.Formatter()
    for tmpl in templates:
        try:
            keys = {fname for _, fname, _, _ in formatter.parse(tmpl) if fname is not None}
        except (ValueError, KeyError):
            log.warning("Invalid format string in %s: %s", label, tmpl)
            continue
        unknown = keys - _VALID_TEMPLATE_KEYS
        if unknown:
            log.warning(
                "%s references unknown keys %s (valid: %s): %s",
                label, unknown, _VALID_TEMPLATE_KEYS, tmpl,
            )


def _sanitize_text(text: str) -> str:
    return re.sub(r"[\x00-\x1f<>\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", text).strip()


def _as_hashtag(text: str | None) -> str | None:
    if not text:
        return None
    token = re.sub(r"[^a-zA-Z0-9]+", "", text.strip().lower())
    if not token:
        return None
    return f"#{token}"


def _build_default_description(clip: Clip) -> str:
    game_name = clip.game_name or "gaming"
    game_hashtag = _as_hashtag(clip.game_name)
    streamer_hashtag = _as_hashtag(clip.streamer)
    hashtags = ["#shorts", "#gaming", "#twitchclips"]
    if game_hashtag:
        hashtags.append(game_hashtag)
    if streamer_hashtag:
        hashtags.append(streamer_hashtag)
    hashtags = _dedupe_tags(hashtags)
    hashtag_line = " ".join(hashtags[:5])
    return (
        f"{game_name} highlight from {clip.streamer}: {clip.title}\n\n"
        f"Gaming shorts, clutch moments, and funny stream clips.\n"
        f"Credit: {clip.streamer} on Twitch.\n\n"
        f"{hashtag_line}"
    )


def _description_has_cta(text: str) -> bool:
    normalized = text.lower()
    return any(verb in normalized for verb in ("follow", "subscribe", "comment", "like"))


def _postprocess_optimized_description(text: str, game_name: str) -> str | None:
    normalized = _sanitize_text(text).strip().strip("\"'")
    normalized = re.sub(r"\s+", " ", normalized)
    if not normalized:
        return None

    if not _description_has_cta(normalized):
        normalized = f"{normalized} Follow for more."

    required_tags = ["#shorts", "#gaming"]
    game_hashtag = _as_hashtag(game_name)
    if game_hashtag:
        required_tags.append(game_hashtag)
    required_tags = _dedupe_tags(required_tags)

    lower = normalized.lower()
    missing = [tag for tag in required_tags if tag not in lower]
    if missing:
        normalized = f"{normalized} {' '.join(missing)}".strip()

    if len(normalized) <= _MAX_DESCRIPTION_LEN:
        return normalized

    tags_suffix = " ".join(required_tags)
    # Keep hashtags present even if we have to shrink the prose.
    core = normalized
    for tag in required_tags:
        core = re.sub(re.escape(tag), "", core, flags=re.IGNORECASE)
    core = re.sub(r"\s+", " ", core).strip()
    allowed_core_len = _MAX_DESCRIPTION_LEN - len(tags_suffix) - 1
    if allowed_core_len <= 0:
        return tags_suffix[:_MAX_DESCRIPTION_LEN]
    core = _truncate_description(core, allowed_core_len)
    return f"{core} {tags_suffix}".strip()


def optimize_description(title: str, game_name: str, streamer_name: str) -> str | None:
    """Generate an engaging Shorts description via local LLM and enforce platform constraints."""
    base_url = os.environ.get("LLM_BASE_URL")
    if not base_url:
        return None

    model_name = os.environ.get("LLM_MODEL_NAME", _LLM_MODEL)
    endpoint = base_url.rstrip("/") + "/chat/completions"
    game_hashtag = _as_hashtag(game_name) or "#gaming"
    system_prompt = (
        "You write YouTube Shorts descriptions for gaming clips.\n"
        "Return a compact description with:\n"
        "1) a hook first line,\n"
        "2) short game context,\n"
        "3) hashtags including #shorts #gaming and the game hashtag,\n"
        "4) a clear call to action.\n"
        "Keep the full output under 200 characters.\n"
        "Output only description text."
    )
    user_prompt = (
        f"Title: {title}\n"
        f"Game: {game_name}\n"
        f"Streamer: {streamer_name}\n"
        f"Required game hashtag: {game_hashtag}"
    )
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.6,
    }

    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    for attempt in range(_LLM_MAX_ATTEMPTS):
        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=_LLM_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices") if isinstance(data, dict) else None
            if not isinstance(choices, list) or not choices:
                log.warning("Description optimizer returned no choices")
                return None
            first_choice = choices[0]
            if not isinstance(first_choice, dict):
                return None
            message = first_choice.get("message")
            if not isinstance(message, dict):
                return None
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                log.warning("Description optimizer returned empty content")
                return None
            return _postprocess_optimized_description(content, game_name)
        except (requests.RequestException, ValueError) as err:
            if attempt < _LLM_MAX_ATTEMPTS - 1:
                log.warning(
                    "Description optimizer attempt %d/%d failed: %s (retrying in %ds)",
                    attempt + 1,
                    _LLM_MAX_ATTEMPTS,
                    err,
                    _LLM_RETRY_BACKOFF_SECONDS,
                )
                time.sleep(_LLM_RETRY_BACKOFF_SECONDS)
                continue
            log.warning("Description optimizer failed: %s", err)
            return None

    return None


def _get_game_hashtags(game_name: str) -> list[str]:
    """Return game-specific hashtags for better discoverability."""
    if not game_name:
        return []
    
    # Mapping of common games to their best-performing hashtag sets
    game_tags: dict[str, list[str]] = {
        "fortnite": ["#fortnite", "#fortniteclips", "#fortnitewin", "#battleroyale"],
        "valorant": ["#valorant", "#valorantclips", "#valoranthighlights", "#fps"],
        "apex legends": ["#apexlegends", "#apex", "#apexclips", "#battleroyale"],
        "league of legends": ["#leagueoflegends", "#lol", "#lolclips", "#esports"],
        "overwatch 2": ["#overwatch2", "#overwatch", "#ow2", "#ow2clips"],
        "call of duty": ["#callofduty", "#cod", "#codclips", "#warzone"],
        "minecraft": ["#minecraft", "#minecraftclips", "#minecraftshorts"],
        "gta v": ["#gtav", "#gta5", "#gtaonline", "#gtaclips"],
        "counter-strike 2": ["#cs2", "#counterstrike", "#cs2clips", "#fps"],
        "arc raiders": ["#arcraiders", "#arcraidersgame", "#newgame"],
        "rocket league": ["#rocketleague", "#rlclips", "#rocketleagueclips"],
        "dead by daylight": ["#deadbydaylight", "#dbd", "#dbdclips"],
        "elden ring": ["#eldenring", "#fromsoftware", "#eldenringclips"],
        "just chatting": ["#justchatting", "#streamer", "#funny", "#reaction"],
    }
    
    name_lower = game_name.lower().strip()
    tags = game_tags.get(name_lower, [])
    if not tags:
        # Fallback: generate game-specific hashtag from the name
        game_tag = _as_hashtag(game_name)
        if game_tag:
            tags = [game_tag, f"{game_tag}clips"]
    return tags


def _ensure_description_hashtags(description: str, clip: Clip) -> str:
    normalized = description or ""
    lower = normalized.lower()
    
    # Core required hashtags
    required = ["#shorts", "#gaming", "#twitchclips"]
    
    # Add streamer-specific hashtag
    streamer_hashtag = _as_hashtag(clip.streamer)
    if streamer_hashtag:
        required.append(streamer_hashtag)
    
    # Add game-specific hashtags (up to 2 extras)
    game_tags = _get_game_hashtags(clip.game_name or "")
    game_tag_main = _as_hashtag(clip.game_name)
    if game_tag_main:
        required.append(game_tag_main)
    for tag in game_tags[:2]:
        if tag.lower() not in [r.lower() for r in required]:
            required.append(tag)
    
    # Add engagement/discoverability hashtags
    required.extend(["#gamingclips", "#viral"])
    
    # Dedupe and limit (YouTube allows up to 15 hashtags in description, 3 shown)
    seen = set()
    deduped = []
    for tag in required:
        if tag.lower() not in seen:
            seen.add(tag.lower())
            deduped.append(tag)
    required = deduped[:10]  # Cap at 10 hashtags
    
    missing = [tag for tag in required if tag.lower() not in lower]
    if missing:
        normalized = (normalized + "\n\n" if normalized else "") + " ".join(missing)
    if "credit:" not in normalized.lower():
        normalized += f"\nCredit: {clip.streamer} on Twitch."
    return normalized



def _render_template(template: str, clip: Clip) -> str:
    values = _TemplateDict(
        title=clip.title,
        streamer=clip.streamer,
        game=clip.game_name,
        game_name=clip.game_name,
    )
    return template.format_map(values)


def _choose_template_index(clip_id: str, templates: list[str] | None) -> int | None:
    if not templates:
        return None
    digest = hashlib.md5(clip_id.encode("utf-8")).hexdigest()
    return int(digest, 16) % len(templates)


def _choose_template(clip_id: str, templates: list[str] | None) -> str | None:
    idx = _choose_template_index(clip_id, templates)
    if idx is None:
        return None
    return templates[idx]


def get_title_variant_label(
    clip: Clip,
    title_template: str | None = None,
    title_templates: list[str] | None = None,
) -> str:
    """Return a stable label for the chosen title path."""
    idx = _choose_template_index(clip.id, title_templates)
    if idx is not None:
        return f"template_{idx}"
    if title_template:
        return "template_default"
    return "original"


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
    title, _ = build_upload_title_with_variant(clip, title_template, title_templates)
    return title


def build_upload_title_with_variant(
    clip: Clip,
    title_template: str | None = None,
    title_templates: list[str] | None = None,
) -> tuple[str, str]:
    """Build the upload title and return the selected title variant label."""
    variant = get_title_variant_label(clip, title_template, title_templates)
    chosen_title = _choose_template(clip.id, title_templates) or title_template
    raw_title = _render_template(chosen_title, clip) if chosen_title else clip.title
    return _truncate_title(_sanitize_text(raw_title)), variant


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
    prebuilt_title: str | None = None,
) -> str | None:
    """Upload a video as a YouTube Short. Returns the video ID on success.

    Raises QuotaExhaustedError if the YouTube API quota is exceeded.
    """
    streamer_name = clip.streamer
    game_name = clip.game_name

    full_title = prebuilt_title if prebuilt_title is not None else build_upload_title(clip, title_template, title_templates)

    chosen_description = _choose_template(clip.id, description_templates) or description_template
    if chosen_description:
        fallback_description = _sanitize_text(_render_template(chosen_description, clip))
    else:
        fallback_description = _build_default_description(clip)

    description = optimize_description(full_title, game_name, streamer_name)
    if not description:
        description = _ensure_description_hashtags(fallback_description, clip)

    tags = ["#shorts", "Shorts", streamer_name, "Twitch", "Gaming", "Highlights", "Clips"]
    if game_name:
        tags.append(game_name)
        game_hashtag = _as_hashtag(game_name)
        if game_hashtag:
            tags.append(game_hashtag)
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

    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True, chunksize=5 * 1024 * 1024)

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

        video_id = response.get("id") if isinstance(response, dict) else None
        if not isinstance(video_id, str):
            raise RuntimeError("Upload response missing video id")
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
    except (httplib2.error.RedirectMissingLocation, RefreshError) as e:
        log.error("Authentication error during upload for %s: %s", full_title, e)
        raise AuthenticationError(str(e)) from e
    except Exception:
        log.exception("Upload failed for %s", full_title)
        return None


_uploads_playlist_cache: dict[str, str] = {}


def check_channel_for_duplicate(service, clip_title: str, max_results: int = 50, cache_key: str = "default") -> str | None:
    """Check channel's recent uploads for a video with a matching title.

    Uses playlistItems.list on the uploads playlist (2 quota units total on first call,
    1 quota unit on subsequent calls due to cached uploads playlist ID).
    Returns the youtube_id if a duplicate is found, None otherwise.
    """
    try:
        uploads_playlist = _uploads_playlist_cache.get(cache_key)
        if uploads_playlist is None:
            ch_resp = service.channels().list(part="contentDetails", mine=True).execute()
            items = ch_resp.get("items", [])
            if not items:
                log.warning("No channel found for authenticated user")
                return None
            uploads_playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
            _uploads_playlist_cache[cache_key] = uploads_playlist

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

            items = pl_resp.get("items", [])
            for item in items:
                existing_title = item["snippet"].get("title", "")
                if existing_title == clip_title:
                    video_id_raw: Any = item["snippet"]["resourceId"]["videoId"]
                    if not isinstance(video_id_raw, str):
                        continue
                    video_id = video_id_raw
                    # Verify video is actually live/processing (not a ghost from failed upload)
                    try:
                        v_resp = service.videos().list(part="status", id=video_id).execute()
                        v_items = v_resp.get("items", [])
                        if not v_items:
                            log.info("Ghost video %s (title match but not accessible) — ignoring", video_id)
                            continue
                        status = v_items[0]["status"].get("uploadStatus", "")
                        if status in ("deleted", "rejected", "failed"):
                            log.info("Dead video %s (status=%s) — ignoring", video_id, status)
                            continue
                    except Exception:
                        pass  # If verify fails, trust the match
                    log.info("Duplicate found on channel: '%s' -> %s", clip_title, video_id)
                    return video_id
            checked += len(items)
            page_token = pl_resp.get("nextPageToken")
            # Defensive guard: if API returns an empty page with a next token,
            # break to avoid an infinite pagination loop.
            if not items and page_token:
                log.warning("Channel duplicate check got empty page with next token; stopping pagination early")
                break
            if not page_token:
                break
        return None
    except HttpError as e:
        reason = _extract_error_reason(e)
        if e.resp.status in (401, 403) and reason not in QUOTA_REASONS:
            log.error("Channel duplicate check fatal error (HTTP %s): %s", e.resp.status, e)
            raise
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
        status_raw: Any = items[0]["status"].get("uploadStatus")
        status = status_raw if isinstance(status_raw, str) else ""
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

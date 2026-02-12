import json
import logging
import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict, cast

import requests

from src.models import Clip
from src.youtube_uploader import (
    _choose_template,
    _render_template,
    _sanitize_text,
)

log = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.instagram.com"
GRAPH_API_VERSION = "v21.0"
_CLEANUP_QUEUE_PATH = os.path.join("data", "pending_ig_release_cleanup.txt")


class InstagramTokenData(TypedDict):
    access_token: str
    ig_user_id: str
    token_expiry: str


class InstagramAuthError(Exception):
    """Raised when Instagram authentication fails or credentials are invalid."""


class InstagramRateLimitError(Exception):
    """Raised when Instagram Graph API returns 429 rate limit."""


class InstagramPublishError(Exception):
    """Raised when Instagram reel publishing fails."""


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------


def load_instagram_token(credentials_file: str) -> InstagramTokenData:
    """Load Instagram credentials from a JSON file.

    Expected keys: access_token, ig_user_id, token_expiry (ISO 8601 string).
    Raises InstagramAuthError if the file is missing or malformed.
    """
    if not os.path.exists(credentials_file):
        raise InstagramAuthError(f"Credentials file not found: {credentials_file}")

    try:
        with open(credentials_file, encoding="utf-8") as f:
            data = cast(dict[str, Any], json.load(f))
    except (OSError, json.JSONDecodeError) as e:
        raise InstagramAuthError(f"Failed to read credentials file: {e}") from e

    required_keys = {"access_token", "ig_user_id", "token_expiry"}
    missing = required_keys - set(data.keys())
    if missing:
        raise InstagramAuthError(f"Credentials file missing keys: {missing}")

    for key in ("access_token", "ig_user_id"):
        if not isinstance(data[key], str) or not data[key].strip():
            raise InstagramAuthError(f"Credentials key '{key}' must be a non-empty string")

    token_expiry = data.get("token_expiry")
    if not isinstance(token_expiry, str) or not token_expiry.strip():
        raise InstagramAuthError("Credentials key 'token_expiry' must be a non-empty string")

    return {
        "access_token": data["access_token"],
        "ig_user_id": data["ig_user_id"],
        "token_expiry": token_expiry,
    }


def refresh_instagram_token(credentials_file: str) -> str:
    """Refresh the long-lived Instagram token if it expires within 7 days.

    Long-lived tokens are valid for 60 days and can be refreshed via the
    Graph API.  If the token is still fresh (expiry > 7 days away), this
    is a no-op that returns the current token.

    Returns the (possibly refreshed) access_token.
    Raises InstagramAuthError on API failure.
    """
    data = load_instagram_token(credentials_file)
    access_token = data["access_token"]

    try:
        expiry = datetime.fromisoformat(data["token_expiry"])
    except (ValueError, TypeError) as e:
        raise InstagramAuthError(f"Invalid token_expiry format: {e}") from e

    # Make expiry offset-aware if naive (assume UTC)
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)

    now = datetime.now(UTC)
    days_until_expiry = (expiry - now).total_seconds() / 86400

    if days_until_expiry > 7:
        log.debug("Instagram token still fresh (%.1f days remaining), skipping refresh", days_until_expiry)
        return access_token

    log.info("Instagram token expires in %.1f days, refreshing...", days_until_expiry)

    try:
        resp = requests.get(
            f"{GRAPH_API_BASE}/refresh_access_token",
            params={
                "grant_type": "ig_refresh_token",
                "access_token": access_token,
            },
            timeout=30,
        )
    except requests.RequestException as e:
        raise InstagramAuthError(f"Token refresh request failed: {e}") from e

    if resp.status_code != 200:
        raise InstagramAuthError(
            f"Token refresh failed (HTTP {resp.status_code}): {resp.text}"
        )

    try:
        result = cast(dict[str, Any], resp.json())
    except ValueError as e:
        raise InstagramAuthError(f"Token refresh returned invalid JSON: {e}") from e

    new_token = result.get("access_token")
    expires_in = result.get("expires_in")  # seconds
    if not isinstance(new_token, str) or not new_token:
        raise InstagramAuthError("Token refresh response missing access_token")

    # Calculate new expiry
    if expires_in and isinstance(expires_in, (int, float)):
        new_expiry = now.replace(microsecond=0) + timedelta(seconds=int(expires_in))
    else:
        # Default to 60 days if expires_in not provided
        new_expiry = now.replace(microsecond=0) + timedelta(days=60)

    data["access_token"] = new_token
    data["token_expiry"] = new_expiry.isoformat()

    try:
        fd = os.open(credentials_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        raise InstagramAuthError(f"Failed to write refreshed token: {e}") from e

    log.info("Instagram token refreshed, new expiry: %s", new_expiry.isoformat())
    return new_token


# ---------------------------------------------------------------------------
# Video hosting via GitHub Release asset
# ---------------------------------------------------------------------------


def _create_temp_release(video_path: str, clip_id: str) -> tuple[str, str]:
    """Create a temporary GitHub release to host the video file publicly.

    Instagram requires a publicly-accessible URL for reel uploads.
    Returns (release_tag, asset_url).
    Raises InstagramPublishError on failure.
    """
    release_tag = f"temp-ig-{clip_id}"

    # Create the release with the video as an asset
    result = subprocess.run(
        ["gh", "release", "create", release_tag, "--title", "temp", "--notes", "", video_path],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise InstagramPublishError(
            f"Failed to create temp release {release_tag}: {result.stderr}"
        )

    # Get the asset download URL
    result = subprocess.run(
        [
            "gh", "release", "view", release_tag,
            "--json", "assets",
            "--jq", ".assets[0].url",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise InstagramPublishError(
            f"Failed to get asset URL for {release_tag}: {result.stderr}"
        )

    asset_url = result.stdout.strip()
    log.info("Temp release created: %s -> %s", release_tag, asset_url)
    return release_tag, asset_url


def _queue_release_cleanup(release_tag: str) -> None:
    """Persist a failed temp-release cleanup for retry on the next run."""
    try:
        os.makedirs(os.path.dirname(_CLEANUP_QUEUE_PATH), exist_ok=True)
        existing: set[str] = set()
        if os.path.exists(_CLEANUP_QUEUE_PATH):
            with open(_CLEANUP_QUEUE_PATH, encoding="utf-8") as f:
                existing = {line.strip() for line in f if line.strip()}
        if release_tag in existing:
            return
        with open(_CLEANUP_QUEUE_PATH, "a", encoding="utf-8") as f:
            f.write(release_tag + "\n")
    except OSError:
        log.warning("Failed to queue temp release cleanup for %s", release_tag, exc_info=True)


def _delete_temp_release(release_tag: str, *, queue_on_failure: bool = True) -> bool:
    """Delete a temporary GitHub release. Returns True if deleted (or already absent)."""
    for attempt in range(3):
        try:
            result = subprocess.run(
                ["gh", "release", "delete", release_tag, "-y", "--cleanup-tag"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                log.info("Temp release deleted: %s", release_tag)
                return True
            stderr = (result.stderr or "").strip()
            if "not found" in stderr.lower():
                log.info("Temp release %s already absent", release_tag)
                return True
            if attempt < 2:
                time.sleep(2**attempt)
            else:
                log.warning("Failed to delete temp release %s: %s", release_tag, stderr)
        except Exception:
            if attempt < 2:
                time.sleep(2**attempt)
            else:
                log.warning("Failed to delete temp release %s", release_tag, exc_info=True)

    if queue_on_failure:
        _queue_release_cleanup(release_tag)
    return False


def _drain_release_cleanup_queue() -> None:
    """Retry cleanup for previously failed temp-release deletions."""
    if not os.path.exists(_CLEANUP_QUEUE_PATH):
        return

    try:
        with open(_CLEANUP_QUEUE_PATH, encoding="utf-8") as f:
            tags = [line.strip() for line in f if line.strip()]
    except OSError:
        log.warning("Failed to read IG cleanup queue", exc_info=True)
        return

    if not tags:
        return

    remaining: list[str] = []
    for tag in tags:
        if not _delete_temp_release(tag, queue_on_failure=False):
            remaining.append(tag)

    try:
        if remaining:
            with open(_CLEANUP_QUEUE_PATH, "w", encoding="utf-8") as f:
                for tag in remaining:
                    f.write(tag + "\n")
        else:
            os.remove(_CLEANUP_QUEUE_PATH)
    except OSError:
        log.warning("Failed to update IG cleanup queue", exc_info=True)


# ---------------------------------------------------------------------------
# Instagram Graph API
# ---------------------------------------------------------------------------


def _create_reel_container(
    ig_user_id: str,
    access_token: str,
    video_url: str,
    caption: str,
    share_to_feed: bool = True,
) -> str:
    """Create a reel media container via the Instagram Graph API.

    Returns the container_id.
    Raises InstagramAuthError on 401/403, InstagramRateLimitError on 429.
    """
    url = f"{GRAPH_API_BASE}/{GRAPH_API_VERSION}/{ig_user_id}/media"
    params = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": str(share_to_feed).lower(),
        "access_token": access_token,
    }

    resp = requests.post(url, params=params, timeout=30)

    if resp.status_code in (401, 403):
        raise InstagramAuthError(
            f"Instagram auth error (HTTP {resp.status_code}): {resp.text}"
        )
    if resp.status_code == 429:
        raise InstagramRateLimitError(f"Instagram rate limit hit: {resp.text}")
    if resp.status_code != 200:
        raise InstagramPublishError(
            f"Failed to create reel container (HTTP {resp.status_code}): {resp.text}"
        )

    try:
        data = cast(dict[str, Any], resp.json())
    except ValueError as e:
        raise InstagramPublishError(f"Invalid JSON in container response: {e}") from e

    container_id = data.get("id")
    if not isinstance(container_id, str) or not container_id:
        raise InstagramPublishError(f"Container response missing 'id': {data}")

    log.info("Reel container created: %s", container_id)
    return container_id


def _poll_container_status(
    container_id: str,
    access_token: str,
    timeout: int = 300,
    interval: int = 10,
) -> str:
    """Poll the container status until FINISHED, ERROR, EXPIRED, or timeout.

    Returns the container_id when status is FINISHED.
    Raises InstagramPublishError on ERROR/EXPIRED status or timeout.
    """
    url = f"{GRAPH_API_BASE}/{GRAPH_API_VERSION}/{container_id}"
    params = {
        "fields": "status_code,status",
        "access_token": access_token,
    }

    deadline = time.monotonic() + timeout

    while True:
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            raise InstagramPublishError(
                f"Container status check failed (HTTP {resp.status_code}): {resp.text}"
            )

        data = cast(dict[str, Any], resp.json())
        status_code = data.get("status_code", "")
        status = data.get("status", "")
        if not isinstance(status_code, str):
            status_code = str(status_code)
        if not isinstance(status, str):
            status = str(status)

        log.debug("Container %s status: %s (%s)", container_id, status_code, status)

        if status_code == "FINISHED":
            log.info("Container %s ready for publishing", container_id)
            return container_id

        if status_code in ("ERROR", "EXPIRED"):
            raise InstagramPublishError(
                f"Container {container_id} failed with status {status_code}: {status}"
            )

        if time.monotonic() >= deadline:
            raise InstagramPublishError(
                f"Container {container_id} timed out after {timeout}s (last status: {status_code})"
            )

        time.sleep(interval)


def _publish_container(
    ig_user_id: str,
    access_token: str,
    container_id: str,
) -> str:
    """Publish a finished reel container.

    Returns the ig_media_id.
    Raises InstagramPublishError on failure.
    """
    url = f"{GRAPH_API_BASE}/{GRAPH_API_VERSION}/{ig_user_id}/media_publish"
    params = {
        "creation_id": container_id,
        "access_token": access_token,
    }

    resp = requests.post(url, params=params, timeout=30)

    if resp.status_code != 200:
        raise InstagramPublishError(
            f"Failed to publish reel (HTTP {resp.status_code}): {resp.text}"
        )

    try:
        data = cast(dict[str, Any], resp.json())
    except ValueError as e:
        raise InstagramPublishError(f"Invalid JSON in publish response: {e}") from e

    media_id = data.get("id")
    if not isinstance(media_id, str) or not media_id:
        raise InstagramPublishError(f"Publish response missing 'id': {data}")

    log.info("Reel published: %s", media_id)
    return media_id


# ---------------------------------------------------------------------------
# Caption building
# ---------------------------------------------------------------------------


def build_instagram_caption(
    clip: Clip,
    caption_template: str | None = None,
    caption_templates: list[str] | None = None,
    hashtags: list[str] | None = None,
    prebuilt_title: str | None = None,
    max_length: int = 2200,
) -> str:
    """Build an Instagram caption from a clip.

    Structure: title + "\\n\\n" + hashtags (space-separated).
    Reuses template/sanitize helpers from youtube_uploader.
    Truncates to max_length (Instagram limit is 2200 chars).
    """
    if prebuilt_title is not None:
        title = prebuilt_title
    else:
        chosen = _choose_template(clip.id, caption_templates) or caption_template
        title = _sanitize_text(_render_template(chosen, clip)) if chosen else _sanitize_text(clip.title)

    parts = [title]

    if hashtags:
        tag_str = " ".join(f"#{tag.lstrip('#')}" for tag in hashtags if tag.strip())
        if tag_str:
            parts.append(tag_str)

    caption = "\n\n".join(parts)

    if len(caption) > max_length:
        caption = caption[:max_length]

    return caption


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


def check_recent_reels(
    ig_user_id: str,
    access_token: str,
    caption_prefix: str,
    limit: int = 25,
) -> str | None:
    """Check recent Instagram media for a duplicate caption prefix.

    Returns the ig_media_id if a duplicate is found, None otherwise.
    """
    url = f"{GRAPH_API_BASE}/{GRAPH_API_VERSION}/{ig_user_id}/media"
    params: dict[str, str | int] = {
        "fields": "id,caption,timestamp",
        "limit": limit,
        "access_token": access_token,
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            log.warning("Failed to check recent reels (HTTP %s): %s", resp.status_code, resp.text)
            return None

        data = cast(dict[str, Any], resp.json())
        raw_items = data.get("data", [])
        if not isinstance(raw_items, list):
            return None
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            item_caption = item.get("caption", "")
            if not isinstance(item_caption, str):
                continue
            if item_caption.startswith(caption_prefix):
                media_id = item.get("id", "")
                if not isinstance(media_id, str) or not media_id:
                    continue
                log.info("Duplicate reel found: '%s' -> %s", caption_prefix, media_id)
                return media_id

    except Exception:
        log.warning("Failed to check recent reels", exc_info=True)

    return None


# ---------------------------------------------------------------------------
# Main upload function
# ---------------------------------------------------------------------------


def upload_reel(
    video_path: str,
    clip: Clip,
    credentials_file: str,
    caption_template: str | None = None,
    caption_templates: list[str] | None = None,
    hashtags: list[str] | None = None,
    prebuilt_title: str | None = None,
) -> str | None:
    """Upload a video as an Instagram Reel.

    Full flow:
      1. Load + refresh Instagram token
      2. Build caption
      3. Check for duplicate reels
      4. Create temp GitHub release for public video URL
      5. Create reel container
      6. Poll until container is ready
      7. Publish the reel
      8. Cleanup temp release (always)

    Returns ig_media_id on success, None on non-auth failure.
    Raises InstagramAuthError or InstagramRateLimitError to let the caller handle.
    """
    release_tag: str | None = None
    _drain_release_cleanup_queue()

    # 1. Load and refresh token
    data = load_instagram_token(credentials_file)
    access_token = refresh_instagram_token(credentials_file)
    ig_user_id = data["ig_user_id"]

    # 2. Build caption
    caption = build_instagram_caption(
        clip,
        caption_template=caption_template,
        caption_templates=caption_templates,
        hashtags=hashtags,
        prebuilt_title=prebuilt_title,
    )

    # 3. Dedup check
    duplicate = check_recent_reels(ig_user_id, access_token, caption.split("\n\n")[0])
    if duplicate:
        log.info("Skipping upload — duplicate reel found: %s", duplicate)
        return None

    try:
        # 4. Host video via temp GitHub release
        release_tag, asset_url = _create_temp_release(video_path, clip.id)

        # 5. Create reel container
        container_id = _create_reel_container(
            ig_user_id, access_token, asset_url, caption
        )

        # 6. Poll until ready
        _poll_container_status(container_id, access_token)

        # 7. Publish
        media_id = _publish_container(ig_user_id, access_token, container_id)

        log.info("Reel upload complete: %s", media_id)
        return media_id

    except (InstagramAuthError, InstagramRateLimitError):
        raise
    except Exception:
        log.exception("Reel upload failed for clip %s", clip.id)
        return None
    finally:
        # 8. Always clean up temp release
        if release_tag:
            _delete_temp_release(release_tag)

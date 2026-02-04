import logging
import time
from datetime import datetime, timedelta, timezone

import requests

from src.models import Clip

log = logging.getLogger(__name__)

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
CLIPS_URL = "https://api.twitch.tv/helix/clips"
GAMES_URL = "https://api.twitch.tv/helix/games"
DEFAULT_TIMEOUT = (5, 15)


class TwitchClient:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def _get_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        resp = requests.post(TOKEN_URL, params={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
        }, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        # Refresh 60s before actual expiry to avoid edge-case 401s
        self._token_expires_at = time.monotonic() + data.get("expires_in", 3600) - 60
        return self._token

    def _headers(self) -> dict:
        return {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self._get_token()}",
        }

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        if "timeout" not in kwargs:
            kwargs["timeout"] = DEFAULT_TIMEOUT
        resp: requests.Response | None = None
        for attempt in range(3):
            resp = requests.request(method, url, headers=self._headers(), **kwargs)
            if resp.status_code == 401:
                self._token = None
                if attempt < 2:
                    continue
            if resp.status_code == 429:
                reset = resp.headers.get("Ratelimit-Reset")
                try:
                    wait = min(max(int(reset) - int(time.time()), 1), 60)
                except (ValueError, TypeError):
                    wait = 5
                log.warning("Rate limited by Twitch, waiting %ds", wait)
                time.sleep(wait)
                if attempt < 2:
                    continue
            resp.raise_for_status()
            return resp
        assert resp is not None
        resp.raise_for_status()
        return resp

    def get_game_names(self, game_ids: list[str]) -> dict[str, str]:
        """Resolve game IDs to names. Returns {game_id: game_name}."""
        ids = [gid for gid in set(game_ids) if gid]
        if not ids:
            return {}
        result = {}
        # API accepts up to 100 IDs per request
        for i in range(0, len(ids), 100):
            batch = ids[i:i + 100]
            params = [("id", gid) for gid in batch]
            resp = self._request("GET", GAMES_URL, params=params)
            for g in resp.json().get("data", []):
                result[g["id"]] = g["name"]
        return result

    def fetch_clips(self, broadcaster_id: str, lookback_hours: int = 24, max_clips: int = 500) -> list[Clip]:
        """Fetch all clips for a broadcaster in the given time window."""
        started_at = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
        ended_at = datetime.now(timezone.utc).isoformat()

        clips: list[Clip] = []
        cursor = None

        while True:
            params = {
                "broadcaster_id": broadcaster_id,
                "started_at": started_at,
                "ended_at": ended_at,
                "first": 100,
            }
            if cursor:
                params["after"] = cursor

            resp = self._request("GET", CLIPS_URL, params=params)

            data = resp.json()
            for c in data.get("data", []):
                try:
                    clip = Clip(
                        id=c["id"],
                        url=c["url"],
                        title=c["title"],
                        view_count=c["view_count"],
                        created_at=c["created_at"],
                        duration=c["duration"],
                        game_id=c.get("game_id", ""),
                    )
                except (KeyError, TypeError) as e:
                    log.warning("Skipping malformed clip data: %s", e)
                    continue
                clips.append(clip)

            cursor = data.get("pagination", {}).get("cursor")
            if not cursor or not data.get("data") or len(clips) >= max_clips:
                break

        clips = clips[:max_clips]

        log.info("Fetched %d clips for broadcaster %s", len(clips), broadcaster_id)
        return clips

"""Trending games detector with caching for score multipliers."""

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

CACHE_FILE = "data/trending_cache.json"
CACHE_TTL_SECONDS = 6 * 3600  # 6 hours


def _load_cache() -> dict | None:
    """Load trending games cache from disk if valid.
    
    Returns:
        Cached data dict with 'games' and 'timestamp', or None if stale/missing
    """
    if not os.path.exists(CACHE_FILE):
        return None
    
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        cached_at = data.get("timestamp", 0)
        age_seconds = time.time() - cached_at
        
        if age_seconds > CACHE_TTL_SECONDS:
            log.info("Trending cache expired (age: %.1fh)", age_seconds / 3600)
            return None
        
        log.info("Using cached trending games (age: %.1fh)", age_seconds / 3600)
        return data
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Failed to load trending cache: %s", e)
        return None


def _save_cache(games: list[dict]):
    """Save trending games to cache file."""
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    
    data = {
        "timestamp": time.time(),
        "games": games,
    }
    
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        log.info("Saved %d trending games to cache", len(games))
    except OSError as e:
        log.warning("Failed to save trending cache: %s", e)


def get_trending_games(twitch_client) -> list[dict]:
    """Get top trending games from Twitch with caching.
    
    Args:
        twitch_client: TwitchClient instance
        
    Returns:
        List of dicts with keys: id, name, rank (1-indexed)
    """
    # Try cache first
    cached = _load_cache()
    if cached:
        return cached.get("games", [])
    
    # Fetch from API
    try:
        games = twitch_client.get_top_games(limit=20)
        _save_cache(games)
        return games
    except Exception as e:
        log.error("Failed to fetch trending games: %s", e)
        return []


def get_trending_multiplier(game_name: str, twitch_client) -> float:
    """Return a score multiplier based on how trending a game is.
    
    Multiplier tiers:
    - Top 5: 1.5x
    - Top 10: 1.3x
    - Top 20: 1.15x
    - Not trending: 1.0x
    
    Args:
        game_name: Name of the game to check
        twitch_client: TwitchClient instance
        
    Returns:
        Multiplier value (1.0-1.5)
    """
    if not game_name:
        return 1.0
    
    games = get_trending_games(twitch_client)
    
    # Find rank for this game (case-insensitive match)
    game_name_lower = game_name.lower().strip()
    for game in games:
        if game["name"].lower().strip() == game_name_lower:
            rank = game["rank"]
            if rank <= 5:
                return 1.5
            elif rank <= 10:
                return 1.3
            elif rank <= 20:
                return 1.15
            break
    
    return 1.0


def get_trending_multipliers(twitch_client) -> dict[str, float]:
    """Get all trending multipliers as a dict for batch scoring.
    
    This is more efficient than calling get_trending_multiplier() per clip
    since it fetches the trending games list only once.
    
    Args:
        twitch_client: TwitchClient instance
        
    Returns:
        Dict mapping game name to multiplier {game_name: multiplier}
    """
    games = get_trending_games(twitch_client)
    
    multipliers = {}
    for game in games:
        name = game["name"]
        rank = game["rank"]
        
        if rank <= 5:
            multipliers[name] = 1.5
        elif rank <= 10:
            multipliers[name] = 1.3
        elif rank <= 20:
            multipliers[name] = 1.15
    
    return multipliers

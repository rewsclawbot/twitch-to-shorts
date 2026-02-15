#!/usr/bin/env python3
"""Automated streamer discovery based on clip virality potential.

Finds streamers with high-quality, engaging clips in the optimal viewer range
(big enough for good clips, small enough to not be over-saturated).
"""

import argparse
import json
import logging
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.twitch_client import TwitchClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def analyze_streamer(client: TwitchClient, user_id: str, user_login: str, user_name: str) -> dict | None:
    """Analyze a streamer's clips to determine viral potential.
    
    Args:
        client: TwitchClient instance
        user_id: Twitch user ID
        user_login: Twitch login name
        user_name: Display name
        
    Returns:
        Dict with analysis results or None if insufficient data
    """
    # Fetch clips from last 7 days
    clips = client.fetch_clips(user_id, lookback_hours=168, max_clips=200)
    
    if len(clips) < 5:
        log.info("  %s: Only %d clips, skipping (need at least 5)", user_name, len(clips))
        return None
    
    # Calculate metrics
    views = [c.view_count for c in clips]
    durations = [c.duration for c in clips]
    games = [c.game_id for c in clips if c.game_id]
    
    avg_views = sum(views) / len(views)
    max_views = max(views)
    total_views = sum(views)
    unique_games = len(set(games))
    
    # Game distribution
    game_counts = Counter(games)
    top_games = game_counts.most_common(3)
    
    # Resolve game names
    game_ids = [g[0] for g in top_games]
    game_names = client.get_game_names(game_ids)
    top_game_names = [game_names.get(g[0], "Unknown") for g in top_games]
    
    # Clip frequency (clips per day)
    clip_frequency = len(clips) / 7.0
    
    # Shorts-ready percentage (clips ≤30s)
    shorts_ready = sum(1 for d in durations if d <= 30)
    shorts_pct = (shorts_ready / len(clips)) * 100
    
    return {
        "user_id": user_id,
        "user_login": user_login,
        "user_name": user_name,
        "clip_count": len(clips),
        "avg_views": round(avg_views, 1),
        "max_views": max_views,
        "total_views": total_views,
        "unique_games": unique_games,
        "top_games": top_game_names,
        "clip_frequency": round(clip_frequency, 1),
        "shorts_ready_pct": round(shorts_pct, 1),
        "avg_duration": round(sum(durations) / len(durations), 1),
    }


def score_streamer(data: dict) -> float:
    """Calculate a virality score for a streamer.
    
    Scoring components:
    - Clip volume (25 pts): More clips = more content
    - Avg views (25 pts): Higher engagement per clip
    - Peak virality (15 pts): Potential for breakout clips
    - Game diversity (10 pts): Broader audience appeal
    - Shorts-ready % (25 pts): Clips that fit YouTube Shorts format
    
    Args:
        data: Streamer analysis dict
        
    Returns:
        Score from 0-100
    """
    # Clip volume: aim for ~10+ clips/week for good content flow
    volume_score = min(data["clip_frequency"] / 10.0, 1.0) * 25
    
    # Avg views: 500+ is good, 2000+ is excellent
    avg_views_score = min(data["avg_views"] / 2000.0, 1.0) * 25
    
    # Peak virality: 10K+ views is good, 50K+ is excellent
    peak_score = min(data["max_views"] / 50000.0, 1.0) * 15
    
    # Game diversity: 3+ games is good, 5+ is excellent
    diversity_score = min(data["unique_games"] / 5.0, 1.0) * 10
    
    # Shorts-ready: 40%+ is good, 70%+ is excellent
    shorts_score = min(data["shorts_ready_pct"] / 70.0, 1.0) * 25
    
    total = volume_score + avg_views_score + peak_score + diversity_score + shorts_score
    return round(total, 1)


def generate_yaml_config(data: dict) -> str:
    """Generate ready-to-paste YAML config block for a streamer.
    
    Args:
        data: Streamer analysis dict
        
    Returns:
        YAML config string
    """
    return f"""  - name: {data['user_name']}
    twitch_id: "{data['user_id']}"
    youtube_credentials: credentials-{data['user_login']}.json
    facecam:
      x: 0.75
      y: 0.02
      w: 0.22
      h: 0.30
    # {data['clip_count']} clips/week, {data['avg_views']:.0f} avg views, {data['unique_games']} games
    # Top games: {', '.join(data['top_games'][:2])}"""


def discover_streamers(
    client: TwitchClient,
    game_name: str | None = None,
    min_viewers: int = 1000,
    max_viewers: int = 50000,
    max_results: int = 20,
) -> list[dict]:
    """Discover promising streamers based on clip virality potential.
    
    Args:
        client: TwitchClient instance
        game_name: Optional game name to focus on
        min_viewers: Minimum concurrent viewer count
        max_viewers: Maximum concurrent viewer count
        max_results: Max streamers to analyze
        
    Returns:
        List of analyzed streamers with scores
    """
    log.info("Starting streamer discovery...")
    log.info("  Viewer range: %d - %d", min_viewers, max_viewers)
    
    # Get top games to search
    if game_name:
        log.info("  Searching for game: %s", game_name)
        # Find game by name (search through top games)
        top_games = client.get_top_games(limit=100)
        game_id = None
        for game in top_games:
            if game["name"].lower() == game_name.lower():
                game_id = game["id"]
                break
        
        if not game_id:
            log.error("Game '%s' not found in top 100 games", game_name)
            return []
        
        game_ids = [game_id]
    else:
        # Search across top 10 trending games
        log.info("  Searching across top 10 trending games")
        top_games = client.get_top_games(limit=10)
        game_ids = [g["id"] for g in top_games]
    
    # Collect candidate streamers
    candidates = []
    seen_user_ids = set()
    
    for game_id in game_ids:
        streams = client.get_streams(game_id=game_id, first=100)
        
        for stream in streams:
            user_id = stream["user_id"]
            
            # Skip if already seen
            if user_id in seen_user_ids:
                continue
            
            # Filter by viewer count
            if not (min_viewers <= stream["viewer_count"] <= max_viewers):
                continue
            
            seen_user_ids.add(user_id)
            candidates.append({
                "user_id": user_id,
                "user_login": stream["user_login"],
                "user_name": stream["user_name"],
                "viewer_count": stream["viewer_count"],
                "game_name": stream["game_name"],
            })
            
            log.info("  Found candidate: %s (%d viewers, playing %s)",
                    stream["user_name"], stream["viewer_count"], stream["game_name"])
            
            if len(candidates) >= max_results:
                break
        
        if len(candidates) >= max_results:
            break
    
    log.info("Found %d candidate streamers, analyzing clips...", len(candidates))
    
    # Analyze each candidate
    results = []
    for i, candidate in enumerate(candidates, 1):
        log.info("[%d/%d] Analyzing %s...", i, len(candidates), candidate["user_name"])
        
        analysis = analyze_streamer(
            client,
            candidate["user_id"],
            candidate["user_login"],
            candidate["user_name"],
        )
        
        if analysis:
            analysis["current_viewers"] = candidate["viewer_count"]
            analysis["current_game"] = candidate["game_name"]
            analysis["score"] = score_streamer(analysis)
            results.append(analysis)
    
    # Sort by score (descending)
    results.sort(key=lambda x: x["score"], reverse=True)
    
    log.info("Analysis complete! Found %d streamers with sufficient data", len(results))
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Discover promising Twitch streamers for YouTube Shorts pipeline"
    )
    parser.add_argument(
        "--game",
        type=str,
        help="Focus on a specific game (e.g., 'Fortnite')",
    )
    parser.add_argument(
        "--min-viewers",
        type=int,
        default=1000,
        help="Minimum concurrent viewer count (default: 1000)",
    )
    parser.add_argument(
        "--max-viewers",
        type=int,
        default=50000,
        help="Maximum concurrent viewer count (default: 50000)",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=20,
        help="Maximum streamers to analyze (default: 20)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/streamer_recommendations.json",
        help="Output JSON file path",
    )
    
    args = parser.parse_args()
    
    # Get Twitch credentials
    client_id = os.environ.get("TWITCH_CLIENT_ID")
    client_secret = os.environ.get("TWITCH_CLIENT_SECRET")
    
    if not client_id or not client_secret:
        log.error("TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET must be set")
        sys.exit(1)
    
    client = TwitchClient(client_id, client_secret)
    
    # Discover streamers
    results = discover_streamers(
        client,
        game_name=args.game,
        min_viewers=args.min_viewers,
        max_viewers=args.max_viewers,
        max_results=args.max_results,
    )
    
    if not results:
        log.warning("No streamers found matching criteria")
        return
    
    # Save to JSON
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    output_data = {
        "generated_at": datetime.now(UTC).isoformat(),
        "criteria": {
            "game": args.game,
            "min_viewers": args.min_viewers,
            "max_viewers": args.max_viewers,
        },
        "streamers": results,
    }
    
    with open(output_path, "w") as f:
        json.dump(output_data, f, indent=2)
    
    log.info("Results saved to %s", output_path)
    
    # Print summary
    print("\n" + "=" * 80)
    print("STREAMER DISCOVERY RESULTS")
    print("=" * 80)
    print(f"\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Criteria: {args.min_viewers:,} - {args.max_viewers:,} viewers")
    if args.game:
        print(f"Game filter: {args.game}")
    
    print("\n" + "-" * 80)
    print("TOP RECOMMENDATIONS")
    print("-" * 80)
    
    for i, streamer in enumerate(results[:10], 1):
        print(f"\n{i}. {streamer['user_name']} (Score: {streamer['score']}/100)")
        print(f"   Twitch: twitch.tv/{streamer['user_login']}")
        print(f"   Current: {streamer['current_viewers']:,} viewers, playing {streamer['current_game']}")
        print(f"   Clips: {streamer['clip_count']} in last 7 days ({streamer['clip_frequency']}/day)")
        print(f"   Engagement: {streamer['avg_views']:,.0f} avg views, {streamer['max_views']:,} peak")
        print(f"   Games: {streamer['unique_games']} unique, top: {', '.join(streamer['top_games'][:2])}")
        print(f"   Shorts-ready: {streamer['shorts_ready_pct']:.0f}% of clips ≤30s")
    
    print("\n" + "-" * 80)
    print("YAML CONFIG (Top 5)")
    print("-" * 80)
    print("\nstreamers:")
    
    for streamer in results[:5]:
        print(generate_yaml_config(streamer))
    
    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()

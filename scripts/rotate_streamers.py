#!/usr/bin/env python3
"""Automated weekly streamer rotation based on performance metrics.

Evaluates current streamers, identifies underperformers, and replaces them
with high-potential discoveries from the streamer discovery module.

Usage:
    python scripts/rotate_streamers.py          # Dry-run (default)
    python scripts/rotate_streamers.py --execute  # Actually make changes
"""

import argparse
import json
import logging
import os
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))
os.chdir(Path(__file__).parent.parent)

from dotenv import load_dotenv
load_dotenv()

from src.db import get_connection
from src.twitch_client import TwitchClient
from scripts.discover_streamers import discover_streamers, score_streamer, analyze_streamer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

CONFIG_PATH = Path("config.yaml")
ROTATION_LOG_PATH = Path("data/rotation_log.json")
DB_PATH = Path("data/clips.db")

# Default facecam coords for new streamers
DEFAULT_FACECAM = {
    "x": 0.75,
    "y": 0.02,
    "w": 0.22,
    "h": 0.30,
    "output_w": 420,
}

# Rotation constraints
MIN_PROTECTED_STREAMERS = 3  # Never rotate out top 3 performers
MAX_ROTATIONS_PER_RUN = 2    # Maximum streamers to swap per run
HEALTH_THRESHOLD = 0.4       # Score below this triggers rotation consideration
LOOKBACK_DAYS = 14           # Days to look back for metrics


def calculate_health_score(
    clip_count: int,
    upload_count: int,
    avg_youtube_views: float,
    clips_available: int,
) -> float:
    """Calculate a 0-1 health score for a streamer.
    
    Components:
    - Clip availability (40%): Raw clips available from Twitch
    - Upload activity (30%): How many we actually uploaded
    - YouTube performance (30%): Average views on uploaded clips
    
    Args:
        clip_count: Clips fetched from Twitch in lookback period
        upload_count: Clips we uploaded to YouTube
        avg_youtube_views: Average YouTube views on uploaded clips
        clips_available: Fresh clips available now
        
    Returns:
        Score from 0.0 (dead) to 1.0 (excellent)
    """
    # Clip availability: 10+ clips = full score
    availability_score = min(clips_available / 10.0, 1.0) * 0.4
    
    # Upload activity: 5+ uploads in 14 days = full score
    activity_score = min(upload_count / 5.0, 1.0) * 0.3
    
    # YouTube performance: 500+ avg views = full score
    performance_score = min(avg_youtube_views / 500.0, 1.0) * 0.3
    
    total = availability_score + activity_score + performance_score
    return round(total, 3)


def evaluate_streamer(
    conn,
    streamer_name: str,
    twitch_id: str,
    twitch_client: TwitchClient,
) -> dict:
    """Evaluate a streamer's performance and health.
    
    Args:
        conn: Database connection
        streamer_name: Streamer name from config
        twitch_id: Twitch user ID
        twitch_client: TwitchClient instance
        
    Returns:
        Dict with health metrics and score
    """
    cutoff = (datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)).isoformat()
    
    # Query database for uploads and metrics
    upload_stats = conn.execute(
        """SELECT COUNT(*) as upload_count,
                  AVG(COALESCE(yt_views, 0)) as avg_views
           FROM clips
           WHERE streamer = ?
             AND youtube_id IS NOT NULL
             AND posted_at IS NOT NULL
             AND posted_at >= ?""",
        (streamer_name, cutoff),
    ).fetchone()
    
    upload_count = upload_stats["upload_count"] or 0
    avg_youtube_views = upload_stats["avg_views"] or 0.0
    
    # Check current clip availability from Twitch
    clips_available = len(twitch_client.fetch_clips(twitch_id, lookback_hours=LOOKBACK_DAYS * 24))
    
    # Calculate clip count in DB (all clips, not just uploaded)
    clip_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM clips WHERE streamer = ? AND created_at >= ?",
        (streamer_name, cutoff),
    ).fetchone()["cnt"] or 0
    
    health_score = calculate_health_score(
        clip_count=clip_count,
        upload_count=upload_count,
        avg_youtube_views=avg_youtube_views,
        clips_available=clips_available,
    )
    
    return {
        "name": streamer_name,
        "twitch_id": twitch_id,
        "clip_count": clip_count,
        "clips_available": clips_available,
        "upload_count": upload_count,
        "avg_youtube_views": round(avg_youtube_views, 1),
        "health_score": health_score,
    }


def find_replacement_candidates(
    twitch_client: TwitchClient,
    current_streamer_ids: set[str],
    count: int = 5,
) -> list[dict]:
    """Discover and score potential replacement streamers.
    
    Args:
        twitch_client: TwitchClient instance
        current_streamer_ids: Set of Twitch IDs already configured
        count: Number of candidates to return
        
    Returns:
        List of candidate dicts with discovery scores
    """
    log.info("Running streamer discovery to find replacements...")
    
    # Discover streamers in the sweet spot (good clips, not oversaturated)
    discoveries = discover_streamers(
        twitch_client,
        game_name=None,  # Search across top games
        min_viewers=1000,
        max_viewers=50000,
        max_results=30,
    )
    
    # Filter out already-configured streamers
    candidates = [
        d for d in discoveries
        if d["user_id"] not in current_streamer_ids
    ]
    
    # Sort by score and return top N
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:count]


def select_rotations(
    current_streamers: list[dict],
    candidates: list[dict],
) -> list[tuple[dict, dict]]:
    """Decide which streamers to rotate out and their replacements.
    
    Strategy:
    1. Never rotate top MIN_PROTECTED_STREAMERS performers
    2. Only rotate streamers below HEALTH_THRESHOLD
    3. Only if replacement has better discovery score
    4. Maximum MAX_ROTATIONS_PER_RUN changes per run
    
    Args:
        current_streamers: List of evaluated current streamers
        candidates: List of replacement candidates
        
    Returns:
        List of (current, replacement) tuples
    """
    # Sort current by health score (descending)
    sorted_current = sorted(current_streamers, key=lambda x: x["health_score"], reverse=True)
    
    # Protect top performers
    protected = sorted_current[:MIN_PROTECTED_STREAMERS]
    protected_names = {s["name"] for s in protected}
    log.info(f"Protected top {MIN_PROTECTED_STREAMERS}: {', '.join(protected_names)}")
    
    # Find underperformers (below threshold and not protected)
    underperformers = [
        s for s in sorted_current
        if s["health_score"] < HEALTH_THRESHOLD and s["name"] not in protected_names
    ]
    
    if not underperformers:
        log.info("No underperformers found (all above threshold or protected)")
        return []
    
    if not candidates:
        log.warning("No replacement candidates available")
        return []
    
    # Match underperformers with replacements
    rotations = []
    for underperformer in underperformers[:MAX_ROTATIONS_PER_RUN]:
        if not candidates:
            break
        
        # Take the top-scoring candidate
        replacement = candidates.pop(0)
        
        # Only rotate if replacement looks better
        # Discovery score is 0-100, health score is 0-1, so normalize
        replacement_potential = replacement["score"] / 100.0
        
        if replacement_potential > underperformer["health_score"]:
            rotations.append((underperformer, replacement))
            log.info(
                f"Rotation candidate: {underperformer['name']} "
                f"(health {underperformer['health_score']:.2f}) → "
                f"{replacement['user_name']} (potential {replacement_potential:.2f})"
            )
        else:
            log.info(
                f"Skipping {underperformer['name']}: "
                f"no candidate better than health {underperformer['health_score']:.2f}"
            )
    
    return rotations


def apply_rotations(
    config: dict,
    rotations: list[tuple[dict, dict]],
    dry_run: bool = True,
) -> bool:
    """Apply rotations to config.yaml.
    
    Args:
        config: Parsed config dict
        rotations: List of (current, replacement) tuples
        dry_run: If True, only show changes without writing
        
    Returns:
        True if changes were made (or would be made in dry-run)
    """
    if not rotations:
        log.info("No rotations to apply")
        return False
    
    # Backup config before modifying
    if not dry_run:
        backup_path = CONFIG_PATH.with_suffix(f".yaml.backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(CONFIG_PATH, backup_path)
        log.info(f"Backed up config to {backup_path}")
    
    # Get the shared YouTube credentials from existing streamers
    existing_creds = config["streamers"][0].get("youtube_credentials", "credentials/theburntpeanut_youtube.json")
    
    # Apply each rotation
    for current, replacement in rotations:
        # Find and remove current streamer
        config["streamers"] = [
            s for s in config["streamers"]
            if s["name"] != current["name"]
        ]
        
        # Add replacement streamer
        new_streamer = {
            "name": replacement["user_name"],
            "twitch_id": replacement["user_id"],
            "youtube_credentials": existing_creds,
            "facecam": DEFAULT_FACECAM.copy(),
            "privacy_status": "public",
            "category_id": "20",
        }
        
        config["streamers"].append(new_streamer)
        
        log.info(
            f"{'[DRY RUN] Would rotate' if dry_run else 'Rotated'}: "
            f"{current['name']} → {replacement['user_name']}"
        )
    
    # Write updated config
    if not dry_run:
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        log.info(f"Updated {CONFIG_PATH}")
    
    return True


def log_rotation(rotations: list[tuple[dict, dict]], dry_run: bool):
    """Append rotation record to rotation log.
    
    Args:
        rotations: List of (current, replacement) tuples
        dry_run: Whether this was a dry run
    """
    ROTATION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    # Load existing log
    if ROTATION_LOG_PATH.exists():
        with open(ROTATION_LOG_PATH) as f:
            log_data = json.load(f)
    else:
        log_data = {"rotations": []}
    
    # Append new entry
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "dry_run": dry_run,
        "changes": [
            {
                "removed": {
                    "name": current["name"],
                    "twitch_id": current["twitch_id"],
                    "health_score": current["health_score"],
                    "upload_count": current["upload_count"],
                    "avg_youtube_views": current["avg_youtube_views"],
                },
                "added": {
                    "name": replacement["user_name"],
                    "twitch_id": replacement["user_id"],
                    "discovery_score": replacement["score"],
                    "clip_count": replacement["clip_count"],
                    "avg_views": replacement["avg_views"],
                },
            }
            for current, replacement in rotations
        ],
    }
    
    log_data["rotations"].append(entry)
    
    with open(ROTATION_LOG_PATH, "w") as f:
        json.dump(log_data, f, indent=2)
    
    log.info(f"Logged rotation to {ROTATION_LOG_PATH}")


def main():
    parser = argparse.ArgumentParser(
        description="Automated streamer rotation based on performance"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually apply changes (default: dry-run only)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=HEALTH_THRESHOLD,
        help=f"Health score threshold for rotation (default: {HEALTH_THRESHOLD})",
    )
    
    args = parser.parse_args()
    dry_run = not args.execute
    
    if dry_run:
        log.info("=== DRY RUN MODE (use --execute to apply changes) ===")
    else:
        log.info("=== EXECUTE MODE (changes will be applied) ===")
    
    # Load config
    config = yaml.safe_load(CONFIG_PATH.read_text())
    streamers = config.get("streamers", [])
    
    if len(streamers) < MIN_PROTECTED_STREAMERS:
        log.error(f"Need at least {MIN_PROTECTED_STREAMERS} streamers configured")
        sys.exit(1)
    
    # Initialize clients
    twitch_client = TwitchClient(
        os.environ["TWITCH_CLIENT_ID"],
        os.environ["TWITCH_CLIENT_SECRET"],
    )
    
    conn = get_connection(str(DB_PATH))
    
    # Evaluate all current streamers
    log.info(f"Evaluating {len(streamers)} current streamers...")
    print()
    
    current_evaluations = []
    for streamer in streamers:
        evaluation = evaluate_streamer(
            conn,
            streamer["name"],
            streamer["twitch_id"],
            twitch_client,
        )
        current_evaluations.append(evaluation)
        
        status_icon = "✅" if evaluation["health_score"] >= HEALTH_THRESHOLD else "⚠️"
        print(
            f"{status_icon} {evaluation['name']}: "
            f"health={evaluation['health_score']:.2f}, "
            f"uploads={evaluation['upload_count']}, "
            f"clips_avail={evaluation['clips_available']}, "
            f"avg_views={evaluation['avg_youtube_views']:.0f}"
        )
    
    print()
    
    # Find replacement candidates
    current_ids = {s["twitch_id"] for s in streamers}
    candidates = find_replacement_candidates(twitch_client, current_ids, count=5)
    
    if candidates:
        print(f"Found {len(candidates)} replacement candidates:")
        for i, c in enumerate(candidates, 1):
            print(
                f"  {i}. {c['user_name']}: "
                f"score={c['score']}/100, "
                f"clips={c['clip_count']}, "
                f"avg_views={c['avg_views']:.0f}"
            )
        print()
    
    # Decide on rotations
    rotations = select_rotations(current_evaluations, candidates)
    
    if not rotations:
        log.info("No rotations needed - all streamers healthy or no better alternatives")
        conn.close()
        return
    
    print(f"\n{'[DRY RUN] Would make' if dry_run else 'Making'} {len(rotations)} rotation(s):")
    for current, replacement in rotations:
        print(
            f"  OUT: {current['name']} "
            f"(health {current['health_score']:.2f}, "
            f"{current['upload_count']} uploads)"
        )
        print(
            f"  IN:  {replacement['user_name']} "
            f"(score {replacement['score']}/100, "
            f"{replacement['clip_count']} clips)"
        )
        print()
    
    # Apply rotations
    changes_made = apply_rotations(config, rotations, dry_run=dry_run)
    
    # Log the rotation
    if changes_made:
        log_rotation(rotations, dry_run)
    
    conn.close()
    
    if dry_run:
        print("\n✓ Dry run complete. Use --execute to apply changes.")
    else:
        print("\n✓ Rotation complete!")


if __name__ == "__main__":
    main()

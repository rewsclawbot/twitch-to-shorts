#!/usr/bin/env python3
"""CLI wrapper for YouTube comment monitoring and auto-engagement.

Checks recent uploads for new comments and auto-replies to drive engagement.

Usage:
    .venv/bin/python scripts/monitor_comments.py [--dry-run] [--max-videos 5]
"""
import argparse
import logging
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.comment_monitor import monitor_and_engage
from src.db import get_connection
from src.youtube_uploader import get_authenticated_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="Monitor YouTube comments and auto-reply to drive engagement"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't actually post replies, just simulate",
    )
    parser.add_argument(
        "--max-videos",
        type=int,
        default=5,
        help="Maximum number of recent videos to check (default: 5)",
    )
    parser.add_argument(
        "--max-replies-per-video",
        type=int,
        default=2,
        help="Maximum replies per video per run (default: 2)",
    )
    parser.add_argument(
        "--max-total-replies",
        type=int,
        default=10,
        help="Maximum total replies per run (default: 10)",
    )
    parser.add_argument(
        "--db-path",
        default="data/pipeline.db",
        help="Path to database (default: data/pipeline.db)",
    )
    parser.add_argument(
        "--client-secrets",
        default="config/youtube_client_secrets.json",
        help="YouTube client secrets file (default: config/youtube_client_secrets.json)",
    )
    parser.add_argument(
        "--credentials",
        default="config/youtube_credentials.json",
        help="YouTube credentials file (default: config/youtube_credentials.json)",
    )
    
    args = parser.parse_args()
    
    # Validate files exist
    if not os.path.exists(args.client_secrets):
        log.error("Client secrets file not found: %s", args.client_secrets)
        return 1
    
    if not os.path.exists(args.credentials):
        log.error("Credentials file not found: %s", args.credentials)
        log.error("Run the main pipeline once to authenticate")
        return 1
    
    # Connect to database
    log.info("Connecting to database: %s", args.db_path)
    conn = get_connection(args.db_path)
    
    try:
        # Authenticate YouTube service
        log.info("Authenticating with YouTube API...")
        youtube_service = get_authenticated_service(args.client_secrets, args.credentials)
        
        # Run comment monitoring
        if args.dry_run:
            log.info("DRY RUN MODE - No replies will be posted")
        
        result = monitor_and_engage(
            youtube_service,
            conn,
            max_videos=args.max_videos,
            max_replies_per_video=args.max_replies_per_video,
            max_total_replies=args.max_total_replies,
            dry_run=args.dry_run,
        )
        
        log.info("=" * 60)
        log.info("Comment Monitoring Summary:")
        log.info("  Videos checked: %d", result["videos_checked"])
        log.info("  Comments fetched: %d", result["comments_fetched"])
        log.info("  Replies posted: %d", result["replies_posted"])
        log.info("  Videos engaged: %d", result["videos_engaged"])
        log.info("=" * 60)
        
        return 0
        
    except Exception:
        log.exception("Comment monitoring failed")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

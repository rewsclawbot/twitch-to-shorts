#!/usr/bin/env python3
"""Daily performance dashboard for ClipFrenzy channel.

Generates a concise daily report with:
- Upload summary (last 24h)
- Analytics snapshot
- Pipeline health
- Trending games
- Growth metrics

Output is formatted for Telegram (plain text with emoji).
"""

import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml


def get_db_connection(db_path: str = "data/clips.db") -> sqlite3.Connection:
    """Get database connection with row factory."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def load_config(config_path: str = "config.yaml") -> dict:
    """Load pipeline configuration."""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_trending_cache(cache_path: str = "data/trending_cache.json") -> dict | None:
    """Load trending games cache if it exists."""
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def format_youtube_url(video_id: str) -> str:
    """Format YouTube Shorts URL."""
    return f"https://youtube.com/shorts/{video_id}"


def get_upload_summary(conn: sqlite3.Connection) -> dict:
    """Get upload summary for last 24 hours."""
    cutoff = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
    
    rows = conn.execute("""
        SELECT clip_id, streamer, title, title_variant, youtube_id, posted_at
        FROM clips
        WHERE posted_at >= ? AND youtube_id IS NOT NULL
        ORDER BY posted_at DESC
    """, (cutoff,)).fetchall()
    
    uploads = []
    for row in rows:
        uploads.append({
            'clip_id': row['clip_id'],
            'streamer': row['streamer'],
            'title': row['title_variant'] or row['title'],
            'youtube_id': row['youtube_id'],
            'posted_at': row['posted_at'],
        })
    
    return {
        'count': len(uploads),
        'uploads': uploads,
    }


def get_analytics_snapshot(conn: sqlite3.Connection) -> dict:
    """Get analytics metrics for all uploaded shorts."""
    # Total channel views
    row = conn.execute("""
        SELECT 
            COALESCE(SUM(yt_views), 0) as total_views,
            COUNT(*) as total_shorts
        FROM clips
        WHERE youtube_id IS NOT NULL
    """).fetchone()
    
    total_views = row['total_views']
    total_shorts = row['total_shorts']
    avg_views = total_views / total_shorts if total_shorts > 0 else 0
    
    # Best performing short
    best_row = conn.execute("""
        SELECT clip_id, title, title_variant, streamer, youtube_id, yt_views
        FROM clips
        WHERE youtube_id IS NOT NULL AND yt_views IS NOT NULL
        ORDER BY yt_views DESC
        LIMIT 1
    """).fetchone()
    
    best_short = None
    if best_row:
        best_short = {
            'title': best_row['title_variant'] or best_row['title'],
            'streamer': best_row['streamer'],
            'youtube_id': best_row['youtube_id'],
            'views': best_row['yt_views'],
        }
    
    # Average retention percentage
    retention_row = conn.execute("""
        SELECT AVG(yt_avg_view_percentage) as avg_retention
        FROM clips
        WHERE youtube_id IS NOT NULL AND yt_avg_view_percentage IS NOT NULL
    """).fetchone()
    
    avg_retention = retention_row['avg_retention'] if retention_row['avg_retention'] else None
    
    return {
        'total_views': int(total_views),
        'total_shorts': total_shorts,
        'avg_views': int(avg_views),
        'avg_retention': avg_retention,
        'best_short': best_short,
    }


def get_pipeline_health(conn: sqlite3.Connection) -> dict:
    """Get pipeline health metrics."""
    # Total clips in database
    total_row = conn.execute("SELECT COUNT(*) as cnt FROM clips").fetchone()
    total_clips = total_row['cnt']
    
    # Uploaded clips
    uploaded_row = conn.execute("""
        SELECT COUNT(*) as cnt FROM clips WHERE youtube_id IS NOT NULL
    """).fetchone()
    uploaded_clips = uploaded_row['cnt']
    
    # Clips in queue
    queue_row = conn.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
            SUM(CASE WHEN status = 'expired' THEN 1 ELSE 0 END) as expired
        FROM clip_queue
    """).fetchone()
    
    queue_total = queue_row['total'] if queue_row['total'] else 0
    queue_pending = queue_row['pending'] if queue_row['pending'] else 0
    queue_expired = queue_row['expired'] if queue_row['expired'] else 0
    
    # Last successful run
    last_run_row = conn.execute("""
        SELECT finished_at, total_uploaded
        FROM pipeline_runs
        WHERE finished_at IS NOT NULL AND total_uploaded > 0
        ORDER BY finished_at DESC
        LIMIT 1
    """).fetchone()
    
    last_run_time = None
    if last_run_row and last_run_row['finished_at']:
        last_run_time = last_run_row['finished_at']
    
    # Failed uploads in last 24h
    cutoff = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
    failed_row = conn.execute("""
        SELECT COUNT(*) as cnt
        FROM clips
        WHERE youtube_id IS NULL 
        AND fail_count > 0 
        AND last_failed_at >= ?
    """, (cutoff,)).fetchone()
    
    failed_uploads = failed_row['cnt']
    
    return {
        'total_clips': total_clips,
        'uploaded_clips': uploaded_clips,
        'queued_clips': queue_total,
        'queue_pending': queue_pending,
        'queue_expired': queue_expired,
        'last_run_time': last_run_time,
        'failed_uploads_24h': failed_uploads,
    }


def get_trending_games_info(config: dict, trending_cache: dict | None) -> dict:
    """Get trending games and which streamers play them."""
    if not trending_cache or 'games' not in trending_cache:
        return {
            'games': [],
            'streamer_games': {},
        }
    
    # Get top 5 trending games
    top_games = trending_cache['games'][:5]
    
    # Build set of streamer names from config
    streamer_names = {s['name'] for s in config.get('streamers', [])}
    
    # For now, we can't easily determine which streamers play which games
    # without querying the clips database. Let's add that logic.
    return {
        'games': top_games,
        'streamer_names': list(streamer_names),
    }


def get_trending_streamers_for_games(conn: sqlite3.Connection, trending_games: list[dict]) -> dict:
    """Find which tracked streamers have clips for trending games."""
    if not trending_games:
        return {}
    
    game_names = [g['name'] for g in trending_games]
    placeholders = ','.join('?' * len(game_names))
    
    rows = conn.execute(f"""
        SELECT DISTINCT game_name, streamer
        FROM clips
        WHERE game_name IN ({placeholders})
        AND youtube_id IS NOT NULL
    """, game_names).fetchall()
    
    # Build map of game -> streamers
    game_streamers = {}
    for row in rows:
        game = row['game_name']
        streamer = row['streamer']
        if game not in game_streamers:
            game_streamers[game] = []
        if streamer not in game_streamers[game]:
            game_streamers[game].append(streamer)
    
    return game_streamers


def get_growth_metrics(conn: sqlite3.Connection) -> dict:
    """Calculate growth metrics (views today vs yesterday, uploads this week vs last)."""
    now = datetime.now(UTC)
    
    # Views today vs yesterday
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    yesterday_start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    
    # Get views for videos posted today
    today_views_row = conn.execute("""
        SELECT COALESCE(SUM(yt_views), 0) as views
        FROM clips
        WHERE posted_at >= ? AND youtube_id IS NOT NULL
    """, (today_start,)).fetchone()
    today_views = today_views_row['views']
    
    # Get views for videos posted yesterday
    yesterday_views_row = conn.execute("""
        SELECT COALESCE(SUM(yt_views), 0) as views
        FROM clips
        WHERE posted_at >= ? AND posted_at < ? AND youtube_id IS NOT NULL
    """, (yesterday_start, today_start)).fetchone()
    yesterday_views = yesterday_views_row['views']
    
    # Upload count this week vs last week
    this_week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    last_week_start = (now - timedelta(days=now.weekday() + 7)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    
    this_week_count = conn.execute("""
        SELECT COUNT(*) as cnt
        FROM clips
        WHERE posted_at >= ? AND youtube_id IS NOT NULL
    """, (this_week_start,)).fetchone()['cnt']
    
    last_week_count = conn.execute("""
        SELECT COUNT(*) as cnt
        FROM clips
        WHERE posted_at >= ? AND posted_at < ? AND youtube_id IS NOT NULL
    """, (last_week_start, this_week_start)).fetchone()['cnt']
    
    return {
        'today_views': int(today_views),
        'yesterday_views': int(yesterday_views),
        'this_week_uploads': this_week_count,
        'last_week_uploads': last_week_count,
    }


def format_time_ago(iso_timestamp: str) -> str:
    """Format ISO timestamp as human-readable time ago."""
    try:
        dt = datetime.fromisoformat(iso_timestamp.replace('Z', '+00:00'))
        now = datetime.now(UTC)
        delta = now - dt
        
        if delta.days > 0:
            return f"{delta.days}d ago"
        hours = delta.seconds // 3600
        if hours > 0:
            return f"{hours}h ago"
        minutes = (delta.seconds % 3600) // 60
        return f"{minutes}m ago"
    except (ValueError, AttributeError):
        return "unknown"


def generate_report(db_path: str = "data/clips.db", config_path: str = "config.yaml") -> str:
    """Generate daily dashboard report."""
    conn = get_db_connection(db_path)
    config = load_config(config_path)
    trending_cache = load_trending_cache()
    
    # Gather all metrics
    upload_summary = get_upload_summary(conn)
    analytics = get_analytics_snapshot(conn)
    health = get_pipeline_health(conn)
    trending_info = get_trending_games_info(config, trending_cache)
    growth = get_growth_metrics(conn)
    
    # Get streamer-game mapping if trending games exist
    game_streamers = {}
    if trending_info['games']:
        game_streamers = get_trending_streamers_for_games(conn, trending_info['games'])
    
    conn.close()
    
    # Build report sections
    lines = []
    
    # Header
    lines.append("📊 ClipFrenzy Daily Dashboard")
    lines.append(f"📅 {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")
    
    # 1. Upload Summary
    lines.append("🎬 Upload Summary (Last 24h)")
    if upload_summary['count'] == 0:
        lines.append("  • No uploads in last 24 hours")
    else:
        lines.append(f"  • {upload_summary['count']} short(s) uploaded")
        for upload in upload_summary['uploads']:
            url = format_youtube_url(upload['youtube_id'])
            lines.append(f"  • {upload['streamer']}: {upload['title'][:50]}")
            lines.append(f"    {url}")
    lines.append("")
    
    # 2. Analytics Snapshot
    lines.append("📈 Analytics Snapshot")
    lines.append(f"  • Total channel views: {analytics['total_views']:,}")
    lines.append(f"  • Total shorts: {analytics['total_shorts']}")
    if analytics['total_shorts'] > 0:
        lines.append(f"  • Avg views/short: {analytics['avg_views']:,}")
    if analytics['avg_retention'] is not None:
        lines.append(f"  • Avg retention: {analytics['avg_retention']:.1f}%")
    if analytics['best_short']:
        best = analytics['best_short']
        lines.append(f"  • Best performing: {best['title'][:40]} ({best['views']:,} views)")
        lines.append(f"    {format_youtube_url(best['youtube_id'])}")
    lines.append("")
    
    # 3. Pipeline Health
    lines.append("⚙️ Pipeline Health")
    lines.append(f"  • Total clips in DB: {health['total_clips']}")
    lines.append(f"  • Uploaded: {health['uploaded_clips']}")
    lines.append(f"  • Queue: {health['queue_pending']} pending, {health['queue_expired']} expired")
    if health['last_run_time']:
        time_ago = format_time_ago(health['last_run_time'])
        lines.append(f"  • Last successful run: {time_ago}")
    else:
        lines.append(f"  • Last successful run: Never")
    if health['failed_uploads_24h'] > 0:
        lines.append(f"  • ⚠️ Failed uploads (24h): {health['failed_uploads_24h']}")
    lines.append("")
    
    # 4. Trending Games
    lines.append("🔥 Trending Games")
    if not trending_info['games']:
        lines.append("  • No trending data available")
    else:
        lines.append("  Top 5 trending games:")
        for game in trending_info['games']:
            rank = game.get('rank', '?')
            name = game.get('name', 'Unknown')
            streamers = game_streamers.get(name, [])
            if streamers:
                streamers_str = ", ".join(streamers)
                lines.append(f"  {rank}. {name} (our streamers: {streamers_str})")
            else:
                lines.append(f"  {rank}. {name}")
    lines.append("")
    
    # 5. Growth Metrics
    lines.append("📊 Growth Metrics")
    if growth['today_views'] > 0 or growth['yesterday_views'] > 0:
        lines.append(f"  • Views today: {growth['today_views']:,}")
        lines.append(f"  • Views yesterday: {growth['yesterday_views']:,}")
        if growth['yesterday_views'] > 0:
            change = ((growth['today_views'] - growth['yesterday_views']) / growth['yesterday_views']) * 100
            emoji = "📈" if change >= 0 else "📉"
            lines.append(f"  • Change: {emoji} {change:+.1f}%")
    else:
        lines.append("  • Not enough data for daily view comparison")
    
    if growth['this_week_uploads'] > 0 or growth['last_week_uploads'] > 0:
        lines.append(f"  • Uploads this week: {growth['this_week_uploads']}")
        lines.append(f"  • Uploads last week: {growth['last_week_uploads']}")
    lines.append("")
    
    return "\n".join(lines)


def main():
    """Main entry point."""
    # Support custom paths via command-line args
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/clips.db"
    config_path = sys.argv[2] if len(sys.argv) > 2 else "config.yaml"
    
    try:
        report = generate_report(db_path, config_path)
        print(report)
    except Exception as e:
        print(f"❌ Error generating dashboard: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate markdown summary of pipeline metrics from clips database."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path


def format_duration(seconds: float | None) -> str:
    """Format duration in seconds to human-readable string."""
    if seconds is None:
        return "N/A"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins}m {secs}s" if mins > 0 else f"{secs}s"


def get_metrics_summary(db_path: str = "data/clips.db") -> str:
    """Generate markdown metrics summary from clips database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    now = datetime.now(UTC)
    day_ago = (now - timedelta(days=1)).isoformat()
    week_ago = (now - timedelta(days=7)).isoformat()

    # Total uploads by streamer
    cursor.execute("""
        SELECT streamer, COUNT(*) as total
        FROM clips
        WHERE youtube_id IS NOT NULL
        GROUP BY streamer
        ORDER BY total DESC
    """)
    streamer_totals = cursor.fetchall()

    # Uploads in last 24h
    cursor.execute("""
        SELECT COUNT(*) as count
        FROM clips
        WHERE youtube_id IS NOT NULL AND posted_at >= ?
    """, (day_ago,))
    uploads_24h = cursor.fetchone()["count"]

    # Uploads in last 7d
    cursor.execute("""
        SELECT COUNT(*) as count
        FROM clips
        WHERE youtube_id IS NOT NULL AND posted_at >= ?
    """, (week_ago,))
    uploads_7d = cursor.fetchone()["count"]

    # Title variant distribution
    cursor.execute("""
        SELECT 
            CASE 
                WHEN title_variant IS NULL THEN 'Not Set'
                WHEN title_variant LIKE '%optimized%' AND title_variant LIKE '%template%' THEN 'Template + Optimized'
                WHEN title_variant LIKE '%optimized%' THEN 'Optimized (LLM)'
                WHEN title_variant LIKE '%template%' THEN 'Template Only'
                WHEN title_variant = 'original' THEN 'Original'
                ELSE 'Other (' || title_variant || ')'
            END as variant,
            COUNT(*) as count
        FROM clips
        WHERE youtube_id IS NOT NULL
        GROUP BY 
            CASE 
                WHEN title_variant IS NULL THEN 'Not Set'
                WHEN title_variant LIKE '%optimized%' AND title_variant LIKE '%template%' THEN 'Template + Optimized'
                WHEN title_variant LIKE '%optimized%' THEN 'Optimized (LLM)'
                WHEN title_variant LIKE '%template%' THEN 'Template Only'
                WHEN title_variant = 'original' THEN 'Original'
                ELSE 'Other (' || title_variant || ')'
            END
        ORDER BY count DESC
    """)
    title_variants = cursor.fetchall()

    # Average clip duration
    cursor.execute("""
        SELECT AVG(duration) as avg_duration
        FROM clips
        WHERE youtube_id IS NOT NULL AND duration IS NOT NULL
    """)
    avg_duration = cursor.fetchone()["avg_duration"]

    # Analytics data summary
    cursor.execute("""
        SELECT 
            COUNT(*) as total_with_analytics,
            SUM(yt_views) as total_views,
            AVG(yt_views) as avg_views,
            AVG(yt_avg_view_percentage) as avg_view_pct,
            AVG(yt_impressions_ctr) as avg_ctr
        FROM clips
        WHERE youtube_id IS NOT NULL AND yt_views IS NOT NULL
    """)
    analytics = cursor.fetchone()

    # Top performing videos (by views)
    cursor.execute("""
        SELECT streamer, title, yt_views, yt_avg_view_percentage, youtube_id
        FROM clips
        WHERE youtube_id IS NOT NULL AND yt_views IS NOT NULL
        ORDER BY yt_views DESC
        LIMIT 5
    """)
    top_videos = cursor.fetchall()

    conn.close()

    # Build markdown report
    md = ["# Pipeline Metrics Summary", ""]
    md.append(f"**Generated:** {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    md.append("")

    # Upload stats
    md.append("## Upload Statistics")
    md.append("")
    md.append(f"- **Last 24 hours:** {uploads_24h} uploads")
    md.append(f"- **Last 7 days:** {uploads_7d} uploads")
    md.append("")

    # By streamer
    md.append("### Total Uploads by Streamer")
    md.append("")
    if streamer_totals:
        md.append("| Streamer | Total Uploads |")
        md.append("|----------|---------------|")
        for row in streamer_totals:
            md.append(f"| {row['streamer']} | {row['total']} |")
    else:
        md.append("*No uploads found*")
    md.append("")

    # Title variants
    md.append("## Title Variant Distribution")
    md.append("")
    if title_variants:
        md.append("| Variant | Count |")
        md.append("|---------|-------|")
        for row in title_variants:
            md.append(f"| {row['variant']} | {row['count']} |")
    else:
        md.append("*No title variant data*")
    md.append("")

    # Clip duration
    md.append("## Clip Metrics")
    md.append("")
    md.append(f"- **Average clip duration:** {format_duration(avg_duration)}")
    md.append("")

    # Analytics
    md.append("## YouTube Analytics")
    md.append("")
    if analytics["total_with_analytics"]:
        md.append(f"- **Videos with analytics:** {analytics['total_with_analytics']}")
        md.append(f"- **Total views:** {analytics['total_views']:,}")
        md.append(f"- **Average views per video:** {analytics['avg_views']:.1f}")
        if analytics["avg_view_pct"] is not None:
            md.append(f"- **Average view percentage:** {analytics['avg_view_pct']:.1f}%")
        if analytics["avg_ctr"] is not None:
            md.append(f"- **Average CTR:** {analytics['avg_ctr']*100:.2f}%")
    else:
        md.append("*No analytics data available yet (videos may be < 48h old)*")
    md.append("")

    # Top videos
    if top_videos:
        md.append("### Top 5 Videos by Views")
        md.append("")
        md.append("| Streamer | Title | Views | Avg View % | YouTube ID |")
        md.append("|----------|-------|-------|------------|------------|")
        for row in top_videos:
            title = row["title"][:50] + "..." if len(row["title"]) > 50 else row["title"]
            views = row["yt_views"]
            view_pct = f"{row['yt_avg_view_percentage']:.1f}%" if row["yt_avg_view_percentage"] else "N/A"
            yt_id = row["youtube_id"]
            md.append(f"| {row['streamer']} | {title} | {views:,} | {view_pct} | {yt_id} |")
        md.append("")

    return "\n".join(md)


if __name__ == "__main__":
    db_path = Path(__file__).parent.parent / "data" / "clips.db"
    summary = get_metrics_summary(str(db_path))
    print(summary)

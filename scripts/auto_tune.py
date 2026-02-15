#!/usr/bin/env python3
"""Auto-tune pipeline parameters based on YouTube analytics data.

Reads performance data from clips.db and outputs recommended config changes.
Designed to be run periodically (e.g., weekly) to optimize the pipeline.
"""
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "clips.db"
CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


def get_performance_data(conn: sqlite3.Connection, days: int = 7) -> list[dict]:
    """Get clips with analytics data from the last N days."""
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    rows = conn.execute("""
        SELECT clip_id, streamer, title, youtube_id, title_variant,
               yt_views, yt_impressions, yt_impressions_ctr,
               yt_avg_view_percentage, yt_avg_view_duration,
               duration, game_name, posted_at
        FROM clips
        WHERE youtube_id IS NOT NULL
          AND yt_views IS NOT NULL
          AND posted_at >= ?
        ORDER BY yt_views DESC
    """, (cutoff,)).fetchall()
    
    cols = ["clip_id", "streamer", "title", "youtube_id", "title_variant",
            "yt_views", "yt_impressions", "yt_impressions_ctr",
            "yt_avg_view_percentage", "yt_avg_view_duration",
            "duration", "game_name", "posted_at"]
    return [dict(zip(cols, row)) for row in rows]


def analyze_title_variants(data: list[dict]) -> dict:
    """Compare performance of different title optimization strategies."""
    variants = {}
    for clip in data:
        variant = clip.get("title_variant") or "none"
        # Normalize composite variants
        base = "optimized" if "optimized" in variant else ("template" if "template" in variant else "original")
        if base not in variants:
            variants[base] = {"count": 0, "total_views": 0, "total_ctr": 0, "total_retention": 0}
        v = variants[base]
        v["count"] += 1
        v["total_views"] += clip.get("yt_views") or 0
        if clip.get("yt_impressions_ctr"):
            v["total_ctr"] += clip["yt_impressions_ctr"]
        if clip.get("yt_avg_view_percentage"):
            v["total_retention"] += clip["yt_avg_view_percentage"]
    
    results = {}
    for variant, stats in variants.items():
        n = stats["count"]
        results[variant] = {
            "count": n,
            "avg_views": stats["total_views"] / n if n else 0,
            "avg_ctr": stats["total_ctr"] / n if n else 0,
            "avg_retention": stats["total_retention"] / n if n else 0,
        }
    return results


def analyze_duration_performance(data: list[dict]) -> dict:
    """Find optimal clip duration based on retention data."""
    buckets = {"short_0_15": [], "medium_15_30": [], "long_30_60": []}
    for clip in data:
        dur = clip.get("duration") or 0
        retention = clip.get("yt_avg_view_percentage") or 0
        views = clip.get("yt_views") or 0
        entry = {"retention": retention, "views": views, "duration": dur}
        if dur <= 15:
            buckets["short_0_15"].append(entry)
        elif dur <= 30:
            buckets["medium_15_30"].append(entry)
        else:
            buckets["long_30_60"].append(entry)
    
    results = {}
    for bucket, clips in buckets.items():
        n = len(clips)
        if n == 0:
            results[bucket] = {"count": 0, "avg_retention": 0, "avg_views": 0}
            continue
        results[bucket] = {
            "count": n,
            "avg_retention": sum(c["retention"] for c in clips) / n,
            "avg_views": sum(c["views"] for c in clips) / n,
        }
    return results


def analyze_streamer_performance(data: list[dict]) -> dict:
    """Rank streamers by average performance."""
    streamers = {}
    for clip in data:
        name = clip.get("streamer", "unknown")
        if name not in streamers:
            streamers[name] = {"count": 0, "total_views": 0, "total_retention": 0}
        s = streamers[name]
        s["count"] += 1
        s["total_views"] += clip.get("yt_views") or 0
        if clip.get("yt_avg_view_percentage"):
            s["total_retention"] += clip["yt_avg_view_percentage"]
    
    results = {}
    for name, stats in streamers.items():
        n = stats["count"]
        results[name] = {
            "count": n,
            "avg_views": stats["total_views"] / n if n else 0,
            "avg_retention": stats["total_retention"] / n if n else 0,
        }
    return results


def generate_recommendations(title_analysis: dict, duration_analysis: dict, streamer_analysis: dict) -> list[str]:
    """Generate actionable config recommendations."""
    recs = []
    
    # Title variant recommendations
    if title_analysis:
        best = max(title_analysis.items(), key=lambda x: x[1]["avg_views"])
        if best[1]["count"] >= 3:
            recs.append(f"Best performing title variant: '{best[0]}' (avg {best[1]['avg_views']:.0f} views)")
    
    # Duration recommendations
    dur = duration_analysis
    best_dur = max(dur.items(), key=lambda x: x[1]["avg_retention"] if x[1]["count"] > 0 else 0)
    if best_dur[1]["count"] >= 2:
        recs.append(f"Best retention by duration: '{best_dur[0]}' ({best_dur[1]['avg_retention']:.1f}% avg retention)")
    
    # Streamer recommendations
    if streamer_analysis:
        ranked = sorted(streamer_analysis.items(), key=lambda x: x[1]["avg_views"], reverse=True)
        for name, stats in ranked:
            if stats["count"] >= 2:
                recs.append(f"Streamer '{name}': {stats['avg_views']:.0f} avg views, {stats['avg_retention']:.1f}% retention ({stats['count']} clips)")
    
    return recs


def main():
    if not DB_PATH.exists():
        print("No database found at", DB_PATH)
        sys.exit(1)
    
    conn = sqlite3.connect(str(DB_PATH))
    data = get_performance_data(conn, days=30)  # Look back 30 days
    
    print("# Auto-Tune Report")
    print(f"\n**Generated:** {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"**Clips with analytics:** {len(data)}")
    
    if not data:
        print("\n⚠️ No analytics data available yet. Videos need 48+ hours to accumulate data.")
        print("Run again after videos have been live for a few days.")
        conn.close()
        return
    
    title_analysis = analyze_title_variants(data)
    duration_analysis = analyze_duration_performance(data)
    streamer_analysis = analyze_streamer_performance(data)
    
    print("\n## Title Variant Performance")
    for variant, stats in sorted(title_analysis.items(), key=lambda x: x[1]["avg_views"], reverse=True):
        print(f"- **{variant}**: {stats['count']} clips, {stats['avg_views']:.0f} avg views, {stats['avg_ctr']:.2%} CTR, {stats['avg_retention']:.1f}% retention")
    
    print("\n## Duration Performance")
    for bucket, stats in duration_analysis.items():
        if stats["count"] > 0:
            print(f"- **{bucket}**: {stats['count']} clips, {stats['avg_views']:.0f} avg views, {stats['avg_retention']:.1f}% retention")
    
    print("\n## Streamer Performance")
    for name, stats in sorted(streamer_analysis.items(), key=lambda x: x[1]["avg_views"], reverse=True):
        print(f"- **{name}**: {stats['count']} clips, {stats['avg_views']:.0f} avg views, {stats['avg_retention']:.1f}% retention")
    
    recs = generate_recommendations(title_analysis, duration_analysis, streamer_analysis)
    if recs:
        print("\n## Recommendations")
        for r in recs:
            print(f"- {r}")
    
    # Output machine-readable summary
    summary = {
        "timestamp": datetime.now(UTC).isoformat(),
        "clips_analyzed": len(data),
        "title_variants": title_analysis,
        "duration_buckets": duration_analysis,
        "streamers": streamer_analysis,
        "recommendations": recs,
    }
    
    output_path = Path(__file__).parent.parent / "data" / "auto_tune_report.json"
    output_path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\n📊 Full report saved to {output_path}")
    
    conn.close()


if __name__ == "__main__":
    main()

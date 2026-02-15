#!/usr/bin/env python3
"""Self-improvement loop for the twitch-to-shorts pipeline.

Analyzes performance data and automatically updates config.yaml with
data-driven optimizations. Designed to run weekly via cron.

Changes are logged to data/improvement_log.json for auditability.
"""
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "clips.db"
CONFIG_PATH = ROOT / "config.yaml"
LOG_PATH = ROOT / "data" / "improvement_log.json"


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def save_config(config: dict) -> None:
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


def load_improvement_log() -> list[dict]:
    if LOG_PATH.exists():
        return json.loads(LOG_PATH.read_text())
    return []


def save_improvement_log(log: list[dict]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(json.dumps(log, indent=2, default=str))


def get_clips_with_analytics(conn: sqlite3.Connection, days: int = 14) -> list[dict]:
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    rows = conn.execute("""
        SELECT clip_id, streamer, title, youtube_id, title_variant,
               yt_views, yt_impressions, yt_impressions_ctr,
               yt_avg_view_percentage, yt_avg_view_duration,
               duration, game_name, posted_at
        FROM clips
        WHERE youtube_id IS NOT NULL
          AND posted_at >= ?
        ORDER BY posted_at DESC
    """, (cutoff,)).fetchall()

    cols = ["clip_id", "streamer", "title", "youtube_id", "title_variant",
            "yt_views", "yt_impressions", "yt_impressions_ctr",
            "yt_avg_view_percentage", "yt_avg_view_duration",
            "duration", "game_name", "posted_at"]
    return [dict(zip(cols, row)) for row in rows]


def analyze_and_recommend(clips: list[dict], config: dict) -> list[dict]:
    """Analyze clip performance and return config change recommendations.
    
    Each recommendation is a dict with:
        - key: config path (e.g., "pipeline.optimal_duration_max")
        - old_value: current value
        - new_value: recommended value
        - reason: why this change
        - confidence: low/medium/high
    """
    recommendations = []
    pipeline = config.get("pipeline", {})

    # Only make recommendations if we have enough data
    clips_with_views = [c for c in clips if c.get("yt_views") is not None]
    if len(clips_with_views) < 5:
        return recommendations

    # --- Duration analysis ---
    clips_with_retention = [c for c in clips_with_views 
                           if c.get("yt_avg_view_percentage") is not None 
                           and c.get("duration")]
    if len(clips_with_retention) >= 3:
        # Find optimal duration range based on retention
        short_clips = [c for c in clips_with_retention if c["duration"] <= 15]
        medium_clips = [c for c in clips_with_retention if 15 < c["duration"] <= 30]
        long_clips = [c for c in clips_with_retention if c["duration"] > 30]

        best_bucket = None
        best_retention = 0
        for name, bucket in [("short", short_clips), ("medium", medium_clips), ("long", long_clips)]:
            if len(bucket) >= 2:
                avg_ret = sum(c["yt_avg_view_percentage"] for c in bucket) / len(bucket)
                if avg_ret > best_retention:
                    best_retention = avg_ret
                    best_bucket = name

        if best_bucket == "short" and pipeline.get("optimal_duration_max", 15) > 15:
            recommendations.append({
                "key": "pipeline.optimal_duration_max",
                "old_value": pipeline.get("optimal_duration_max"),
                "new_value": 15,
                "reason": f"Short clips (<15s) have {best_retention:.1f}% avg retention, outperforming longer clips",
                "confidence": "medium",
            })
        elif best_bucket == "medium" and pipeline.get("optimal_duration_max", 15) < 30:
            recommendations.append({
                "key": "pipeline.optimal_duration_max",
                "old_value": pipeline.get("optimal_duration_max"),
                "new_value": 30,
                "reason": f"Medium clips (15-30s) have {best_retention:.1f}% avg retention",
                "confidence": "medium",
            })

    # --- View count threshold ---
    avg_views = sum(c.get("yt_views", 0) for c in clips_with_views) / len(clips_with_views)
    # If top clips have 10x+ more views, raise the minimum view count to filter better
    if len(clips_with_views) >= 5:
        sorted_by_views = sorted(clips_with_views, key=lambda c: c.get("yt_views", 0), reverse=True)
        top_half_avg = sum(c.get("yt_views", 0) for c in sorted_by_views[:len(sorted_by_views)//2]) / max(len(sorted_by_views)//2, 1)
        bottom_half_avg = sum(c.get("yt_views", 0) for c in sorted_by_views[len(sorted_by_views)//2:]) / max(len(sorted_by_views) - len(sorted_by_views)//2, 1)
        
        if top_half_avg > 0 and bottom_half_avg > 0:
            ratio = top_half_avg / bottom_half_avg
            if ratio > 5:
                # Top clips significantly outperform — consider raising min_view_count
                current_min = pipeline.get("min_view_count", 50)
                # Suggest raising by 25% but cap at 500
                new_min = min(int(current_min * 1.25), 500)
                if new_min > current_min:
                    recommendations.append({
                        "key": "pipeline.min_view_count",
                        "old_value": current_min,
                        "new_value": new_min,
                        "reason": f"Top half of clips get {ratio:.1f}x more views. Raising threshold to filter low-performers.",
                        "confidence": "low",
                    })

    # --- Title variant analysis ---
    variant_stats = {}
    for c in clips_with_views:
        variant = c.get("title_variant") or "none"
        base = "optimized" if "optimized" in variant else ("template" if "template" in variant else "original")
        if base not in variant_stats:
            variant_stats[base] = {"count": 0, "total_views": 0}
        variant_stats[base]["count"] += 1
        variant_stats[base]["total_views"] += c.get("yt_views", 0)

    # If optimized titles significantly outperform, increase title_quality_weight
    if "optimized" in variant_stats and "template" in variant_stats:
        opt = variant_stats["optimized"]
        tmpl = variant_stats["template"]
        if opt["count"] >= 3 and tmpl["count"] >= 3:
            opt_avg = opt["total_views"] / opt["count"]
            tmpl_avg = tmpl["total_views"] / tmpl["count"]
            if opt_avg > tmpl_avg * 1.5:
                current_weight = pipeline.get("title_quality_weight", 0.05)
                new_weight = min(current_weight + 0.05, 0.3)
                if new_weight > current_weight:
                    recommendations.append({
                        "key": "pipeline.title_quality_weight",
                        "old_value": current_weight,
                        "new_value": new_weight,
                        "reason": f"Optimized titles avg {opt_avg:.0f} views vs template {tmpl_avg:.0f} views",
                        "confidence": "medium",
                    })

    # --- Streamer performance ---
    streamer_stats = {}
    for c in clips_with_views:
        name = c.get("streamer", "unknown")
        if name not in streamer_stats:
            streamer_stats[name] = {"count": 0, "total_views": 0}
        streamer_stats[name]["count"] += 1
        streamer_stats[name]["total_views"] += c.get("yt_views", 0)

    # Log streamer performance for awareness (don't auto-remove streamers)
    for name, stats in streamer_stats.items():
        if stats["count"] >= 3 and stats["total_views"] / stats["count"] < 5:
            recommendations.append({
                "key": f"streamer.{name}.note",
                "old_value": None,
                "new_value": "underperforming",
                "reason": f"{name} averaging {stats['total_views']/stats['count']:.0f} views over {stats['count']} clips — consider replacing",
                "confidence": "low",
            })

    return recommendations


def apply_recommendations(config: dict, recommendations: list[dict], auto_apply_confidence: str = "medium") -> tuple[dict, list[dict]]:
    """Apply recommendations to config. Returns (updated_config, applied_changes).
    
    Only applies changes at or above the auto_apply_confidence level.
    Confidence hierarchy: high > medium > low
    """
    confidence_levels = {"high": 3, "medium": 2, "low": 1}
    min_level = confidence_levels.get(auto_apply_confidence, 2)

    applied = []
    for rec in recommendations:
        level = confidence_levels.get(rec["confidence"], 0)
        if level < min_level:
            continue

        # Only apply pipeline.* keys
        key = rec["key"]
        if not key.startswith("pipeline."):
            continue

        param = key.split(".", 1)[1]
        if "pipeline" not in config:
            config["pipeline"] = {}
        
        config["pipeline"][param] = rec["new_value"]
        applied.append(rec)

    return config, applied


def main():
    if not DB_PATH.exists():
        print("No database found at", DB_PATH)
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    config = load_config()

    clips = get_clips_with_analytics(conn, days=14)
    print(f"Analyzing {len(clips)} clips from the last 14 days...")

    clips_with_views = [c for c in clips if c.get("yt_views") is not None]
    print(f"  {len(clips_with_views)} clips have YouTube analytics data")

    recommendations = analyze_and_recommend(clips, config)

    if not recommendations:
        print("No recommendations at this time. Need more data or everything looks good.")
        conn.close()
        return

    print(f"\n## {len(recommendations)} Recommendations:")
    for r in recommendations:
        print(f"  [{r['confidence'].upper()}] {r['key']}: {r['old_value']} → {r['new_value']}")
        print(f"    Reason: {r['reason']}")

    # Auto-apply medium+ confidence changes
    config, applied = apply_recommendations(config, recommendations, auto_apply_confidence="medium")

    if applied:
        save_config(config)
        print(f"\n✅ Applied {len(applied)} changes to config.yaml")
        for a in applied:
            print(f"  - {a['key']}: {a['old_value']} → {a['new_value']}")
    else:
        print("\nNo changes met the auto-apply threshold. Manual review needed.")

    # Log everything
    log = load_improvement_log()
    log.append({
        "timestamp": datetime.now(UTC).isoformat(),
        "clips_analyzed": len(clips),
        "clips_with_analytics": len(clips_with_views),
        "recommendations": recommendations,
        "applied": applied,
    })
    save_improvement_log(log)
    print(f"\n📊 Full log saved to {LOG_PATH}")

    conn.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Check streamer health: clip availability, activity, and recommend replacements.

Designed to run weekly to ensure the pipeline always has active streamers.
Usage: .venv/bin/python scripts/streamer_health.py
"""
import json
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
os.chdir(Path(__file__).parent.parent)

# Load env
from dotenv import load_dotenv
load_dotenv()

from src.twitch_client import TwitchClient

CONFIG_PATH = Path("config.yaml")
REPORT_PATH = Path("data/streamer_health.json")

# Backup candidates to try when a streamer goes inactive
BACKUP_STREAMERS = {
    "Lirik": "23161357",
    "tarik": "36340781",
    "TimTheTatman": "36769016",
    "Shroud": "37402112",
    "KaiCenat": "641972806",
    "aceu": "430862664",
    "TenZ": "170547759",
    "s1mple": "86340416",
}


def check_streamer_clips(tc: TwitchClient, name: str, twitch_id: str, hours: int = 168) -> dict:
    """Check a streamer's clip availability over the last N hours."""
    clips = tc.fetch_clips(twitch_id, lookback_hours=hours)
    total_views = sum(c.view_count for c in clips) if clips else 0
    avg_views = total_views // max(len(clips), 1)
    return {
        "name": name,
        "twitch_id": twitch_id,
        "clip_count": len(clips),
        "total_views": total_views,
        "avg_views": avg_views,
        "status": "active" if len(clips) > 10 else ("low" if len(clips) > 0 else "inactive"),
    }


def main():
    config = yaml.safe_load(CONFIG_PATH.read_text())
    streamers = config.get("streamers", [])

    tc = TwitchClient(os.environ["TWITCH_CLIENT_ID"], os.environ["TWITCH_CLIENT_SECRET"])

    print("# Streamer Health Report\n")

    # Check configured streamers
    results = []
    inactive = []
    for s in streamers:
        info = check_streamer_clips(tc, s["name"], s["twitch_id"])
        results.append(info)
        status_icon = {"active": "✅", "low": "⚠️", "inactive": "❌"}[info["status"]]
        print(f"{status_icon} {info['name']}: {info['clip_count']} clips, {info['total_views']:,} views ({info['status']})")
        if info["status"] == "inactive":
            inactive.append(info["name"])

    # If there are inactive streamers, check backups
    if inactive:
        print(f"\n## Inactive Streamers: {', '.join(inactive)}")
        print("\n### Backup Candidates:")

        configured_ids = {s["twitch_id"] for s in streamers}
        candidates = []
        for name, tid in BACKUP_STREAMERS.items():
            if tid in configured_ids:
                continue
            info = check_streamer_clips(tc, name, tid)
            candidates.append(info)

        candidates.sort(key=lambda x: x["total_views"], reverse=True)
        for c in candidates[:5]:
            status_icon = {"active": "✅", "low": "⚠️", "inactive": "❌"}[c["status"]]
            print(f"  {status_icon} {c['name']}: {c['clip_count']} clips, {c['total_views']:,} views")

        # Recommend top active candidates
        active_candidates = [c for c in candidates if c["status"] == "active"]
        if active_candidates:
            print(f"\n### Recommended replacements for {', '.join(inactive)}:")
            for i, replacement in enumerate(active_candidates[:len(inactive)]):
                print(f"  → Replace with {replacement['name']} ({replacement['clip_count']} clips, {replacement['avg_views']} avg views)")

    # Save report
    report = {
        "configured": results,
        "inactive": inactive,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2))
    print(f"\n📊 Report saved to {REPORT_PATH}")


if __name__ == "__main__":
    main()

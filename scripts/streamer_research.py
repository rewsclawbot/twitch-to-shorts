#!/usr/bin/env python3
"""Analyze Twitch streamer clip data to find optimal candidates for YouTube Shorts."""

import os, requests, json
from datetime import datetime, timedelta, timezone
from collections import Counter

CLIENT_ID = os.environ["TWITCH_CLIENT_ID"]
CLIENT_SECRET = os.environ["TWITCH_CLIENT_SECRET"]

# Get OAuth token
token_resp = requests.post("https://id.twitch.tv/oauth2/token", params={
    "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "grant_type": "client_credentials"
})
TOKEN = token_resp.json()["access_token"]
HEADERS = {"Client-ID": CLIENT_ID, "Authorization": f"Bearer {TOKEN}"}

STREAMERS = [
    "TheBurntPeanut", "Clix", "iiTzTimmy", "tarik", "aceu",
    "xQc", "KaiCenat", "IShowSpeed", "Shroud", "TenZ", "s1mple",
    "Jynxzi", "Lacy", "Sketch", "Summit1g", "Lirik",
    "CaseOh_", "BruceDropEmOff"
]

seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

# Resolve all broadcaster IDs in batches of 100
def get_user_ids(logins):
    params = [("login", l) for l in logins]
    r = requests.get("https://api.twitch.tv/helix/users", headers=HEADERS, params=params)
    return {u["login"].lower(): u["id"] for u in r.json().get("data", [])}

user_map = get_user_ids(STREAMERS)
print(f"Resolved {len(user_map)} users: {list(user_map.keys())}")

def fetch_clips(broadcaster_id):
    clips = []
    cursor = None
    for _ in range(5):  # up to 500 clips
        params = {"broadcaster_id": broadcaster_id, "first": 100, "started_at": seven_days_ago}
        if cursor:
            params["after"] = cursor
        r = requests.get("https://api.twitch.tv/helix/clips", headers=HEADERS, params=params)
        data = r.json()
        batch = data.get("data", [])
        clips.extend(batch)
        cursor = data.get("pagination", {}).get("cursor")
        if not cursor or len(batch) < 100:
            break
    return clips

results = []
for name in STREAMERS:
    key = name.lower()
    if key not in user_map:
        print(f"  SKIP {name} — not found")
        results.append({"name": name, "found": False})
        continue
    
    bid = user_map[key]
    clips = fetch_clips(bid)
    
    if not clips:
        print(f"  {name}: 0 clips")
        results.append({"name": name, "found": True, "clip_count": 0})
        continue
    
    views = [c["view_count"] for c in clips]
    durations = [c["duration"] for c in clips]
    games = [c.get("game_id", "unknown") for c in clips]
    game_counts = Counter(games)
    
    # Resolve top game name
    top_game_id = game_counts.most_common(1)[0][0]
    try:
        gr = requests.get("https://api.twitch.tv/helix/games", headers=HEADERS, params={"id": top_game_id})
        top_game_name = gr.json()["data"][0]["name"] if gr.json().get("data") else top_game_id
    except:
        top_game_name = top_game_id
    
    unique_games = len(set(games))
    under_15 = sum(1 for d in durations if d < 15)
    between_15_30 = sum(1 for d in durations if 15 <= d <= 30)
    over_30 = sum(1 for d in durations if d > 30)
    
    entry = {
        "name": name, "found": True,
        "clip_count": len(clips),
        "avg_views": round(sum(views) / len(views), 1),
        "max_views": max(views),
        "total_views": sum(views),
        "top_game": top_game_name,
        "unique_games": unique_games,
        "avg_duration": round(sum(durations) / len(durations), 1),
        "under_15s": under_15,
        "between_15_30s": between_15_30,
        "over_30s": over_30,
        "pct_shorts_ready": round((under_15 + between_15_30) / len(clips) * 100, 1),
    }
    results.append(entry)
    print(f"  {name}: {entry['clip_count']} clips, avg {entry['avg_views']} views, max {entry['max_views']}, top game: {top_game_name}")

# Score and rank
def score(r):
    if not r.get("clip_count"):
        return 0
    # Weighted scoring
    volume = min(r["clip_count"] / 500, 1.0) * 25        # 25pts for clip volume
    avg_v = min(r["avg_views"] / 1000, 1.0) * 25          # 25pts for avg views
    peak = min(r["max_views"] / 50000, 1.0) * 15           # 15pts for peak virality
    diversity = min(r["unique_games"] / 5, 1.0) * 10       # 10pts for game diversity
    shorts_fit = (r["pct_shorts_ready"] / 100) * 25        # 25pts for shorts-ready %
    return round(volume + avg_v + peak + diversity + shorts_fit, 1)

for r in results:
    r["score"] = score(r)

ranked = sorted(results, key=lambda x: x.get("score", 0), reverse=True)

# Generate markdown report
lines = [
    "# Streamer Research — YouTube Shorts Clip Potential",
    f"\n**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')} CST",
    f"**Period:** Last 7 days (since {seven_days_ago[:10]})",
    "\n## Scoring Methodology",
    "- **Clip Volume** (25pts): More clips = more content to choose from",
    "- **Avg Views** (25pts): Higher engagement per clip",
    "- **Peak Virality** (15pts): Potential for breakout clips",
    "- **Game Diversity** (10pts): Broader YouTube audience appeal",
    "- **Shorts-Ready %** (25pts): Clips ≤30s that fit Shorts format",
    "\n## Full Rankings\n",
    "| Rank | Streamer | Score | Clips | Avg Views | Max Views | Top Game | Games | Avg Dur | ≤15s | 15-30s | >30s | Shorts% |",
    "|------|----------|-------|-------|-----------|-----------|----------|-------|---------|------|--------|------|---------|",
]

for i, r in enumerate(ranked, 1):
    if not r.get("clip_count"):
        lines.append(f"| {i} | {r['name']} | 0 | 0 | — | — | — | — | — | — | — | — | — |")
    else:
        lines.append(f"| {i} | {r['name']} | {r['score']} | {r['clip_count']} | {r['avg_views']:,.0f} | {r['max_views']:,} | {r['top_game']} | {r['unique_games']} | {r['avg_duration']}s | {r['under_15s']} | {r['between_15_30s']} | {r['over_30s']} | {r['pct_shorts_ready']}% |")

# Top 5 recommendations (excluding TheBurntPeanut)
top5 = [r for r in ranked if r["name"] != "TheBurntPeanut" and r.get("clip_count", 0) > 0][:5]

lines.append("\n## 🏆 Top 5 Recommendations\n")
for i, r in enumerate(top5, 1):
    lines.append(f"### {i}. {r['name']} (Score: {r['score']})")
    lines.append(f"- **{r['clip_count']}** clips this week, **{r['avg_views']:,.0f}** avg views, **{r['max_views']:,}** peak")
    lines.append(f"- Top game: {r['top_game']} ({r['unique_games']} unique games)")
    lines.append(f"- {r['pct_shorts_ready']}% shorts-ready (≤30s)")
    lines.append("")

# Baseline comparison
bp = next((r for r in results if r["name"] == "TheBurntPeanut"), None)
if bp and bp.get("clip_count"):
    lines.append(f"\n## 📊 Baseline: TheBurntPeanut (Score: {bp['score']})")
    lines.append(f"- {bp['clip_count']} clips, {bp['avg_views']:,.0f} avg views, {bp['max_views']:,} peak")
    lines.append(f"- {bp['pct_shorts_ready']}% shorts-ready")

lines.append("\n## Notes")
lines.append("- Clip counts capped at 500 per streamer (API pagination limit used)")
lines.append("- View counts are lifetime views on each clip, not just from the 7-day window")
lines.append("- 'Shorts-ready' = clips ≤30s; actual usability depends on content (action vs talking)")
lines.append("- Streamers not found on Twitch are listed with score 0")

report = "\n".join(lines)
os.makedirs("/Users/rew/Projects/twitch-to-shorts-claw/data", exist_ok=True)
with open("/Users/rew/Projects/twitch-to-shorts-claw/data/streamer-research.md", "w") as f:
    f.write(report)

print("\n" + report)

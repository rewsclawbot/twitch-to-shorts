# Local Migration Plan: Twitch-to-Shorts Pipeline

*Generated 2026-02-14 by Claw — for Rew to review in the morning*

---

## Current State: GitHub Actions

### How It Works Today
The pipeline runs on GitHub Actions via `.github/workflows/pipeline.yml`:

- **Schedule:** Every 4 hours (`17 2/4 * * *` — at :17 past 2am, 6am, 10am, 2pm, 6pm, 10pm UTC)
- **Also:** Manual trigger via `workflow_dispatch`
- **Runner:** `ubuntu-latest` (ephemeral VM, fresh every run)
- **Timeout:** 60 minutes per run
- **Concurrency:** One run at a time (pipeline lock)

### Pipeline Steps (per run)
1. Checkout code, install Python 3.12 + ffmpeg + pip deps
2. Restore YouTube/Instagram OAuth credentials from GitHub Secrets (base64-encoded)
3. Restore SQLite database from GitHub Actions cache (with artifact fallback)
4. Run `python main.py` — the actual pipeline:
   - Fetch top clips from Twitch API for each configured streamer
   - Filter, rank, and deduplicate against DB
   - Download clips via yt-dlp
   - Crop to vertical (9:16) with facecam overlay
   - Optional: burn-in captions (requires Deepgram API key)
   - Upload to YouTube as Shorts
   - Optional: upload to Instagram as Reels
   - Sync YouTube Analytics metrics
   - Optional: generate/set thumbnails
5. Save refreshed OAuth tokens back to GitHub Secrets (via `gh secret set`)
6. Checkpoint + save SQLite DB to cache + upload as artifact
7. Cleanup credentials

### External Dependencies
| Service | What For | Credentials |
|---------|----------|-------------|
| **Twitch API** | Fetch clips, resolve game names | `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` (env vars) |
| **YouTube Data API** | Upload shorts, set thumbnails, check duplicates | OAuth client secret + per-streamer token JSON |
| **YouTube Analytics/Reporting API** | Fetch video metrics post-upload | Same YouTube OAuth |
| **Instagram Graph API** | Upload Reels (optional, currently disabled) | Per-streamer token JSON |
| **Deepgram** | Speech-to-text for captions (optional) | `DEEPGRAM_API_KEY` env var |
| **ffmpeg** | Video processing (crop, overlay, captions) | System binary |
| **yt-dlp** | Download Twitch clips | pip package |
| **SQLite** | Track clips, uploads, analytics, dedup | Local file (`data/clips.db`) |

### Pain Points with GitHub Actions
- **Ephemeral environment:** Every run reinstalls everything, restores DB from cache
- **Credential gymnastics:** Secrets stored as base64, decoded each run, re-encoded and saved back
- **DB persistence is fragile:** Cache can expire/evict; artifact fallback adds complexity
- **No persistent storage:** Can't keep video files, logs, or intermediate data between runs
- **6 runs/day max** at the current schedule (limited by Actions minutes and quota)
- **No GPU:** `DISABLE_GPU_ENCODE=1` is hardcoded because Actions runners have no GPU
- **Blind execution:** No real-time monitoring — you check after the fact
- **Token refresh race:** If OAuth token refreshes mid-run, it must be saved back to Secrets atomically

---

## Local Migration Plan

### What You Already Have (Mac Mini)
- ✅ Python 3.12 (via pyenv/brew)
- ✅ Virtual environment at `.venv/`
- ✅ YouTube OAuth credentials on disk (`credentials/client_secret.json`, `credentials/claw_youtube.json`)
- ✅ SQLite DB (`data/clips.db`) — persistent, no cache dance needed
- ✅ Always-on machine (no ephemeral VMs)
- ❌ ffmpeg — **needs install** (`brew install ffmpeg`)
- ❌ Twitch API credentials — **still blocked, need from Rew**

### Step-by-Step Migration

#### Phase 1: Environment Setup (15 min)
```bash
# Install ffmpeg
brew install ffmpeg

# Verify venv has all deps
cd ~/Projects/twitch-to-shorts-claw
source .venv/bin/activate
pip install -r requirements.txt

# Create .env file for secrets
cat > .env << 'EOF'
TWITCH_CLIENT_ID=<from Rew>
TWITCH_CLIENT_SECRET=<from Rew>
# DEEPGRAM_API_KEY=<optional, for captions>
EOF
```

#### Phase 2: Local Test Run (5 min)
```bash
cd ~/Projects/twitch-to-shorts-claw
source .venv/bin/activate
python main.py --dry-run
```
This validates config, credentials, and Twitch API connectivity without uploading anything.

#### Phase 3: launchd Service (Replaces GitHub Actions Cron)

Create `~/Library/LaunchAgents/com.claw.twitch-to-shorts.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claw.twitch-to-shorts</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/rew/Projects/twitch-to-shorts-claw/scripts/run-pipeline.sh</string>
    </array>
    <key>StartInterval</key>
    <integer>14400</integer> <!-- Every 4 hours (same as current GHA schedule) -->
    <key>WorkingDirectory</key>
    <string>/Users/rew/Projects/twitch-to-shorts-claw</string>
    <key>StandardOutPath</key>
    <string>/Users/rew/Projects/twitch-to-shorts-claw/data/launchd-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/rew/Projects/twitch-to-shorts-claw/data/launchd-stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>RunAtLoad</key>
    <true/> <!-- Run immediately when loaded -->
    <key>Nice</key>
    <integer>10</integer> <!-- Lower priority so it doesn't hog CPU -->
</dict>
</plist>
```

Runner script `scripts/run-pipeline.sh`:
```bash
#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."
source .venv/bin/activate

# Source secrets
set -a; source .env; set +a

# Run with local trigger tag
PIPELINE_TRIGGER=local python main.py 2>&1 | tee -a data/pipeline.log

# Notify Claw on completion
EXIT_CODE=$?
if [ $EXIT_CODE -eq 0 ]; then
    openclaw system event --text "Pipeline run complete. Check data/pipeline.log for details." --mode now
else
    openclaw system event --text "⚠️ Pipeline FAILED (exit $EXIT_CODE). Check data/pipeline.log." --mode now
fi
```

#### Phase 4: Enable & Verify
```bash
chmod +x scripts/run-pipeline.sh
launchctl load ~/Library/LaunchAgents/com.claw.twitch-to-shorts.plist

# Verify it's scheduled
launchctl list | grep twitch

# Manual trigger anytime
launchctl kickstart -k gui/$(id -u)/com.claw.twitch-to-shorts
```

#### Phase 5: Disable GitHub Actions (After Confirming Local Works)
Either delete the workflow file or add `if: false` to the job:
```yaml
jobs:
  run-pipeline:
    if: false  # Migrated to local Mac mini
```

---

## Unlocked Capabilities

This is where it gets interesting. Moving local doesn't just replicate GHA — it enables things that were impossible before:

### 1. Claw-Managed Pipeline (AI-in-the-Loop)
Instead of a dumb cron, **I (Claw) can orchestrate the pipeline**:
- Monitor each run in real-time via the existing Qwen watchdog infrastructure
- React to failures immediately (retry, adjust, notify Rew)
- Make intelligent scheduling decisions: "Twitch is popping off right now, run early"
- Review uploaded videos and track performance over time
- **Auto-tune parameters** based on analytics (clip selection weights, upload timing, etc.)

### 2. Local LLM Integration (Qwen on LM Studio)
You already have Qwen2.5-7B running on localhost:1234. We can use it for:
- **Smart title generation:** Instead of template-based titles, use LLM to craft viral-optimized titles
- **Content analysis:** Analyze clip audio/content to write better descriptions
- **Thumbnail selection:** Use vision models to pick the best frame
- **Trend detection:** Analyze which clips/games perform best and bias future selection
- Zero API cost — it's all local

### 3. Persistent State = Smarter Decisions
- **SQLite DB is always available** — no cache restore dance, no corruption risk
- Can query historical performance anytime for analytics dashboards
- Can run analytics sync MORE frequently (not locked to pipeline runs)
- **A/B test results accumulate properly** without cache eviction resetting data

### 4. Flexible Scheduling
- Run every hour instead of every 4 hours (no Actions minutes cost)
- Run on-demand when Rew says "run the pipeline now"
- Adaptive scheduling: run more during peak Twitch hours, less at night
- Chain with other local tasks (e.g., "after pipeline, generate weekly report")

### 5. GPU-Accelerated Video Processing
- Mac mini has Apple Silicon — can use hardware-accelerated encoding via `videotoolbox`
- Remove `DISABLE_GPU_ENCODE=1` flag
- Faster video processing = more clips per run
- Could even explore local whisper for captions instead of Deepgram (free, private)

### 6. Direct File Access
- Keep processed videos for review/reuse
- Build a local clip library
- No upload/download of artifacts — everything stays on disk
- Easier debugging (inspect intermediate files)

### 7. Integration with Existing Infrastructure
- **Qwen Watchdog** already monitors tmux agents — extend to monitor pipeline
- **OpenClaw cron** can trigger runs or check status
- **Mission Control dashboard** could show pipeline status alongside other metrics
- Direct notification to Rew via Telegram when interesting things happen

---

## Proposed Architecture

```
┌─────────────────────────────────────────────┐
│                  Mac Mini                    │
│                                             │
│  ┌─────────────┐    ┌──────────────────┐   │
│  │   launchd    │───▶│ run-pipeline.sh  │   │
│  │  (every 4h)  │    │                  │   │
│  └─────────────┘    │  ┌────────────┐  │   │
│                      │  │  main.py   │  │   │
│  ┌─────────────┐    │  │  (pipeline)│  │   │
│  │    Claw      │◀───│  └────────────┘  │   │
│  │  (OpenClaw)  │    │                  │   │
│  │  • monitors  │    │  ┌────────────┐  │   │
│  │  • notified  │    │  │ clips.db   │  │   │
│  │  • can trigger│   │  │ (persistent)│  │   │
│  │  • analyzes  │    │  └────────────┘  │   │
│  └─────────────┘    └──────────────────┘   │
│         │                                   │
│         ▼                                   │
│  ┌─────────────┐    ┌──────────────────┐   │
│  │ Qwen (local)│    │   credentials/   │   │
│  │ localhost:   │    │  (on-disk, safe) │   │
│  │    1234      │    └──────────────────┘   │
│  └─────────────┘                            │
└─────────────────────────────────────────────┘
         │
         ▼
  ┌──────────────┐  ┌──────────┐  ┌──────────┐
  │  Twitch API  │  │ YouTube  │  │ Instagram│
  └──────────────┘  └──────────┘  └──────────┘
```

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Mac mini goes offline | Pipeline stops | Keep GHA workflow as disabled fallback; can re-enable in 30 seconds |
| Credential files corrupted/deleted | Can't upload | Git-ignore credentials dir, back up to encrypted location |
| Pipeline crashes loop | CPU/memory waste | Lock file already prevents concurrent runs; launchd `ThrottleInterval` prevents rapid restarts |
| OAuth token expires | Auth failure | Pipeline already handles token refresh and saves updated tokens; locally this is simpler (just write to disk) |
| DB corruption | Loss of upload history | Regular SQLite backups (cron job: `sqlite3 data/clips.db ".backup data/clips.db.bak"`) |
| ffmpeg version mismatch | Processing failures | Pin ffmpeg via `brew pin ffmpeg` after confirming working version |
| Disk fills up | Pipeline fails | `clean_stale_tmp()` already runs; add monitoring for disk usage |

---

## What's Needed from Rew

1. **Twitch API credentials** — `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET` (this has been the blocker for weeks)
2. **Approve `brew install ffmpeg`** — I can do this right now if you say go
3. **Decision on schedule** — Keep 4h interval, or go more aggressive?
4. **Claude Code login** — auth expired, need `claude /login` in terminal (not blocking for this plan, but needed for future dev work)

---

## Suggested Rollout

1. **Tonight/Tomorrow:** Install ffmpeg, get Twitch creds from Rew
2. **Day 1:** Dry-run test locally, verify all credentials work
3. **Day 1:** Set up launchd service, run first real pipeline locally
4. **Day 2-3:** Monitor a few runs, compare output quality to GHA runs
5. **Day 3:** Disable GHA workflow, local is primary
6. **Week 2:** Start building Claw-managed features (smart scheduling, LLM titles, analytics dashboard)

---

## TL;DR

Moving the pipeline local is a no-brainer. The Mac mini is always on, has the credentials on disk already, and eliminates all the ephemeral-environment pain of GitHub Actions. The real win isn't just replicating what GHA does — it's unlocking AI-in-the-loop pipeline management, local LLM integration, GPU-accelerated processing, and flexible scheduling. The migration itself is straightforward (~30 min of setup once we have Twitch creds).

The only blocker remains: **Twitch API credentials.**

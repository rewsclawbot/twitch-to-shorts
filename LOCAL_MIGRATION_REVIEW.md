# Senior Engineering Review: LOCAL_MIGRATION_PLAN.md

**Reviewer**: Claude (Sonnet 4.5)
**Review Date**: 2026-02-14
**Documents Reviewed**: LOCAL_MIGRATION_PLAN.md, full codebase analysis
**Verdict**: ⚠️ **DO NOT EXECUTE** - Critical technical errors and missing operational requirements

---

## Executive Summary

The migration plan's **strategic direction is correct** (move to local Mac mini), but the **technical implementation has critical flaws** that would cause immediate production failures. Most critically: **GPU encoding will completely fail on Mac mini** due to incorrect codec assumptions.

**Key Issues**:
- 🔴 **BLOCKER**: GPU encoding configured for NVIDIA CUDA, not Apple VideoToolbox - won't work on Mac
- 🔴 **BLOCKER**: Credential file name mismatch will cause auth failures
- 🔴 **CRITICAL**: Instagram upload depends on undocumented GitHub CLI requirement
- 🟡 **HIGH**: No operational monitoring, database backups, or rollback procedure
- 🟢 **QUICK WINS**: 5 immediate improvements identified

---

## 🔴 Critical Technical Errors

### 1. **GPU Encoding Codec Mismatch** ⚠️ MOST CRITICAL ISSUE

**Plan Claims** (LOCAL_MIGRATION_PLAN.md line 214):
> "Mac mini has Apple Silicon — can use hardware-accelerated encoding via `videotoolbox`"

**Reality from Code** (`src/video_processor.py:363-387`):
```python
# GPU encoding uses NVIDIA CUDA codecs
if not skip_gpu:
    gpu_args = [
        "-hwaccel", "cuda",           # ← NVIDIA hardware acceleration
        "-c:v", "h264_nvenc",         # ← NVIDIA encoder (NOT VideoToolbox)
        "-preset", "p4",
        "-cq", "23"
    ]
```

**Impact**:
- GPU encoding will **fail 100% of the time** on Mac mini
- FFmpeg will error: "Unknown encoder 'h264_nvenc'" or "No CUDA-capable devices found"
- Pipeline will fall back to CPU encoding (which works, but defeats the "GPU acceleration" selling point)
- Video processing will be **slower than current GHA**, not faster

**Fix Required**:
```python
# Detect hardware platform and use appropriate encoder
if sys.platform == "darwin":  # macOS
    gpu_args = [
        "-c:v", "h264_videotoolbox",   # Apple VideoToolbox
        "-b:v", "5M",                  # Target bitrate
        "-maxrate", "6M"
    ]
else:  # Linux with NVIDIA
    gpu_args = [
        "-hwaccel", "cuda",
        "-c:v", "h264_nvenc",
        # ... existing CUDA config
    ]
```

**Why This Matters**: The plan's entire "Unlocked Capabilities #5" section is based on this false assumption. GPU acceleration is a major selling point that won't work without code changes.

---

### 2. **Credential File Name Mismatch**

**Plan References** (line 65):
> "✅ YouTube OAuth credentials on disk (`credentials/client_secret.json`, `credentials/claw_youtube.json`)"

**Config Reality** (`config.yaml:39`):
```yaml
streamers:
  - name: TheBurntPeanut
    youtube_credentials: credentials/theburntpeanut_youtube.json  # ← Doesn't exist
```

**Actual Files on Disk**:
- `credentials/claw_youtube.json` ✅ (exists, 843 bytes)
- `credentials/theburntpeanut_youtube.json` ❌ (missing)

**Impact**:
- First local run will fail with: `FileNotFoundError: credentials/theburntpeanut_youtube.json`
- Pipeline will exit before processing any clips

**Fix**: Either:
1. Rename file: `mv credentials/claw_youtube.json credentials/theburntpeanut_youtube.json`
2. Update config: Change line 39 to `credentials/claw_youtube.json`

---

### 3. **Instagram Upload Requires GitHub CLI** (UNDOCUMENTED DEPENDENCY)

**Plan Mentions** (line 44):
> "| **Instagram Graph API** | Upload Reels (optional, currently disabled) | Per-streamer token JSON |"

**Reality from Code** (`src/instagram_uploader.py:231-289`):
```python
# Instagram requires public URL hosting
# Uses GitHub CLI to create temporary releases for video hosting
gh_upload_cmd = [
    "gh", "release", "create", tag,
    "--repo", GITHUB_REPO,
    "--title", release_title,
    "--notes", release_notes,
    video_path
]
```

**Missing Dependencies**:
1. **GitHub CLI** (`gh`) must be installed and authenticated
2. **GitHub repository** must exist and be accessible (currently: `anthropics/twitch-to-shorts`)
3. **GitHub PAT** with release creation permissions
4. **Internet upload** of full video files to GitHub (bandwidth/quota concerns)

**Impact**:
- If Instagram is re-enabled without `gh` setup, uploads will fail
- Creates external dependency on GitHub infrastructure for Instagram feature
- Uses Anthropic's GitHub org without explicit authorization (line 27: `GITHUB_REPO = "anthropics/twitch-to-shorts"`)

**Security Concern**: Using `anthropics/twitch-to-shorts` as video hosting for Rew's personal content is likely unauthorized.

---

### 4. **OAuth Token Persistence Verified ✅**

**Existing Review Said**: "needs verification"

**Confirmed from Code** (`src/youtube_uploader.py:117-119`):
```python
# Tokens ARE automatically persisted to disk
with os.fdopen(os.open(credentials_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as f:
    f.write(creds.to_json())
```

**Status**: ✅ This works correctly. Refreshed tokens are written atomically with proper permissions.

---

## 🟡 Missing Operational Requirements

### 5. **No Database Backup Strategy**

**Plan Mentions** (line 275):
> "DB corruption | Loss of upload history | Regular SQLite backups (cron job: `sqlite3 data/clips.db ".backup data/clips.db.bak"`)"

**Problems**:
- Listed in "Risks" table but **not in the migration steps**
- No schedule specified (daily? hourly? after each run?)
- Backups stored in same directory as primary database (no protection against disk failure)
- No retention policy (keep how many backups?)
- No verification that backups are restorable

**Recommended Fix**:
```bash
# Daily backup to external location
cat > ~/Library/LaunchAgents/com.claw.db-backup.plist << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claw.db-backup</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>-c</string>
        <string>sqlite3 ~/Projects/twitch-to-shorts-claw/data/clips.db ".backup ~/Backups/twitch-shorts/clips-$(date +%Y%m%d-%H%M%S).db"</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>3</integer>
        <key>Minute</key><integer>0</integer>
    </dict>
</dict>
</plist>
EOF
```

---

### 6. **No Log Rotation**

**Plan Creates** (lines 117-119):
```xml
<key>StandardOutPath</key>
<string>/Users/rew/Projects/twitch-to-shorts-claw/data/launchd-stdout.log</string>
<key>StandardErrorPath</key>
<string>/Users/rew/Projects/twitch-to-shorts-claw/data/launchd-stderr.log</string>
```

**Problems**:
- Logs append forever (no rotation)
- Pipeline also appends to `data/pipeline.log` via `tee -a` (line 146)
- Will consume disk space unbounded
- Large log files slow down reads/searches

**Pipeline Already Has Rotation** (`main.py:38-43`):
```python
file_handler = logging.handlers.RotatingFileHandler(
    "data/pipeline.log",
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=3
)
```

**But**: This only rotates the Python logger, not launchd stdout/stderr.

**Fix**: Add newsyslog config or periodic cleanup in runner script.

---

### 7. **No Rollback Procedure**

**Plan Says** (line 169):
> "Either delete the workflow file or add `if: false` to the job"

**Problems**:
- What if local setup fails in production? How quickly can we re-enable GHA?
- What if Mac mini dies overnight? Pipeline stops until hardware is fixed.
- No documented steps for emergency GHA re-activation

**Better Approach**:
1. Keep GHA workflow file intact with `if: false`
2. Document one-line re-enable: Remove `if: false`, commit, push
3. Keep GHA secrets up-to-date even while disabled (in case of rollback)
4. Test GHA rollback monthly to ensure it still works

---

### 8. **No Health Monitoring**

**Plan Notification** (lines 148-154):
```bash
if [ $EXIT_CODE -eq 0 ]; then
    openclaw system event --text "Pipeline run complete..."
else
    openclaw system event --text "⚠️ Pipeline FAILED..."
fi
```

**Gaps**:
- **Silent failures**: If bash script crashes before trap, no notification sent
- **Process killed**: If macOS terminates process (OOM, memory pressure), exit code is lost
- **Hung processes**: If pipeline hangs (network timeout, deadlock), no alert
- **Schedule drift**: No detection if launchd stops running (plist unloaded, Mac asleep)

**Recommended Additions**:
1. **Heartbeat monitor**: Separate cron checks last successful run timestamp
2. **Process timeout**: Add `timeout` wrapper to kill hung runs
3. **Disk space check**: Alert if disk <10% free before starting
4. **Credential expiry check**: Warn if OAuth tokens expire within 7 days

---

## 🔧 Better Approaches

### 9. **Use OpenClaw Cron Instead of Raw launchd**

**Plan Uses**: `launchd` with manual plist configuration

**Better Option**: You already have OpenClaw's cron system

**Advantages**:
- Centralized scheduling visible in Mission Control
- Telegram notifications built-in
- Can pause/modify schedules without `launchctl` commands
- Integrates with existing Qwen watchdog infrastructure
- Model selection per cron job

**Implementation**: Add to OpenClaw crons configuration instead of creating raw launchd plist.

---

### 10. **Parallel Streamer Processing**

**Current Code** (`main.py:629-784`):
```python
for streamer in config.streamers:
    # Process each streamer sequentially
```

**Opportunity**: Each streamer is independent (separate credentials, separate upload quotas)

**Improvement**:
```python
from concurrent.futures import ThreadPoolExecutor

with ThreadPoolExecutor(max_workers=len(config.streamers)) as executor:
    futures = [executor.submit(process_streamer, s) for s in config.streamers]
    for future in futures:
        try:
            future.result()
        except Exception as e:
            log.exception("Streamer processing failed")
```

**Benefits**:
- Cut total run time by ~50% (if processing 2 streamers)
- Better utilize local CPU during I/O-bound operations (Twitch API, YouTube uploads)
- Each streamer's YouTube upload quota is independent

**Risk**: Ensure SQLite WAL mode handles concurrent writes (it should, it's designed for this).

---

### 11. **Increase Run Frequency**

**Plan Suggests** (line 207):
> "Run every hour instead of every 4 hours (no Actions minutes cost)"

**But Phase 3 Schedule** (line 113):
```xml
<key>StartInterval</key>
<integer>14400</integer> <!-- Every 4 hours (same as current GHA schedule) -->
```

**Contradiction**: Plan acknowledges the benefit but doesn't implement it.

**Recommendation**: Start with 2-hour intervals, monitor for quota issues, then optimize:
- Hours 14-22 (UTC): Every 2 hours during peak Twitch hours
- Hours 22-14 (UTC): Every 4 hours during off-peak
- Adaptive scheduling based on analytics (run more when clips perform well)

---

### 12. **Fix StartInterval Drift**

**Plan Uses** (line 113):
```xml
<key>StartInterval</key>
<integer>14400</integer> <!-- Every 4 hours from load time -->
```

**Problem**: This drifts. If Mac restarts at 3:17 AM, runs happen at 3:17, 7:17, 11:17 instead of matching GHA schedule.

**Better**: Use `StartCalendarInterval` with specific hours:
```xml
<key>StartCalendarInterval</key>
<array>
    <dict><key>Hour</key><integer>20</integer><key>Minute</key><integer>17</integer></dict>
    <dict><key>Hour</key><integer>0</integer><key>Minute</key><integer>17</integer></dict>
    <dict><key>Hour</key><integer>4</integer><key>Minute</key><integer>17</integer></dict>
    <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>17</integer></dict>
    <dict><key>Hour</key><integer>12</integer><key>Minute</key><integer>17</integer></dict>
    <dict><key>Hour</key><integer>16</integer><key>Minute</key><integer>17</integer></dict>
</array>
```
(Adjusted for CST — the GHA schedule is UTC.)

---

## 🔒 Security Concerns

### 13. **World-Readable Client Secret**

**Finding from Exploration**:
```bash
$ ls -la credentials/client_secret.json
-rw-r--r--  1 rew  staff  417 Feb 14 12:00 credentials/client_secret.json
```

**Issue**: Mode `644` means any user on the system can read YouTube OAuth client secret.

**Fix**:
```bash
chmod 600 credentials/client_secret.json
```

---

### 14. **No .env File Permissions Guidance**

**Plan Says** (lines 83-89):
```bash
cat > .env << 'EOF'
TWITCH_CLIENT_ID=<from Rew>
TWITCH_CLIENT_SECRET=<from Rew>
EOF
```

**Missing**: Set permissions on creation.

**Fix**:
```bash
touch .env
chmod 600 .env  # Before writing secrets
cat > .env << 'EOF'
...
EOF
```

---

### 15. **.gitignore Status** ✅

**Verified**: `.env` is already in `.gitignore` (line 3)
**Status**: ✅ GOOD - secrets won't be committed

---

### 16. **Anthropic GitHub Repo for Video Hosting**

**From Code** (`src/instagram_uploader.py:27`):
```python
GITHUB_REPO = "anthropics/twitch-to-shorts"
```

**Concerns**:
1. Using Anthropic's GitHub organization for Rew's personal content
2. Releases/assets consume organization storage quota
3. Requires PAT with write access to Anthropic repos
4. Not mentioned in migration plan

**Recommendation**:
- Create personal GitHub repo: `rew-personal/twitch-shorts-hosting`
- Update `GITHUB_REPO` constant
- Document this requirement in Instagram setup

---

## ✅ Quick Wins

### 17. **Install ffmpeg Immediately**

**Plan Says** (line 68):
> "❌ ffmpeg — **needs install** (`brew install ffmpeg`)"

**Issue**: Waiting for Twitch creds to install ffmpeg is unnecessary.

**Quick Win**:
```bash
brew install ffmpeg
ffmpeg -version  # Verify installation
```

Do this now. Zero risk, unblocks testing.

---

### 18. **Create .env Template with Proper Permissions**

**Quick Win**:
```bash
cd ~/Projects/twitch-to-shorts-claw
touch .env
chmod 600 .env
cat > .env << 'EOF'
# TWITCH_CLIENT_ID=your_id_here
# TWITCH_CLIENT_SECRET=your_secret_here
# DEEPGRAM_API_KEY=optional_for_captions
EOF
```

Ready for Rew to fill in when credentials arrive.

---

### 19. **Fix Credential File Permissions Now**

**Quick Win**:
```bash
chmod 600 credentials/*.json
ls -la credentials/  # Verify
```

Takes 5 seconds, eliminates security risk.

---

### 20. **Validate Python Version in Venv**

**Quick Win**:
```bash
source .venv/bin/activate
python --version  # Should output: Python 3.12.x
python -c "from datetime import UTC; print('UTC import works')"
```

Confirms venv is correctly set up for local runs.

---

### 21. **Add Python Version Check to Runner Script**

**Enhancement to** `scripts/run-pipeline.sh`:
```bash
#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."
source .venv/bin/activate

# Fail-fast if Python version is wrong
PYTHON_VERSION=$(python --version 2>&1 | cut -d' ' -f2 | cut -d'.' -f1-2)
if [ "$PYTHON_VERSION" != "3.12" ]; then
    echo "FATAL: Expected Python 3.12, got $PYTHON_VERSION" >&2
    exit 1
fi

# Rest of script...
```

---

## 📊 Risk Assessment Matrix

| Risk | Plan Assessment | Actual Severity | Mitigation Status |
|------|----------------|-----------------|-------------------|
| GPU encoding fails | Not mentioned | 🔴 **CRITICAL** | ❌ Not mitigated - requires code changes |
| Credential file mismatch | Not mentioned | 🔴 **CRITICAL** | ❌ Not mitigated - will fail immediately |
| Mac mini offline | Medium | 🟡 Medium | ✅ Mitigated - GHA fallback mentioned |
| Credential corruption | Low | 🟡 Medium | ✅ Mitigated - atomic writes implemented |
| DB corruption | Medium | 🔴 **HIGH** | ❌ Not mitigated - no backup implementation |
| OAuth expiry | Low | 🟢 Low | ✅ Mitigated - auto-refresh works |
| Disk fills up | Low | 🟡 Medium | ⚠️ Partial - cleanup exists but no monitoring |
| Schedule drift | Not mentioned | 🟡 Medium | ❌ Not mitigated - StartInterval drifts |
| Silent failures | Not mentioned | 🟡 Medium | ⚠️ Partial - openclaw notified but gaps exist |
| Instagram rate limits | Not mentioned | 🟢 Low | ✅ Mitigated - already handled in code |
| Python version mismatch | Low | 🟡 Medium | ⚠️ Partial - venv exists but no validation |

---

## 📋 Pre-Flight Checklist (Before Migration)

### Must Fix Before Any Local Runs:
- [ ] **Fix GPU encoding to use VideoToolbox** (or accept CPU-only)
- [ ] Resolve credential file name mismatch
- [ ] Install ffmpeg: `brew install ffmpeg`
- [ ] Create .env file with proper permissions (600)
- [ ] Fix client_secret.json permissions to 600
- [ ] Verify Python 3.12 in venv
- [ ] Add Python version check to runner script

### Must Have Before Production:
- [ ] Implement database backup launchd job
- [ ] Add log rotation for launchd stdout/stderr
- [ ] Document and test GHA rollback procedure
- [ ] Add heartbeat monitoring for stuck runs
- [ ] Test Mac mini sleep behavior (does launchd still run?)
- [ ] Resolve Instagram GitHub hosting dependency
- [ ] Use StartCalendarInterval instead of StartInterval

### Should Have (Quality of Life):
- [ ] Switch to OpenClaw cron instead of raw launchd
- [ ] Implement parallel streamer processing
- [ ] Add disk space monitoring
- [ ] Create dashboard for pipeline status
- [ ] Increase run frequency to 2 hours

### Nice to Have (Future Iterations):
- [ ] Local Whisper for free captions
- [ ] Qwen-based title optimization
- [ ] Analytics-driven clip selection
- [ ] Adaptive scheduling based on performance

---

## 🎯 Corrected Migration Timeline

### Phase 0: Pre-Migration (Do Now - 5 minutes)
```bash
# Install ffmpeg
brew install ffmpeg

# Fix file permissions
chmod 600 credentials/*.json

# Create .env template
touch .env && chmod 600 .env
echo "# TWITCH_CLIENT_ID=" > .env
echo "# TWITCH_CLIENT_SECRET=" >> .env

# Validate venv
source .venv/bin/activate
python --version  # Confirm 3.12.x
```

### Phase 1: Critical Fixes (After Getting Twitch Creds - 30 minutes)
- [ ] Fix credential file name issue (rename or update config)
- [ ] Fill in .env with Twitch credentials
- [ ] **Update `src/video_processor.py` to use VideoToolbox on macOS**
- [ ] Test dry-run: `python main.py --dry-run`

### Phase 2: Infrastructure Setup (1 hour)
- [ ] Create database backup launchd job
- [ ] Create run-pipeline.sh with Python version check
- [ ] Add log rotation mechanism
- [ ] Test runner script manually
- [ ] Create launchd plist (with StartCalendarInterval)

### Phase 3: Production Deployment (30 minutes)
- [ ] Load launchd plist: `launchctl load ~/Library/LaunchAgents/com.claw.twitch-to-shorts.plist`
- [ ] Verify scheduled: `launchctl list | grep twitch`
- [ ] Monitor first 3 runs manually
- [ ] Compare output quality vs GHA

### Phase 4: Stabilization (2-3 days)
- [ ] Disable GHA with `if: false`
- [ ] Run for 48 hours, monitor for issues
- [ ] Test rollback to GHA (verify still works)
- [ ] Document any new operational learnings

### Phase 5: Optimization (Week 2+)
- [ ] Implement OpenClaw cron integration
- [ ] Add parallel processing
- [ ] Increase run frequency
- [ ] Build analytics dashboard

**Total Time to Production**: 2-3 hours (assuming Twitch creds available)
**Total Time to Stable**: 1 week of monitoring

---

## 💡 Key Code Changes Required

### 1. Video Processor Platform Detection

**File**: `src/video_processor.py`
**Location**: Lines 363-387 (GPU encoding section)

**Required Change**:
```python
import sys

def _run_ffmpeg(..., gpu=False, ...):
    if gpu:
        if sys.platform == "darwin":
            # macOS: Use VideoToolbox
            gpu_args = [
                "-c:v", "h264_videotoolbox",
                "-b:v", "5M",
                "-maxrate", "6M",
                "-bufsize", "10M"
            ]
        else:
            # Linux: Use NVIDIA CUDA
            gpu_args = [
                "-hwaccel", "cuda",
                "-c:v", "h264_nvenc",
                "-preset", "p4",
                "-cq", "23"
            ]
        # ... rest of encoding logic
```

This is the **most critical code change** required for local migration.

---

## Conclusion

The migration plan identifies the right problem and proposes the right solution direction, but **cannot be executed as written** due to critical technical errors. The GPU encoding assumption is fundamentally broken, and the credential file mismatch will cause immediate failure.

**Recommended Next Steps**:
1. Fix the critical issues identified above (GPU encoding, credential mismatch)
2. Implement database backups and log rotation
3. Complete all items in "Phase 0: Pre-Migration"
4. Test corrected plan in dry-run mode
5. Re-review before production deployment

The migration is still worthwhile and will unlock significant capabilities, but needs another engineering pass before execution.

**Bottom Line**: Fix GPU encoding and credential path, then this becomes a 2-hour migration with significant upside.

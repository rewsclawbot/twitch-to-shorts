# Sprint Plan: Competitive YouTube Shorts Optimization

## Context

You're competing against another instance of this pipeline on the same Twitch channel (TheBurntPeanut). Analytics from `data/analytics-insights.md` reveal clear success patterns:

- **Sweet spot duration**: 14-31 seconds (top performers)
- **CTR > impressions**: Click-through rate drives YouTube's algorithm
- **Winning titles**: Funny quotes, curiosity hooks, game tags (e.g., "Duplication Glitch | TheBurntPeanut" - 854 views, 12% CTR)
- **Losing titles**: ALL CAPS without substance, generic descriptions

**Current pipeline gaps**:
- `title_optimizer.py` exists but is NOT integrated into `main.py` (never imported/called)
- Only supports OpenAI API (costs money), no local LLM support
- Clip scoring doesn't favor the 14-31s sweet spot specifically
- No automation - requires manual runs
- Config allows clips up to 60s (analytics show >35s underperforms)

This sprint implements 4 high-impact improvements based on analytics data.

---

## Task 1: Wire title_optimizer into Pipeline with Local Qwen LLM

**Goal**: Enable AI-powered title optimization using cost-free local Qwen at localhost:1234

### Files to Modify

#### 1.1: Add Local LLM Support to `src/title_optimizer.py`

**Location**: Lines 45-106 (function `_rewrite_title_with_llm`)

**Changes**:
1. Add environment variable checks for local LLM:
   - `LLM_BASE_URL` (e.g., "http://localhost:1234/v1")
   - `LLM_MODEL_NAME` (e.g., "qwen2.5-coder")

2. Modify line 47-49: Check for EITHER `OPENAI_API_KEY` OR `LLM_BASE_URL`
   ```python
   base_url = os.environ.get("LLM_BASE_URL")
   api_key = os.environ.get("OPENAI_API_KEY", "not-needed")

   if not base_url and not api_key:
       return None
   ```

3. Modify line 72: Dynamic model selection and base_url support
   ```python
   model_name = os.environ.get("LLM_MODEL_NAME", _LLM_MODEL)

   client_kwargs = {"api_key": api_key}
   if base_url:
       client_kwargs["base_url"] = base_url
       log.debug("Using local LLM at %s with model %s", base_url, model_name)

   client = OpenAI(**client_kwargs)

   response = client.chat.completions.create(
       model=model_name,  # Instead of hardcoded _LLM_MODEL
       # ... rest unchanged
   )
   ```

#### 1.2: Integrate title_optimizer into `main.py`

**Location**: Lines 420-470 (function `_process_single_clip`)

**Changes**:
1. Add import at top (~line 40):
   ```python
   from src.title_optimizer import optimize_title  # noqa: E402
   ```

2. Insert optimization call after line 427 (after `build_upload_title`):
   ```python
   planned_title = build_upload_title(clip, title_template, title_templates)

   # NEW: Optimize title if enabled
   if os.environ.get("TITLE_OPTIMIZER_ENABLED", "false").strip().lower() == "true":
       optimized_title = optimize_title(
           planned_title,
           streamer.name,
           clip.game_name or "",
           clip.id,
       )
       if optimized_title != planned_title:
           log.info("Title optimized for %s: '%s' -> '%s'",
                   clip.id, planned_title, optimized_title)
           planned_title = optimized_title

   cache_key = clip.channel_key or streamer.youtube_credentials or streamer.name
   existing_yt_id = check_channel_for_duplicate(yt_service, planned_title, cache_key=cache_key)
   ```

#### 1.3: Update `.env.example`

**Add**:
```bash
# Local LLM for title optimization (OpenAI-compatible endpoint)
LLM_BASE_URL=http://localhost:1234/v1
LLM_MODEL_NAME=qwen2.5-coder
TITLE_OPTIMIZER_ENABLED=false

# Alternative: OpenAI for title optimization
# OPENAI_API_KEY=sk-...
```

### Verification

1. Start Qwen at localhost:1234
2. Set in `.env`:
   ```
   LLM_BASE_URL=http://localhost:1234/v1
   LLM_MODEL_NAME=qwen2.5-coder
   TITLE_OPTIMIZER_ENABLED=true
   ```
3. Run: `python main.py --dry-run`
4. Check logs for "Title optimized for..." messages
5. Verify A/B split (50% optimized, 50% original via deterministic hash)

---

## Task 2: Tune clip_filter Scoring to Favor 14-31 Second Clips

**Goal**: Bias scoring toward the analytics-proven 14-31s sweet spot

### Files to Modify

#### 2.1: Add Duration Bonus Function to `src/clip_filter.py`

**Location**: After line 37 (after `_transform_views` function)

**Add new function**:
```python
def _duration_bonus(duration: float, optimal_min: int = 14, optimal_max: int = 31) -> float:
    """
    Return a multiplier bonus for clips in the optimal duration range.

    - Clips in [optimal_min, optimal_max]: 1.0 (baseline)
    - Clips shorter than optimal_min: linear penalty down to 0.7 at 0s
    - Clips longer than optimal_max: linear penalty down to 0.5 at 60s

    This biases scoring toward the 14-31s sweet spot found in analytics.
    """
    if optimal_min <= duration <= optimal_max:
        return 1.0
    elif duration < optimal_min:
        # Linear interpolation: 0.7 at 0s, 1.0 at optimal_min
        return 0.7 + (0.3 * (duration / optimal_min))
    else:
        # Linear interpolation: 1.0 at optimal_max, 0.5 at 60s
        overage = min(duration - optimal_max, 60 - optimal_max)
        max_overage = 60 - optimal_max
        return 1.0 - (0.5 * (overage / max_overage))
```

#### 2.2: Update `compute_score` Function

**Location**: Lines 39-56

**Modify signature** (line 39):
```python
def compute_score(
    clip: Clip,
    velocity_weight: float = 2.0,
    age_decay: str = "linear",
    view_transform: str = "linear",
    title_quality_weight: float = 0.0,
    duration_bonus_weight: float = 0.0,  # NEW
    optimal_duration_min: int = 14,      # NEW
    optimal_duration_max: int = 31,      # NEW
) -> float:
```

**Modify body** (after line 53):
```python
score = density + velocity * velocity_weight

# Apply duration bonus if enabled
if duration_bonus_weight > 0:
    bonus = _duration_bonus(duration, optimal_duration_min, optimal_duration_max)
    score *= (1.0 + duration_bonus_weight * (bonus - 1.0))

if title_quality_weight > 0:
    score *= 1.0 + title_quality_weight * _title_quality(clip.title)
```

#### 2.3: Update `filter_and_rank` Function

**Location**: Lines 59-98

**Modify signature** (line 59):
```python
def filter_and_rank(
    conn,
    clips: list[Clip],
    streamer: str,
    velocity_weight: float = 2.0,
    min_view_count: int = 0,
    age_decay: str = "linear",
    view_transform: str = "linear",
    title_quality_weight: float = 0.0,
    duration_bonus_weight: float = 0.0,          # NEW
    optimal_duration_min: int = 14,              # NEW
    optimal_duration_max: int = 31,              # NEW
    analytics_enabled: bool = False,
) -> list[Clip]:
```

**Modify `compute_score` call** (~line 80-86):
```python
c.score = compute_score(
    c,
    velocity_weight=velocity_weight,
    age_decay=age_decay,
    view_transform=view_transform,
    title_quality_weight=title_quality_weight,
    duration_bonus_weight=duration_bonus_weight,           # NEW
    optimal_duration_min=optimal_duration_min,             # NEW
    optimal_duration_max=optimal_duration_max,             # NEW
)
```

#### 2.4: Add Config Fields to `src/models.py`

**Location**: `PipelineConfig` class definition

**Add fields**:
```python
duration_bonus_weight: float = 0.0
optimal_duration_min: int = 14
optimal_duration_max: int = 31
```

#### 2.5: Wire into `main.py`

**Location**: Lines 598-606 (inside `_process_streamer` function)

**Modify `filter_and_rank` call**:
```python
ranked = filter_and_rank(
    conn, clips, name,
    velocity_weight=cfg.velocity_weight,
    min_view_count=cfg.min_view_count,
    age_decay=cfg.age_decay,
    view_transform=cfg.view_transform,
    title_quality_weight=cfg.title_quality_weight,
    duration_bonus_weight=cfg.duration_bonus_weight,           # NEW
    optimal_duration_min=cfg.optimal_duration_min,             # NEW
    optimal_duration_max=cfg.optimal_duration_max,             # NEW
    analytics_enabled=cfg.analytics_enabled,
)
```

#### 2.6: Update `config.yaml`

**Location**: Lines 56-76 (pipeline section)

**Modify**:
```yaml
pipeline:
  max_clips_per_streamer: 6
  max_clip_duration_seconds: 35  # CHANGED from 60 to match analytics
  velocity_weight: 2.0
  clip_lookback_hours: 168  # 7 days
  min_view_count: 50
  age_decay: "log"
  view_transform: "linear"
  title_quality_weight: 0.1  # INCREASED from 0.05 - analytics show titles matter

  # NEW: Duration scoring based on analytics (14-31s sweet spot)
  duration_bonus_weight: 0.3
  optimal_duration_min: 14
  optimal_duration_max: 31

  tmp_dir: "data/tmp"
  # ... rest unchanged
```

### Verification

1. Run: `python main.py --dry-run`
2. Check logs for clip scoring - verify duration affects scores
3. Query database to verify 14-31s clips are prioritized:
   ```python
   import sqlite3
   conn = sqlite3.connect("data/clips.db")
   cursor = conn.execute("""
       SELECT title, duration, score
       FROM clips
       WHERE youtube_id IS NOT NULL
       ORDER BY score DESC
       LIMIT 10
   """)
   for row in cursor: print(row)
   ```

---

## Task 3: Set Up Recurring launchd Schedule for Automatic Runs

**Goal**: Run pipeline automatically 3x daily (8am, 2pm, 8pm) on macOS

### Files to Create

#### 3.1: Create `scripts/ai.twitch-to-shorts.pipeline.plist`

**Content**:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>ai.twitch-to-shorts.pipeline</string>

    <key>Comment</key>
    <string>Twitch-to-Shorts Pipeline - Automatic clip processing and upload</string>

    <key>ProgramArguments</key>
    <array>
      <string>/bin/bash</string>
      <string>/Users/rew/Projects/twitch-to-shorts-claw/scripts/run-pipeline.sh</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/rew/Projects/twitch-to-shorts-claw</string>

    <key>StandardOutPath</key>
    <string>/Users/rew/Projects/twitch-to-shorts-claw/data/launchd-pipeline.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/rew/Projects/twitch-to-shorts-claw/data/launchd-pipeline.err.log</string>

    <key>EnvironmentVariables</key>
    <dict>
      <key>HOME</key>
      <string>/Users/rew</string>
      <key>PATH</key>
      <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
      <key>PIPELINE_TRIGGER</key>
      <string>launchd</string>
    </dict>

    <key>StartCalendarInterval</key>
    <array>
      <dict>
        <key>Hour</key>
        <integer>8</integer>
        <key>Minute</key>
        <integer>0</integer>
      </dict>
      <dict>
        <key>Hour</key>
        <integer>14</integer>
        <key>Minute</key>
        <integer>0</integer>
      </dict>
      <dict>
        <key>Hour</key>
        <integer>20</integer>
        <key>Minute</key>
        <integer>0</integer>
      </dict>
    </array>

    <key>ThrottleInterval</key>
    <integer>3600</integer>

    <key>RunAtLoad</key>
    <false/>

    <key>KeepAlive</key>
    <false/>
  </dict>
</plist>
```

**Note**: StartCalendarInterval must be an array of dicts for multiple times

#### 3.2: Create `scripts/install-launchd.sh`

**Content**:
```bash
#!/bin/bash
# Install launchd job for Twitch-to-Shorts pipeline
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_FILE="$SCRIPT_DIR/ai.twitch-to-shorts.pipeline.plist"
LAUNCHD_DIR="$HOME/Library/LaunchAgents"
INSTALLED_PLIST="$LAUNCHD_DIR/ai.twitch-to-shorts.pipeline.plist"

if [ ! -f "$PLIST_FILE" ]; then
    echo "ERROR: plist file not found at $PLIST_FILE" >&2
    exit 1
fi

mkdir -p "$LAUNCHD_DIR"

echo "Installing plist to $INSTALLED_PLIST"
cp "$PLIST_FILE" "$INSTALLED_PLIST"

# Unload if already loaded (ignore errors)
launchctl unload "$INSTALLED_PLIST" 2>/dev/null || true

echo "Loading launchd job..."
launchctl load "$INSTALLED_PLIST"

if launchctl list | grep -q "ai.twitch-to-shorts.pipeline"; then
    echo "SUCCESS: launchd job installed and loaded"
    echo ""
    echo "Schedule: 8am, 2pm, 8pm daily"
    echo ""
    echo "To check status:"
    echo "  launchctl list | grep twitch-to-shorts"
    echo ""
    echo "To view logs:"
    echo "  tail -f $HOME/Projects/twitch-to-shorts-claw/data/launchd-pipeline.log"
    echo ""
    echo "To manually trigger now:"
    echo "  launchctl start ai.twitch-to-shorts.pipeline"
    echo ""
    echo "To uninstall:"
    echo "  ./scripts/uninstall-launchd.sh"
else
    echo "WARNING: Job loaded but not visible in launchctl list" >&2
fi
```

**Make executable**: `chmod +x scripts/install-launchd.sh`

#### 3.3: Create `scripts/uninstall-launchd.sh`

**Content**:
```bash
#!/bin/bash
# Uninstall launchd job for Twitch-to-Shorts pipeline
set -euo pipefail

INSTALLED_PLIST="$HOME/Library/LaunchAgents/ai.twitch-to-shorts.pipeline.plist"

if [ ! -f "$INSTALLED_PLIST" ]; then
    echo "launchd job not installed (plist not found)"
    exit 0
fi

echo "Unloading launchd job..."
launchctl unload "$INSTALLED_PLIST" 2>/dev/null || true

echo "Removing plist..."
rm "$INSTALLED_PLIST"

if launchctl list | grep -q "ai.twitch-to-shorts.pipeline"; then
    echo "WARNING: Job still visible in launchctl list" >&2
else
    echo "SUCCESS: launchd job uninstalled"
fi
```

**Make executable**: `chmod +x scripts/uninstall-launchd.sh`

#### 3.4: Update `README.md`

**Add section** (after "Running" section):
```markdown
## Automated Scheduling (macOS)

The pipeline can run automatically on a schedule using macOS launchd.

### Install Scheduled Job

```bash
./scripts/install-launchd.sh
```

This sets up 3 daily runs at 8am, 2pm, and 8pm.

### Monitor Logs

```bash
tail -f data/launchd-pipeline.log
tail -f data/launchd-pipeline.err.log
```

### Manually Trigger

```bash
launchctl start ai.twitch-to-shorts.pipeline
```

### Check Status

```bash
launchctl list | grep twitch-to-shorts
```

### Uninstall

```bash
./scripts/uninstall-launchd.sh
```

### Customize Schedule

Edit `scripts/ai.twitch-to-shorts.pipeline.plist` and modify the `StartCalendarInterval` sections, then run `install-launchd.sh` again.
```

### Verification

1. Install: `./scripts/install-launchd.sh`
2. Verify loaded: `launchctl list | grep twitch-to-shorts`
3. Manual trigger: `launchctl start ai.twitch-to-shorts.pipeline`
4. Check logs: `tail -f data/launchd-pipeline.log`
5. Verify database recorded `PIPELINE_TRIGGER=launchd`:
   ```python
   import sqlite3
   conn = sqlite3.connect("data/clips.db")
   cursor = conn.execute("SELECT started_at, trigger FROM pipeline_runs ORDER BY started_at DESC LIMIT 5")
   for row in cursor: print(row)
   ```

---

## Task 4: Quick Config Changes Based on Analytics

**Goal**: Apply analytics-driven config improvements

### Files to Modify

#### 4.1: Update `config.yaml` (already covered in Task 2)

**Summary of changes**:
- `max_clip_duration_seconds: 60` → `35` (analytics show >35s underperforms)
- `title_quality_weight: 0.05` → `0.1` (analytics prove titles drive CTR)
- Add `duration_bonus_weight: 0.3` (favor 14-31s sweet spot)
- Add `optimal_duration_min: 14` and `optimal_duration_max: 31`

**Optional Enhancement**: Add minimum duration filter

#### 4.2: (Optional) Add Min Duration Filter to `src/models.py`

**Add to PipelineConfig**:
```python
min_clip_duration_seconds: int = 10  # Analytics show <10s underperforms
```

#### 4.3: (Optional) Enforce Min Duration in `main.py`

**Location**: Lines 609-614 (in `_process_streamer`)

**Modify**:
```python
new_clips = filter_new_clips(conn, ranked)
max_duration = cfg.max_clip_duration_seconds
min_duration = cfg.min_clip_duration_seconds  # NEW

if new_clips:
    too_long = [c for c in new_clips if c.duration > max_duration]
    too_short = [c for c in new_clips if c.duration < min_duration]  # NEW

    if too_long:
        log.info("Skipping %d clips over %ds", len(too_long), max_duration)
    if too_short:
        log.info("Skipping %d clips under %ds (analytics: poor performance)",
                len(too_short), min_duration)

    new_clips = [c for c in new_clips
                if min_duration <= c.duration <= max_duration]

new_clips = new_clips[:cfg.max_clips_per_streamer]
```

#### 4.4: (Optional) Update `config.yaml`

**Add**:
```yaml
pipeline:
  max_clips_per_streamer: 6
  min_clip_duration_seconds: 10  # NEW
  max_clip_duration_seconds: 35
  # ... rest unchanged
```

### Verification

1. Run: `python main.py --dry-run`
2. Verify logs show clips outside 10-35s being filtered
3. Check clip duration distribution in database:
   ```python
   import sqlite3
   conn = sqlite3.connect("data/clips.db")
   cursor = conn.execute("""
       SELECT
           CASE
               WHEN duration < 10 THEN '<10s'
               WHEN duration BETWEEN 10 AND 13 THEN '10-13s'
               WHEN duration BETWEEN 14 AND 31 THEN '14-31s (sweet spot)'
               WHEN duration BETWEEN 32 AND 35 THEN '32-35s'
               ELSE '>35s'
           END as bucket,
           COUNT(*) as count
       FROM clips
       WHERE youtube_id IS NOT NULL
       GROUP BY bucket
   """)
   for row in cursor: print(row)
   ```

---

## Implementation Order

**Recommended sequence**:

1. **Task 2** (Clip Filter Tuning) - No external dependencies, foundational
2. **Task 4** (Config Changes) - Builds on Task 2, apply config.yaml changes together
3. **Task 1** (Title Optimizer Integration) - Can be done in parallel with 2/4
4. **Task 3** (Launchd Scheduling) - Last, after pipeline changes are tested

---

## Critical Files Summary

| File | Purpose | Tasks |
|------|---------|-------|
| `src/title_optimizer.py` | Add local LLM support | Task 1 |
| `main.py` | Import & integrate title_optimizer, wire duration params | Tasks 1, 2, 4 |
| `src/clip_filter.py` | Add duration bonus scoring | Task 2 |
| `src/models.py` | Add config fields for duration scoring | Tasks 2, 4 |
| `config.yaml` | Update all pipeline settings | Tasks 2, 4 |
| `.env.example` | Document LLM environment variables | Task 1 |
| `scripts/ai.twitch-to-shorts.pipeline.plist` | Launchd schedule config | Task 3 |
| `scripts/install-launchd.sh` | Launchd installer | Task 3 |
| `scripts/uninstall-launchd.sh` | Launchd uninstaller | Task 3 |
| `README.md` | Document automation setup | Task 3 |

---

## Success Metrics

After implementation, monitor these analytics:

1. **CTR improvement**: Compare optimized titles vs original (A/B split built-in)
2. **Duration distribution**: Verify majority of uploads are 14-31s
3. **Upload consistency**: Verify launchd runs 3x daily without failures
4. **Cost savings**: $0 LLM costs using local Qwen vs OpenAI API

Each task is spec'd for one-shot implementation with clear file paths, line numbers, and exact code changes.

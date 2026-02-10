# Security Audit — 2026-02-09

**Auditor:** Security
**Scope:** Full codebase (main.py, all src/ modules, CI workflows, config)
**Previous audit:** 2026-02-05 (see audit-summary.md)

---

## Summary

| Severity | Count |
|----------|-------|
| Critical | 1 |
| High     | 4 |
| Medium   | 5 |
| Low      | 4 |
| **Total**| **14** |

Of the original 18 security findings from the 2026-02-05 audit, **all Critical and High items have been fixed**. This audit focuses on the current codebase state, new code (captioner module), and remaining/new issues.

---

## Previously Fixed (Confirmed)

The following findings from the 2026-02-05 audit are **confirmed resolved**:

- **S-C1 (Twitch client_secret in URL):** FIXED. `twitch_client.py:33` now uses `data=` (POST body) instead of `params=`.
- **S-C2 (config.yaml fallback for secrets):** FIXED. `main.py:99-102` reads exclusively from env vars. Config has `# WARNING: Do NOT put client_id or client_secret here` comment.
- **S-H1 (TLS verification):** FIXED. `twitch_client.py:60` has `verify=True` in `_request()`.
- **S-H2 (Format string injection via clip titles):** FIXED. `youtube_uploader.py:165-172` uses `_TemplateDict` with `__missing__` that returns empty string. `format_map()` does not re-interpret substituted values. The `_escape_braces` function was correctly removed (it was doubling braces unnecessarily).
- **S-H4 (Lock file TOCTOU):** FIXED. `main.py:229-238` uses atomic `os.replace()` pattern instead of remove-then-create.
- **S-M2 (CI credential cleanup):** FIXED. `pipeline.yml:128-129` has `rm -rf credentials/` in `if: always()` step.
- **S-M3 (Unicode bidi characters):** FIXED. `youtube_uploader.py:161` strips `\u200e\u200f\u202a-\u202e\u2066-\u2069`.
- **S-M4 (Bare except clauses):** FIXED. All except blocks now catch specific exception types.

---

## Current Findings

### S-C1: Deepgram API key exposed in Deepgram SDK HTTP requests without TLS pinning
- **Severity:** Critical
- **Effort:** S
- **Location:** `src/captioner.py:35`
- **Issue:** The `DEEPGRAM_API_KEY` is passed directly to `DeepgramClient(api_key)`. The Deepgram SDK sends this as an `Authorization: Token <key>` header in HTTP requests. While the SDK uses HTTPS by default, there is no certificate pinning or key rotation mechanism. If the key is compromised (e.g., leaked env var, CI log exposure), there is no mechanism to detect abuse or rotate. Unlike the YouTube OAuth token (which auto-refreshes and is scoped), this is a static API key with no expiry. Additionally, there is no validation that the key looks like a valid Deepgram key before making network calls.
- **Fix:** Add a `DEEPGRAM_API_KEY` pattern to `.gitignore` comments as a reminder. More importantly: (1) validate key format before use (Deepgram keys are typically 40-char hex), (2) add the key to the CI secrets if captions are enabled in CI, and (3) consider adding a cost/usage budget check since Deepgram charges per audio-second. The key itself is read from env vars which is correct — the risk is about lifecycle management, not about how it's loaded.

### S-H1: Subtitle path injection in ffmpeg filter_complex
- **Severity:** High
- **Effort:** S
- **Location:** `src/video_processor.py:365-406`
- **Issue:** The `_escape_subtitle_path()` function at line 365-367 only escapes `\` to `/` and `:` to `\:`. The escaped path is then interpolated into an ffmpeg filter string at lines 402 and 406 using f-string formatting inside single quotes: `subtitles='{escaped}'`. If a clip ID (which forms part of the subtitle filename at `captioner.py:178`) contains characters like `'`, `;`, `[`, `]`, or newlines, this could break the ffmpeg filter_complex string and potentially cause unexpected behavior. The clip ID comes from the Twitch API (`c["id"]`) and while Twitch clip IDs are typically alphanumeric with dashes, the pipeline does not validate or sanitize clip IDs at ingestion.
- **Fix:** The `_escape_subtitle_path()` function should also escape single quotes (replace `'` with `'\''`), semicolons, and square brackets. Alternatively, validate clip IDs at ingestion in `twitch_client.py:119` to reject any ID not matching `^[a-zA-Z0-9_-]+$`.

### S-H2: No input validation on clip IDs used in filesystem paths and subprocess args
- **Severity:** High
- **Effort:** S
- **Location:** `src/twitch_client.py:119`, `src/downloader.py:17`, `src/video_processor.py:207`, `src/captioner.py:19,177`
- **Issue:** Clip IDs from the Twitch API are used directly in filesystem paths (e.g., `f"{clip.id}.mp4"` in downloader.py:17, `f"{clip_id}_vertical.mp4"` in video_processor.py:208) and as components of subprocess arguments. While Twitch clip IDs are typically safe alphanumeric strings, there is no validation at the trust boundary (API response parsing). A malicious or malformed API response could include path traversal sequences (`../`), null bytes, or shell-significant characters. All downstream modules trust the clip ID implicitly.
- **Fix:** Add clip ID validation in `twitch_client.py:119` after parsing the Twitch response: `if not re.match(r'^[a-zA-Z0-9_-]+$', c["id"]): log.warning(...); continue`. This is a single trust boundary check that protects all downstream consumers.

### S-H3: Clip URL from Twitch API passed directly to yt-dlp subprocess
- **Severity:** High
- **Effort:** S
- **Location:** `src/downloader.py:32-34`
- **Issue:** The `clip.url` value originates from the Twitch Helix API response (`c["url"]` at `twitch_client.py:121`) and is passed as a positional argument to `subprocess.run([YT_DLP, ..., clip_url])`. While using a list form of `subprocess.run` (not `shell=True`) prevents shell injection, yt-dlp itself interprets URLs and could follow redirects or download from unexpected sources if the URL is tampered with (e.g., man-in-the-middle despite TLS, or Twitch API compromise). There is no validation that the URL points to `clips.twitch.tv` or a known Twitch CDN domain.
- **Fix:** Validate that `clip.url` starts with `https://clips.twitch.tv/` or `https://www.twitch.tv/` before passing to yt-dlp. This is a defense-in-depth measure.

### S-H4: `clean_stale_tmp` could delete files outside tmp_dir via symlink following
- **Severity:** High
- **Effort:** S
- **Location:** `main.py:108-124`
- **Issue:** `clean_stale_tmp()` uses `os.scandir()` and `os.remove(entry.path)` to delete old files. If an attacker (or another process) places a symlink in the tmp directory pointing to a file outside of it, `entry.is_file()` returns True for symlinks to files, and `os.remove()` deletes the target. The risk is limited by the suffix filter (`.mp4`, `.mp4.tmp`, `.part`, `.ytdl`) and the age check, but is not zero.
- **Fix:** Add `entry.is_symlink()` check: `if entry.is_symlink() or not entry.is_file(): continue`. This prevents following symlinks during cleanup.

### S-M1: Deepgram audio file written to shared tmp directory without restricted permissions
- **Severity:** Medium
- **Effort:** S
- **Location:** `src/captioner.py:20-21`, `src/media_utils.py:27-32`
- **Issue:** The extracted WAV audio file (`{clip_id}_audio.wav`) is written to the shared `tmp_dir` with default permissions (typically 0o644 or broader depending on umask). On multi-user systems or shared CI runners, other processes could read the audio data. The YouTube credential files are written with `0o600` (good), but audio files are not similarly protected.
- **Fix:** Low priority since the audio is from public Twitch clips, but for consistency with the credential file pattern, consider setting restrictive permissions on the tmp directory itself via `os.makedirs(tmp_dir, mode=0o700, exist_ok=True)`.

### S-M2: CI pipeline uses `GITHUB_TOKEN` for artifact download (limited scope)
- **Severity:** Medium
- **Effort:** S
- **Location:** `.github/workflows/pipeline.yml:72-73`
- **Issue:** The artifact fallback step uses `${{ secrets.GITHUB_TOKEN }}` for `gh run download`. This is the correct token for this purpose. However, the same workflow also uses `${{ secrets.GH_PAT }}` (a Personal Access Token) for saving secrets at line 91. The PAT has broader permissions than needed (it must have `admin:org` or `repo` scope to write secrets). If the CI runner is compromised, the PAT could be used to modify other repository secrets or settings.
- **Fix:** Consider using a fine-grained PAT scoped only to `secrets:write` for this repository. Document the minimum required scopes for `GH_PAT` in the README.

### S-M3: OAuth credential file permissions not verified on load
- **Severity:** Medium
- **Effort:** S
- **Location:** `src/youtube_uploader.py:63-69`
- **Issue:** When loading credentials from `credentials_file`, the code checks `os.path.exists()` but does not verify file permissions. The file is written with `0o600` at line 107, but if an external process or user changes permissions (e.g., `chmod 644`), the credentials would still be loaded without warning. On multi-user systems, this means another user could read the OAuth token.
- **Fix:** Add a permissions check when loading credentials: warn if file mode is not `0o600` on POSIX systems.

### S-M4: SQLite database file permissions not restricted
- **Severity:** Medium
- **Effort:** S
- **Location:** `src/db.py:9`
- **Issue:** The SQLite database at `data/clips.db` is created with default permissions. The database contains clip metadata (not secrets), but it also contains `youtube_id` values that could be used to identify the channel. Additionally, `os.makedirs(os.path.dirname(db_path), exist_ok=True)` creates the directory with default permissions.
- **Fix:** Low priority since the DB does not contain secrets. Consider setting directory permissions to `0o700` for the data directory.

### S-M5: No rate limiting on Deepgram API calls
- **Severity:** Medium
- **Effort:** M
- **Location:** `src/captioner.py:48-49`
- **Issue:** `transcribe_clip()` is called once per clip in the pipeline loop (via `_process_single_clip` at `main.py:298-300`). If captions are enabled and many clips are processed, this could result in high Deepgram API costs with no budget guard. There is no cost estimation, usage tracking, or circuit breaker.
- **Fix:** Add a configurable `max_captions_per_run` limit in the pipeline config, similar to `max_uploads_per_window`. Track caption API calls and stop after the limit.

### S-L1: Lockfile PID written as plain text (information disclosure)
- **Severity:** Low
- **Effort:** S
- **Location:** `main.py:209`
- **Issue:** The PID of the running pipeline process is written to `data/pipeline.lock`. This is by design for stale lock detection, but the PID is written with default permissions and could be read by other users on a shared system. Knowing the PID enables targeted signals.
- **Fix:** No change recommended. The PID-based lock is a standard pattern and the information disclosure risk is minimal.

### S-L2: MD5 used for A/B template selection (not a security issue)
- **Severity:** Low
- **Effort:** N/A
- **Location:** `src/youtube_uploader.py:178`
- **Issue:** MD5 is used to deterministically select A/B templates based on clip ID. This is **not** a security concern — MD5 is fine for non-cryptographic hash distribution. Previous audit agreed.
- **Status:** PREVIOUSLY NOTED, no change needed.

### S-L3: `_TemplateDict.__missing__` silently returns empty string
- **Severity:** Low
- **Effort:** S
- **Location:** `src/youtube_uploader.py:133-135`
- **Issue:** If a template references an unknown key, the `__missing__` method logs a warning but returns `""`. This means a typo in a template (e.g., `{stremr}` instead of `{streamer}`) silently produces an empty substitution. The `validate_templates()` function at line 141 catches this at config load time, but only if templates are explicitly configured (not the default fallback).
- **Fix:** Low priority. The `validate_templates()` function provides adequate coverage. Consider making the default template `"{title} | {streamer}"` also pass through validation.

### S-L4: WAL checkpoint failure in `run_pipeline` is caught but not actionable
- **Severity:** Low
- **Effort:** S
- **Location:** `main.py:256-258`
- **Issue:** If the WAL checkpoint fails, a warning is logged but the pipeline continues. The database remains in WAL mode with unmerged pages. This is not a security issue per se, but on CI where the DB is cached, an incomplete WAL could lead to data being lost if only `clips.db` is cached without `clips.db-wal` and `clips.db-shm`.
- **Fix:** The CI workflow already runs `sqlite3 data/clips.db "PRAGMA wal_checkpoint(TRUNCATE)"` at line 102. This is adequate. No change needed.

---

## CI Workflow Security Review

### Strengths
1. **Credentials restored from base64-encoded secrets** (not plaintext in env vars that could appear in logs)
2. **Credential cleanup in `if: always()`** prevents leaking credentials if pipeline fails
3. **Token save guarded with `if [ -s file ]`** prevents saving empty/corrupt token
4. **DB save uses `if: success()`** prevents stale DB from overwriting good data
5. **Concurrency group** prevents parallel pipeline runs
6. **Minimal permissions** (`actions: write`, `contents: read`) follow least-privilege

### Notes
- The `GH_PAT` secret at line 91 is the most privileged credential in the pipeline. It can write repository secrets. If the runner is compromised, this token allows modifying other secrets.
- The `GITHUB_TOKEN` at line 108 for cache deletion has appropriate limited scope.
- The `printf '%s'` pattern at lines 57-58 is correct for avoiding newline injection.

---

## Subprocess Safety Review

All subprocess calls use list-form arguments (no `shell=True`). This is the correct pattern and prevents shell injection. Specific review:

| File | Line(s) | Binary | Safe? |
|------|---------|--------|-------|
| `src/downloader.py` | 32-34 | yt-dlp | Yes (list form, no shell) |
| `src/media_utils.py` | 14-17 | ffprobe | Yes (list form, path is validated input) |
| `src/media_utils.py` | 27-31 | ffmpeg | Yes (list form) |
| `src/video_processor.py` | 63-68 | ffmpeg | Yes (list form, timestamps are floats) |
| `src/video_processor.py` | 93-103 | ffmpeg | Yes (list form, timestamps are floats) |
| `src/video_processor.py` | 156-162 | ffmpeg | Yes (list form, width is int) |
| `src/video_processor.py` | 178-184 | ffmpeg | Yes (list form, threshold is float) |
| `src/video_processor.py` | 324-328 | ffmpeg | Yes (list form) |
| `src/video_processor.py` | 380-427 | ffmpeg | Yes, BUT subtitle_path is interpolated into filter string (see S-H1) |

The one exception is the subtitle filter injection at `video_processor.py:402,406` where the subtitle path is interpolated into an ffmpeg filter string. This is mitigated by `_escape_subtitle_path()` but the escaping is incomplete (see S-H1).

---

## SQL Injection Review

All SQL queries use parameterized queries (`?` placeholders). The one notable pattern is `dedup.py:30-33`:

```python
placeholders = ",".join("?" for _ in clip_ids)
f"SELECT clip_id FROM clips WHERE clip_id IN ({placeholders})"
```

This is **safe** — the `?` placeholders are generated from a count, not from user data, and the actual values are passed as parameters. This is a standard SQLite parameterized IN clause pattern.

---

## Recommendations (Priority Order)

1. **S-H2:** Add clip ID validation at the Twitch API trust boundary — simple regex, protects all downstream modules
2. **S-H1:** Improve `_escape_subtitle_path()` to handle single quotes and filter-significant characters
3. **S-H3:** Validate clip URLs point to Twitch domains before passing to yt-dlp
4. **S-H4:** Add symlink check in `clean_stale_tmp()`
5. **S-C1:** Add Deepgram API key lifecycle management (budget/usage tracking)
6. **S-M2:** Scope `GH_PAT` to minimum required permissions
7. **S-M5:** Add `max_captions_per_run` config option as a cost guard

---

## Comparison with Previous Audit

| 2026-02-05 Finding | Status | Notes |
|---------------------|--------|-------|
| S-C1 (secret in URL) | FIXED | `data=` in POST body |
| S-C2 (config fallback) | FIXED | Env vars only, warning comment |
| S-H1 (TLS verify) | FIXED | `verify=True` |
| S-H2 (format string) | FIXED | `_TemplateDict.__missing__`, `_escape_braces` removed |
| S-H3 (subprocess injection) | FIXED | All list-form calls |
| S-H4 (lock TOCTOU) | FIXED | `os.replace()` atomic pattern |
| S-H5 (bare except) | FIXED | Specific exception types |
| S-M1 (credentials on disk) | FIXED | `0o600` permissions |
| S-M2 (CI cleanup) | FIXED | `rm -rf credentials/` in always() |
| S-M3 (bidi chars) | FIXED | Stripped in `_sanitize_text` |
| S-M4 (bare excepts) | FIXED | All narrowed |
| S-L1 (MD5) | N/A | Not a security issue (confirmed) |

All previously identified security issues from the 2026-02-05 audit have been addressed. The current findings are either new (related to the captioner module) or represent deeper defense-in-depth concerns that were not in scope for the original audit.

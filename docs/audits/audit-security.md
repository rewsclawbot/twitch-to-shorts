# Security Audit -- 2026-02-05

Auditor: security-auditor agent
Scope: Full codebase (main.py, src/, config.yaml, .github/workflows/, tests/)
Known issues from tasks/lessons.md referenced where applicable.

---

## Critical

### S-C1: Twitch client_secret sent as URL query parameter

- **Location:** `src/twitch_client.py:27-31`
- **Description:** The Twitch OAuth token request sends `client_secret` via `params=` (URL query parameters) rather than in the request body (`data=`). Query parameters are logged in HTTP server access logs, browser history (if debugging via proxy), and are visible in network monitoring tools. The Twitch API accepts both `params` and `data` for this endpoint, but sending secrets in the URL is a well-known anti-pattern (OWASP, RFC 6749 Section 2.3.1).
- **Impact:** Twitch client_secret could be captured by intermediate proxies, load balancers, or HTTP access log scrapers. If the CI runner or any proxy logs full request URLs, the secret is exposed in plaintext.
- **Recommendation:** Change `params=` to `data=` in the `requests.post()` call for the token endpoint. This sends credentials in the POST body instead of the URL.

### S-C2: Twitch client credentials readable from config.yaml (fallback path)

- **Location:** `main.py:100-103`, `main.py:260-261`
- **Description:** The code falls back to reading `twitch.client_id` and `twitch.client_secret` from `config.yaml` if environment variables are not set. While the config currently has these commented out, the code path exists and is tested. If a user uncomments these lines, the secrets would be stored in a file that is tracked in git (config.yaml is not in .gitignore). The `config.yaml` file is already committed to the repository.
- **Impact:** If credentials are added to config.yaml and committed, they become part of the git history permanently (even if later removed). The repository is public on GitHub (`jahruggdd/twitch-to-shorts` per sync_db.py:13), so this would be immediate public exposure.
- **Recommendation:** Remove the config.yaml fallback entirely for secrets. Only support environment variables. Add a comment in config.yaml warning not to put secrets there. Consider adding a pre-commit hook that scans for secret patterns.

---

## High

### S-H1: No HTTPS certificate verification enforcement for Twitch API calls

- **Location:** `src/twitch_client.py:50`
- **Description:** The `requests.request()` call in `_request()` passes through `**kwargs`, which means a caller could pass `verify=False` to disable TLS certificate verification. More importantly, the `requests` library respects the `REQUESTS_CA_BUNDLE` and `CURL_CA_BUNDLE` environment variables, and setting `REQUESTS_CA_BUNDLE=""` disables verification silently. In a CI environment where environment variables are controlled by workflow configuration, this is a risk vector.
- **Impact:** A MITM attacker could intercept Twitch API traffic (including bearer tokens and client credentials) if certificate verification is disabled.
- **Recommendation:** Explicitly pass `verify=True` in the `_request()` method to override any environment-level tampering.

### S-H2: format_map with user-controlled Twitch clip titles enables information disclosure

- **Location:** `src/youtube_uploader.py:136-143`
- **Description:** `_render_template()` uses `format_map()` with a `_TemplateDict` that returns empty string for unknown keys. The template string comes from config.yaml (trusted), but the `clip.title` value comes from the Twitch API (user-created clip titles). While the template itself is not user-controlled, the rendered values (`clip.title`, `clip.streamer`, `clip.game_name`) flow into `format_map()`. If a future template were to use `{title}` and the title contained format string syntax like `{__class__}`, the `_TemplateDict.__missing__` would return `""` rather than crashing, but this is defense-by-accident rather than design. More critically, a Twitch clip title containing literal `{streamer}` would be double-interpolated if it appears in a template that includes `{title}`.
- **Impact:** Low-to-medium. Current templates are safe because `_TemplateDict.__missing__` returns empty string. However, a Twitch clip titled `{streamer}` in a template like `"{title} highlights"` would render as the streamer's name rather than the literal text `{streamer}`. This is data corruption rather than code execution, but it is unexpected behavior driven by untrusted input.
- **Recommendation:** Escape format string syntax in clip titles before passing to `format_map()`. Replace `{` and `}` with `{{` and `}}` in user-supplied values, or switch to a safer template mechanism (e.g., `string.Template` with `$variable` syntax).

### S-H3: GH_PAT secret has broad scope, used for token persistence

- **Location:** `.github/workflows/pipeline.yml:72`
- **Description:** The `Save updated token` step uses `GH_PAT` (a Personal Access Token) rather than `GITHUB_TOKEN` to write back the refreshed YouTube OAuth token as a repository secret. PATs are long-lived, manually managed, and often over-scoped. The PAT needs `repo` scope to set secrets, which also grants push access, issue management, and more. If the PAT leaks (e.g., through a workflow log, or if a future step accidentally echoes it), the attacker gains full repository control.
- **Impact:** The GH_PAT is a high-value target. Its compromise gives write access to the repository (code injection via push), all secrets (credential theft), and CI (supply chain attack).
- **Recommendation:** Document the minimum required PAT scopes. Consider using a GitHub App installation token (scoped to only `secrets:write`) instead of a PAT. If a PAT must be used, set an expiration date and audit scope regularly.

### S-H4: Race condition in lock file acquisition allows duplicate pipeline runs

- **Location:** `main.py:230-234`
- **Description:** Between `os.remove(LOCK_FILE)` (line 231) and `_try_create_lock()` (line 234), another process could create the lock file. The current code handles this gracefully (the second `_try_create_lock()` would fail and return `False`), so this is not exploitable for code execution. However, the check-then-delete-then-create sequence is a TOCTOU race. On a multi-process system or CI with retries, two pipeline instances could briefly both believe they have the lock.
- **Impact:** Low in practice due to the CI concurrency group. On local development or if the concurrency group is removed, two pipelines could run simultaneously, potentially causing duplicate uploads or database corruption.
- **Recommendation:** Use `fcntl.flock()` (Unix) or `msvcrt.locking()` (Windows) for atomic file locking, or accept the current risk given CI concurrency controls.

### S-H5: Credential files on disk without encryption at rest

- **Location:** `src/youtube_uploader.py:101-103`, `credentials/` directory
- **Description:** YouTube OAuth tokens (including refresh tokens) are stored as plaintext JSON files in `credentials/`. While file permissions are set to 0o600 (good), the files exist on disk unencrypted. The `.gitignore` correctly excludes `credentials/`, but local copies persist. The refresh token in these files provides indefinite access to the YouTube channel until revoked.
- **Impact:** If the development machine is compromised, the attacker gains persistent YouTube channel access. On CI, the credentials are decoded from base64-encoded secrets and written to disk during the run -- they exist as plaintext on the runner for the duration of the job.
- **Recommendation:** Consider encrypting credential files at rest with a key from an environment variable. On CI, add a cleanup step that securely deletes credential files after the pipeline completes (even on failure). Use `shred` or `srm` on Linux runners.

---

## Medium

### S-M1: SQL query built with f-string interpolation in dedup

- **Location:** `src/dedup.py:29-34`
- **Description:** The `filter_new_clips()` function builds a SQL `IN (...)` clause using an f-string with `placeholders = ",".join("?" for _ in clip_ids)`. While this generates `?` placeholders (not direct value interpolation), the pattern of constructing SQL with f-strings is fragile. The actual values are passed as parameters (safe), but a future modification could accidentally introduce direct interpolation. This is a code smell rather than a current vulnerability.
- **Impact:** No current SQL injection. However, the pattern invites mistakes in future maintenance.
- **Recommendation:** Add a code comment documenting why this is safe (parameterized values only), or use a query builder that makes the safety more explicit.

### S-M2: No cleanup of credential files on CI after pipeline execution

- **Location:** `.github/workflows/pipeline.yml`
- **Description:** The `Restore credentials` step writes `client_secret.json` and `theburntpeanut_youtube.json` to disk. There is no cleanup step to remove these files after the pipeline completes. While GitHub Actions runners are ephemeral, the files persist for the duration of the job and any subsequent steps.
- **Impact:** If a future step is added that uploads artifacts or logs file contents, credential files could be captured. The risk is low with current workflow steps but increases with workflow modifications.
- **Recommendation:** Add a cleanup step with `if: always()` that removes the `credentials/` directory: `rm -rf credentials/`.

### S-M3: `_sanitize_text` does not strip all dangerous characters for YouTube metadata

- **Location:** `src/youtube_uploader.py:132-133`
- **Description:** `_sanitize_text()` strips control characters (`\x00-\x1f`) and `<>` angle brackets. However, it does not filter or encode other characters that could be problematic in YouTube metadata contexts, such as backticks, quotes, or Unicode directional override characters (U+202E etc.) that could cause visual spoofing in YouTube titles.
- **Impact:** A Twitch clip with a title containing Unicode RTL override characters could appear misleading when displayed on YouTube. This is a content integrity issue rather than a code execution risk.
- **Recommendation:** Additionally strip Unicode bidirectional control characters (U+200E-U+200F, U+202A-U+202E, U+2066-U+2069) and consider normalizing to NFC form.

### S-M4: Twitch API token stored as instance attribute without protection

- **Location:** `src/twitch_client.py:21-22`, `src/twitch_client.py:34`
- **Description:** The Twitch access token is stored as `self._token` (a plain string attribute). It could be exposed via `repr()`, `vars()`, or debugging tools. There is no redaction in `__repr__` or `__str__` methods.
- **Impact:** If a TwitchClient object is accidentally logged (e.g., via `log.exception()` with a traceback that includes local variables), the bearer token could appear in log files.
- **Recommendation:** Override `__repr__` on TwitchClient to redact sensitive fields. Consider using a `SecretStr`-style wrapper.

### S-M5: `xqc_youtube.json` credential file exists locally but is not configured

- **Location:** `credentials/xqc_youtube.json` (found on disk)
- **Description:** The file `credentials/xqc_youtube.json` exists in the local `credentials/` directory but is not referenced in `config.yaml`. It appears to be a leftover or test credential. While `.gitignore` prevents it from being committed, its mere presence suggests credential files may accumulate without being tracked or rotated.
- **Impact:** If `.gitignore` is modified or the file is force-added, an unused OAuth token for another YouTube channel would be exposed. The credential may also be stale but still valid.
- **Recommendation:** Remove unused credential files. Implement a check that all files in `credentials/` are referenced in config.yaml.

### S-M6: No rate limiting or backoff on YouTube API token refresh

- **Location:** `src/youtube_uploader.py:78-81`
- **Description:** When the YouTube OAuth token expires, `creds.refresh(Request())` is called with no retry limit or backoff. If the refresh fails transiently and is retried rapidly (e.g., by a loop or scheduler), it could trigger Google's abuse detection. The `RefreshError` is caught and re-raised, but there is no delay between retries across pipeline runs.
- **Impact:** Repeated rapid refresh attempts could cause Google to temporarily block the OAuth client, affecting all streamers using the same `client_secrets_file`.
- **Recommendation:** Log the refresh attempt timestamp and implement a minimum interval between refresh retries.

---

## Low

### S-L1: MD5 used for A/B template selection

- **Location:** `src/youtube_uploader.py:149`
- **Description:** `hashlib.md5(clip_id.encode("utf-8")).hexdigest()` is used to deterministically select a title template. MD5 is cryptographically broken for collision resistance. However, this is not a security-sensitive use case -- it is used only for deterministic bucketing, not for authentication or integrity verification.
- **Impact:** None. The use of MD5 here is functionally correct for its purpose (deterministic selection).
- **Recommendation:** No change required. If uniformity of distribution matters, consider using `hashlib.sha256` for future-proofing, but this is purely cosmetic.

### S-L2: `pip install` in CI without hash verification

- **Location:** `.github/workflows/pipeline.yml:30-31`, `.github/workflows/tests.yml:22-23`
- **Description:** `pip install -r requirements.txt` does not use `--require-hashes`. A compromised PyPI package (supply chain attack) could inject malicious code into the CI runner, which has access to YouTube credentials, Twitch secrets, and the GH_PAT.
- **Impact:** Supply chain attack on any dependency would compromise the entire pipeline. This is a general CI hardening concern, not specific to this codebase.
- **Recommendation:** Pin dependency versions in `requirements.txt` (if not already) and consider using `pip install --require-hashes` with a lockfile. Use Dependabot or similar for automated security updates.

### S-L3: No logging redaction for credential file paths

- **Location:** `src/youtube_uploader.py:85` (`log.error(..., credentials_file)`)
- **Description:** The credentials file path is logged in error messages. While the path itself is not a secret, it reveals the filesystem layout and the naming convention for credential files, which could aid targeted attacks.
- **Impact:** Minimal. Path disclosure in logs is low severity for a private CI environment.
- **Recommendation:** Acceptable as-is. If the repository becomes multi-tenant, consider redacting paths.

### S-L4: `data/pipeline.log` may contain sensitive operational information

- **Location:** `main.py:46-58`, `config.yaml:56`
- **Description:** The rotating log file at `data/pipeline.log` accumulates operational data including streamer names, clip titles, YouTube video IDs, and error messages. While no credentials are directly logged, the log provides a detailed operational profile that could be useful for social engineering or competitive intelligence.
- **Impact:** Low. The log file is in `.gitignore` (`data/*.log`) and stays local.
- **Recommendation:** Acceptable as-is. Ensure log files are not accidentally included in artifact uploads.

### S-L5: `if: always()` on token save is intentional but worth monitoring

- **Location:** `.github/workflows/pipeline.yml:70`
- **Description:** The `Save updated token` step runs with `if: always()`, which is correct (refreshed tokens must persist even if the pipeline fails). The file-size guard (`if [ -s file ]`) prevents saving empty credentials. This is already documented in tasks/lessons.md as a known pattern.
- **Impact:** None currently. The dual guard (`if: always()` + file size check) is correct.
- **Recommendation:** No change needed. This is documented as a known-good pattern.

---

## Test Coverage Gaps (Security-Relevant)

### S-T1: No tests for `_sanitize_text` with adversarial input

- **Location:** `tests/test_youtube_uploader.py`
- **Description:** There are no tests for `_sanitize_text()` with inputs containing control characters, Unicode RTL overrides, format string syntax (`{}`), or extremely long strings. The function is the primary defense against malicious Twitch clip titles reaching YouTube.
- **Recommendation:** Add tests for: (1) control characters, (2) `<script>` tags, (3) `{format_string}` syntax, (4) Unicode bidirectional overrides, (5) null bytes, (6) strings exceeding YouTube's 100-char title limit.

### S-T2: No tests for credential handling edge cases

- **Location:** `tests/`
- **Description:** There are no tests for `get_credentials()` covering: corrupt JSON in credential files, missing fields, expired tokens with no refresh token, credential file permission verification, or the TTY check for interactive OAuth.
- **Recommendation:** Add mock-based tests for credential loading failure modes. These are the most security-sensitive code paths in the application.

### S-T3: No integration test for subprocess command construction

- **Location:** `tests/`
- **Description:** There are no tests verifying that subprocess commands in `downloader.py` and `video_processor.py` properly handle filenames with special characters (spaces, quotes, semicolons, pipes). While all subprocess calls use list-form arguments (safe), there are no tests proving this remains true.
- **Recommendation:** Add parameterized tests with adversarial filenames to verify no command injection via clip IDs containing shell metacharacters.

---

## Previously Known Issues (from tasks/lessons.md) -- Status Check

| Known Issue | Status | Notes |
|---|---|---|
| `gh secret set --body -` corruption | **FIXED** (commit `37a75f0`) | Correctly uses piped stdin without `--body` |
| DB cache `if: always()` causing duplicates | **FIXED** (commit `b6700a2`) | Save steps now use `if: success()` |
| Token save guard (empty file) | **FIXED** | `if [ -s file ]` guard in place |
| verify_upload 403 (missing scope) | **KNOWN/ACCEPTED** | Code handles gracefully, verify is no-op until re-auth |
| Record before verify | **FIXED** (commit `83fed74`) | DB insert immediately after upload |
| Channel dedup quota optimization | **FIXED** | Uses `playlistItems.list` (2 units) |

---

## Summary

| Severity | Count | Key Themes |
|---|---|---|
| Critical | 2 | Secret in URL params, config.yaml fallback for secrets |
| High | 5 | TLS verification, format string injection, PAT scope, race condition, unencrypted creds |
| Medium | 6 | SQL pattern, CI credential cleanup, sanitization gaps, token exposure, stale creds, refresh rate |
| Low | 5 | MD5 usage, pip hashes, path logging, log file contents, token save pattern |
| Test Gaps | 3 | Sanitization, credential handling, subprocess command construction |

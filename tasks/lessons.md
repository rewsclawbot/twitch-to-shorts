# Lessons Learned

## 2026-02-02 — Initial Audit

- **Score vs filter disconnect**: When building a scoring system, make sure the filtering threshold actually uses the score. Easy to compute a fancy metric and then threshold on a raw field instead.
- **Silent error suppression**: Bare `except: pass` blocks are tech debt time bombs. Always log at minimum `log.warning()` so failures are visible in production.
- **Track ALL failure paths in DB**: If you track success in the DB, you must also track failure. Otherwise failed items re-enter the pipeline forever.
- **INSERT OR IGNORE is dangerous with pre-existing rows**: If another code path can insert the same primary key first (e.g., fail tracking), `INSERT OR IGNORE` will silently drop your data. Use `INSERT ... ON CONFLICT UPDATE` instead.
- **Filter early, process late**: If the API gives you metadata (like duration), filter on it before downloading. Don't waste bandwidth on clips you'll reject.

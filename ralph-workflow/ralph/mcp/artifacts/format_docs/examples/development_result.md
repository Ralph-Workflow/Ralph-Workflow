---
type: development_result
status: completed
---

## Summary

- [SUM-1] Serialized token refresh per token key to eliminate the expiry race; the new concurrency regression test failed before the fix and passes after, and the full auth module is green.

## Files Changed

- [F-1] src/auth/refresh.py
- [F-2] tests/auth/test_refresh_race.py

## Plan Items Proven

- [S-1] Created tests/auth/test_refresh_race.py; ran it before the fix and recorded the failure (AssertionError: token invalidated while in use).
- [S-2] Added the per-token-key lock in src/auth/refresh.py; refresh_token() signature unchanged (verified with git diff — only the function body changed).
- [S-3] pytest tests/auth/test_refresh_race.py -q passed on three consecutive runs (exit 0 each time).
- [S-4] pytest tests/auth -q passed: 47 passed in 8.2s, zero failures, no new warnings.

## Analysis Items Addressed

- [FIX-1] Bounded the lock dictionary: entries are dropped when a refresh completes with no waiters; asserted in test_concurrent_refresh_keeps_token_valid.

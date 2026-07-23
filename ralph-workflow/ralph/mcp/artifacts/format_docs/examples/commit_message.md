---
type: commit
subject: fix(auth): serialize token refresh to prevent expiry race
---

## Body Summary

- [BS-1] Concurrent refresh requests could invalidate a token another request was still using; refresh operations are now serialized per token key.

## Body Details

- [BD-1] The race lived between the expiry check and the refresh write. A per-token-key lock guards that critical section; unrelated tokens stay fully concurrent and the public refresh_token() signature is unchanged. A concurrency regression test reproduces the race and pins the fix.

## Body Footer

- [BF-1] Fixes #482

## Files

- [F-1] src/auth/refresh.py
- [F-2] tests/auth/test_refresh_race.py

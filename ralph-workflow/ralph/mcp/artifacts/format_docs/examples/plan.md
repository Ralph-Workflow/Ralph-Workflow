---
type: plan
---

## Work Units

- [U-1] Race regression test
  Characterize the refresh race before changing the lock implementation.

  Directories: tests/auth

### [S-1] Reproduce the refresh race
Create a deterministic regression for two refreshes using the same token key.

Type: file_create
Files:
- create tests/auth/test_refresh_race.py
Verify: pytest tests/auth/test_refresh_race.py -q
Expect: pytest reports one failing same-key refresh test with exit code 1

- [U-2] Per-token-key lock body
  Replace the global refresh lock with a per-token-key lock.

  Directories: src/auth
  Depends on: U-1

### [S-2] Serialize refreshes per token key
Guard the check-then-refresh critical section with a bounded per-key lock lifecycle.

Type: file_change
Files:
- modify src/auth/refresh.py
Depends on: S-1
Verify: pytest tests/auth/test_refresh_race.py -q
Expect: the race regression passes with exit code 0

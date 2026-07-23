---
type: plan
schema_version: 1
---
## Summary
Concurrent refresh requests can invalidate a token another request is using.

Intent: Serialize refresh per token key without changing refresh_token().
Coverage: bugfix, test

## Scope
- [SC-1] Reproduce the double-refresh race
  Category: test
  Count: 1 test
- [SC-2] Serialize refresh operations per token key
  Category: bugfix
  Count: 1 file
- [SC-3] Verify focused and full auth behavior
  Category: test

## Skills MCP
Skills: test-driven-development, systematic-debugging

## Steps

### [S-1] Reproduce the race in a failing test
Add a deterministic test with two concurrent refreshes for the same token key.
Run it before the fix and record the expected failure.

Type: file_change
Priority: high
Files:
- create tests/auth/test_refresh_race.py
Satisfies: AC-01
Evidence:
- test_name: tests/auth/test_refresh_race.py::test_concurrent_refresh_keeps_token_valid

### [S-2] Serialize refresh per token key
Guard the check-then-refresh critical section with a bounded per-key lock
lifecycle. Preserve the refresh_token() signature and return type.

Type: file_change
Priority: high
Files:
- modify src/auth/refresh.py
Depends on: S-1
Satisfies: AC-01, AC-02
Rationale: Per-key locking removes the race while unrelated tokens remain concurrent.
Evidence:
- file: src/auth/refresh.py

### [S-3] Prove the regression is fixed
Run the focused regression test.

Type: verify
Depends on: S-2
Verify: pytest tests/auth/test_refresh_race.py -q

### [S-4] Prove auth behavior did not regress
Run the full auth test directory and check for failures or warnings.

Type: verify
Depends on: S-3
Verify: pytest tests/auth -q

## Critical Files
- [CF-1] src/auth/refresh.py
  Action: modify
  Changes: add bounded per-token refresh serialization
- [CF-2] tests/auth/test_refresh_race.py
  Action: create
  Changes: add one deterministic concurrency regression test
- [CF-3] src/auth/tokens.py
  Purpose: token model and expiry fields used by refresh

## Constraints
Must not break:
- refresh_token() signature or return type
Must keep working:
- concurrent refreshes for unrelated token keys
Performance budget: focused tests complete within one second each

## Design
Use a per-token synchronization boundary with explicit cleanup. Keep argument
validation and response serialization outside the critical section.

Outcome: Same-key refreshes serialize; unrelated keys remain concurrent.
Non-goals:
- changing token storage
- changing the refresh endpoint API

## Acceptance Criteria
- [AC-01] Same-key concurrent refreshes never invalidate an in-use token
  Satisfied by: S-1, S-2
  Verify: pytest tests/auth/test_refresh_race.py -q
- [AC-02] refresh_token() keeps its public signature and return type
  Satisfied by: S-2
  Verify: pytest tests/auth -q

## Risks
- [R-1] Per-key synchronization state could grow without bound
  Severity: high
  Mitigation: Remove idle entries and test the lifecycle after refresh completion.
- [R-2] Coarse locking could serialize unrelated tokens
  Severity: medium
  Mitigation: Key synchronization by token and cover unrelated-key concurrency.

## Verification
- [V-1] pytest tests/auth/test_refresh_race.py -q
  Expect: the race regression test passes deterministically
  Timeout: 60
- [V-2] pytest tests/auth -q
  Expect: the auth suite passes with zero failures and no new warnings
  Timeout: 60

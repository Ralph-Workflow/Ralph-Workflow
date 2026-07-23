---
type: issues
status: issues_found
---

## Summary

- [SUM-1] The refresh-race fix is correct, but the lock dictionary leaks entries under exception paths and one test asserts on timing instead of state.

## Issues

- [I-1] src/auth/refresh.py | high | Lock entry is not released when refresh raises, so the per-key dictionary grows on every failed refresh
- [I-2] tests/auth/test_refresh_race.py | medium | Race assertion sleeps 0.5s and checks elapsed time; assert on token state instead so the test cannot flake under load
- [I-3] src/auth/refresh.py | low | The module-level lock lacks a comment explaining why a dict-of-locks is safe here

## What Came Up Short

- [W-1] Exception paths were not exercised: no test refreshes a token whose backend write fails.
- [W-2] The concurrency test proves the happy path but relies on wall-clock timing.

## How To Fix

- [FIX-1] Wrap the critical section in try/finally so the lock entry is dropped on failure, and add a test that forces the backend write to raise.
- [FIX-2] Replace the elapsed-time assertion with a state assertion (token remains valid and exactly one refresh occurred).
- [FIX-3] Add a one-line comment on the dict-of-locks invariant (entries only exist while a refresh is in flight).

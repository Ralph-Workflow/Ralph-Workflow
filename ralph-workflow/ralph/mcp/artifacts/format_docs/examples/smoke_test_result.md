---
type: smoke_test_result
status: passed
output_file: tmp/auth-refresh-smoke/session.log
---

## Summary

- [SUM-1] Manual smoke of the auth service: logged in, forced a token near expiry, fired two concurrent refreshes, and confirmed the session stayed valid; every observation below was actually seen in the captured log, none is inferred.

## Observed Working

- [OK-1] Login issued a token and tmp/auth-refresh-smoke/session.log captured the session id
- [OK-2] Two concurrent refresh calls returned 200 and the same rotated token id
- [OK-3] A request using the pre-refresh token during the race window succeeded

## Observed Breaks

- [BR-1] Cosmetic only: the refresh endpoint logs a duplicate "refresh scheduled" line for the losing request (no functional impact; noted for follow-up, not a failure)

## Headless Guide Checks

- [HG-1] session capture — session id present in the output file
- [HG-2] tool activity — refresh calls visible in the request log
- [HG-3] completion signal — smoke script exited 0
- [HG-4] tmp artifact creation — output file written under tmp/

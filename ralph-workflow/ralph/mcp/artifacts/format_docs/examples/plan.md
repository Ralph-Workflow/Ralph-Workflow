---
type: plan
---

## Summary

- [SUM-1] {"context":"Concurrent refresh requests in src/auth/refresh.py can invalidate a token that another request is still using (race window between expiry check and refresh). Fix by serializing refresh per token key. Public API of refresh_token() must not change.","intent":"Eliminate the token-refresh race without changing the public auth API","scope_items":[{"text":"Add a failing regression test that reproduces the double-refresh race","category":"test","count":"1 test"},{"text":"Serialize refresh operations per token key in src/auth/refresh.py","category":"bugfix","count":"1 file"},{"text":"Verify the full auth test module still passes","category":"test"}]}

## Skills MCP

- [SK-1] {"skills":["test-driven-development","systematic-debugging"]}

## Steps

- [S-1] {"title":"Reproduce the race in a failing test","content":"Add tests/auth/test_refresh_race.py::test_concurrent_refresh_keeps_token_valid: start two concurrent refresh calls for the same token key and assert the first token is never invalidated while in use. It must FAIL against current code — run it and record the failure before moving on.","step_type":"file_change","targets":[{"path":"tests/auth/test_refresh_race.py","action":"create"}],"satisfies":["AC-01"],"expected_evidence":[{"kind":"test_name","ref":"tests/auth/test_refresh_race.py::test_concurrent_refresh_keeps_token_valid","note":"fails before the fix, passes after"}]}
- [S-2] {"title":"Serialize refresh per token key","content":"In src/auth/refresh.py, guard the check-then-refresh critical section with a per-token-key lock (dict of locks keyed by token id, created under a module lock). Do not change the refresh_token() signature or return type. Keep the lock scope minimal: acquire after argument validation, release before response serialization.","step_type":"file_change","targets":[{"path":"src/auth/refresh.py","action":"modify"}],"depends_on":["S-1"],"satisfies":["AC-01","AC-02"],"rationale":"A per-key lock removes the race while leaving unrelated tokens fully concurrent."}
- [S-3] {"title":"Prove the regression test now passes","content":"Run the new regression test and confirm it passes deterministically (three consecutive runs).","step_type":"verify","verify_command":"pytest tests/auth/test_refresh_race.py -q","depends_on":["S-2"]}
- [S-4] {"title":"Prove no auth behavior regressed","content":"Run the whole auth test module to confirm the lock did not change existing behavior or introduce deadlocks.","step_type":"verify","verify_command":"pytest tests/auth -q","depends_on":["S-3"]}

## Critical Files

- [CF-1] {"primary_files":[{"path":"src/auth/refresh.py","action":"modify","estimated_changes":"one per-key lock plus a guarded critical section (~20 lines)"},{"path":"tests/auth/test_refresh_race.py","action":"create","estimated_changes":"one concurrency regression test (~40 lines)"}],"reference_files":[{"path":"src/auth/tokens.py","purpose":"Token model and expiry fields read by the refresh path"}]}

## Design

- [D-1] {"acceptance_criteria":{"criteria":[{"id":"AC-01","description":"Two concurrent refresh calls for the same token never invalidate a token that is still in use.","verification_step":"pytest tests/auth/test_refresh_race.py -q","satisfied_by_steps":[1,2]},{"id":"AC-02","description":"The public refresh_token() signature and return type are unchanged.","verification_step":"pytest tests/auth -q","satisfied_by_steps":[2]}]},"non_goals":{"items":["No token-format or storage changes","No refresh-endpoint API changes"]}}

## Risks Mitigations

- [R-1] {"risk":"A per-key lock dictionary could grow without bound under token churn.","mitigation":"Drop the lock entry when the refresh completes and the key has no waiters; assert dictionary size stays bounded in the regression test.","severity":"medium"}
- [R-2] {"risk":"Coarse locking could serialize unrelated tokens and regress latency.","mitigation":"Lock per token key, never globally; S-4 runs the full auth module to catch timeouts.","severity":"low"}

## Verification

- [V-1] {"method":"pytest tests/auth/test_refresh_race.py -q","expected_outcome":"test_concurrent_refresh_keeps_token_valid passes; it failed before S-2","timeout_seconds":120}
- [V-2] {"method":"pytest tests/auth -q","expected_outcome":"entire auth module passes with zero failures and no new warnings","timeout_seconds":300}

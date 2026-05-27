# Marketing repair — execution-board freshness guard

- When: 2026-05-27 06:27 Europe/Berlin
- Tactic type: repaired
- Why now: the active loop had current `distribution_lane_latest.*` truth, but the shared execution-board surface could still be read in a stale state. That weakens the measurement-hold contract and can steer the marketer off an outdated board.

## What I changed
1. Patched `agents/marketing/marketing_loop_independent_verify.py` to fail closed when `drafts/marketing_execution_board_latest.md` has a stale or missing `Generated:` timestamp relative to runner/audit/lane artifacts.
2. Added regression coverage in `agents/marketing/tests/test_marketing_system.py` for a fresh-mtime / stale-content board case, and updated hold-state tests to include valid board timestamps.
3. Refreshed `drafts/marketing_execution_board_latest.md` and resynced `agents/marketing/logs/outcome_execution_board_latest.*` so the current hold-window truth surfaces match.

## Shared findings reused
- `distribution_lane_latest.json` / `.md` → current truthful lane is `measurement_hold`
- `marketing_execution_board_latest.md` → current board should stay the single follow-through truth surface
- `marketing_workflow_audit_latest.json` → primary bottleneck remains `distribution_and_message_to_primary_repo_conversion`
- `adoption_metrics_latest.md` → Codeberg remains flat, so fake-green truth artifacts are harmful

## Verification
- `python3 -m unittest agents.marketing.tests.test_marketing_system.MarketingLoopCertificationTests.test_independent_verifier_flags_stale_execution_board_generated_timestamp_during_hold agents.marketing.tests.test_marketing_system.MarketingLoopCertificationTests.test_independent_verifier_flags_stale_outcome_execution_board_status_during_hold agents.marketing.tests.test_marketing_system.MarketingLoopCertificationTests.test_independent_verifier_accepts_fresh_outcome_execution_board_status_during_hold` → OK
- `python3 agents/marketing/marketing_loop_independent_verify.py` now shows fresh execution-board artifacts; the only remaining blocker is the real one: primary repo adoption is still measurement-pending.

## Expected outcome
Future marketing runs should stop trusting a stale execution board during measurement holds, which reduces fake follow-through and improves odds that the next slot acts on current Codeberg-first truth instead of an obsolete board.

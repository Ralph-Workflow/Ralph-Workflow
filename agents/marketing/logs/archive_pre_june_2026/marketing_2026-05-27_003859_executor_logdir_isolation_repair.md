# Marketing repair — executor logdir isolation
Generated: 2026-05-27T00:38:59+02:00

## Why this run
- The latest execution board still said there was no truthful do-now packet in the active review window.
- Repeated architecture/hold churn meant the highest-leverage truthful action was a runtime repair, not another packet refresh.
- The measurement-hold executor was reading selector-side state that ignored executor-local log fixtures/state, which could falsely block a real packet or reschedule a post-hold wake to the wrong release time.

## What I changed
- Patched `agents/marketing/distribution_lane_executor.py` so short-window release fallback now resolves from executor-local marketing logs instead of leaking selector-global log state.
- Added an executor-local primary-repo-flat prep-repeat counter so execution-board packet gating uses the current run's log surface instead of unrelated selector-global history.
- Kept a narrow mock-aware fallback for the short-window helper so existing regression coverage that intentionally patches the selector helper still works.

## Verification
- `python3 -m unittest agents.marketing.tests.test_distribution_lane_executor_measurement_hold.DistributionLaneExecutorMeasurementHoldTests.test_execution_board_surfaces_primary_repo_flat_packet_for_github_issue_only_target_when_discovery_explicitly_recommends_it agents.marketing.tests.test_distribution_lane_executor_measurement_hold.DistributionLaneExecutorMeasurementHoldTests.test_primary_repo_flat_packet_reschedules_post_hold_rerun_when_short_window_moves_later -q` → OK
- `python3 -m unittest agents.marketing.tests.test_distribution_lane_executor_measurement_hold -q` → OK (69 tests)

## Result
- The execution board no longer falsely suppresses a truthful primary-repo-flat packet because of selector-global repeat-history bleed-through.
- Post-hold rerun scheduling now stays pinned to the executor-visible short-window truth instead of drifting to unrelated global-history release times.
- This improves the odds that the next real post-hold slot yields a truthful executable lane instead of another fake-progress guard/hold churn.

## Shared findings reused
- `agents/marketing/logs/marketing_workflow_audit_latest.json`
- `agents/marketing/logs/distribution_lane_latest.json`
- `drafts/marketing_execution_board_latest.md`
- `agents/marketing/logs/outcome_execution_board_latest.json`
- `agents/marketing/logs/reddit_execution_status_latest.json`
- `agents/marketing/logs/adoption_metrics_latest.md`

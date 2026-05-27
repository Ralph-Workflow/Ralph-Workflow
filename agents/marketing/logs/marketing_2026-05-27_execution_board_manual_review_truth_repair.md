# Execution-board manual-review truth repair

- Timestamp: 2026-05-27T16:23:56+02:00
- Type: distribution_architecture_repair
- Status: executed
- Target: marketing_execution_board / manual follow-through asset detection

## Why this repair ran
The latest execution board was still able to look empty even when a current `primary_repo_flat_manual_review_asset_latest.md` existed for manual-only publisher targets. That made the board under-report truthful Codeberg-first follow-through and pushed the loop toward another empty-board repair.

## Repair applied
- Updated `distribution_lane_executor._manual_outreach_assets_waiting_for_execution()` to source primary-repo-flat manual-review targets from `distribution_lane_selector._primary_repo_flat_manual_review_targets_waiting_for_execution(now)` instead of reusing the runtime-sendable target list.
- Kept the existing delivery/age guards, so already-delivered or stale manual assets still stay fail-closed.
- Added a regression test that proves the execution board now surfaces a manual publisher outreach asset when only manual-reviewable channels remain.

## Shared findings reused
- `agents/marketing/logs/market_intelligence_latest.json`
- `agents/marketing/logs/marketing_workflow_audit_latest.json`
- `agents/marketing/logs/distribution_lane_latest.json`
- `drafts/marketing_execution_board_latest.md`
- `agents/marketing/logs/adoption_metrics_latest.json`
- `agents/marketing/logs/primary_repo_flat_contact_discovery_latest.json`
- `agents/marketing/logs/reddit_post_analysis.json`

## Verification
- `python3 -m unittest agents.marketing.tests.test_distribution_lane_executor_measurement_hold.DistributionLaneExecutorMeasurementHoldTests.test_execution_board_lists_manual_review_asset_when_only_manual_channels_remain agents.marketing.tests.test_distribution_lane_executor_measurement_hold.DistributionLaneExecutorMeasurementHoldTests.test_execution_board_empty_marker_yields_to_manual_review_asset agents.marketing.tests.test_outcome_execution_board_runner.OutcomeExecutionBoardRunnerTests.test_sync_from_execution_refreshes_execution_board_after_latest_lane_persist -v`
- Result: passed

## Result
- Manual-only publisher follow-through can now appear on the execution board when it is genuinely still waiting.
- The fix does not force a fake packet today; it removes a board blind spot so the next truthful manual-review slot surfaces correctly instead of collapsing into another empty-board repair.

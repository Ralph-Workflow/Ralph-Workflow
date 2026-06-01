# Reddit blocked manual-asset guard repair
Generated: 2026-05-27T05:20:18+02:00

## Why this ran
- The execution board was still surfacing a `Manual community discussion asset` even though Reddit execution is currently fail-closed from this environment.
- That was fake progress: the latest Reddit execution status is `execution_blocked`, adoption is still flat, and the active distribution brief already said not to treat another Reddit pass as shippable.

## Repair applied
- Patched `agents/marketing/distribution_lane_executor.py` so blocked Reddit state suppresses:
  - resurfacing stale manual Reddit-discussion assets on the execution board
  - regenerating a fresh Reddit discussion handoff packet during `distribution_architecture_repair`
  - generic manual-outreach asset relisting when the asset is really a Reddit discussion packet and Reddit is blocked
- Added regression coverage in:
  - `agents/marketing/tests/test_distribution_lane_executor_measurement_hold.py`
  - `agents/marketing/tests/test_marketing_system.py`

## Verification
- `python3 -m unittest agents.marketing.tests.test_distribution_lane_executor_measurement_hold.DistributionLaneExecutorMeasurementHoldTests.test_distribution_architecture_repair_creates_manual_reddit_discussion_asset_when_monitor_has_live_opportunities agents.marketing.tests.test_distribution_lane_executor_measurement_hold.DistributionLaneExecutorMeasurementHoldTests.test_distribution_architecture_repair_skips_manual_reddit_discussion_asset_when_reddit_execution_is_blocked agents.marketing.tests.test_marketing_system.DistributionLaneExecutorTests.test_marketing_execution_board_hides_reddit_manual_discussion_asset_when_execution_is_blocked -q`
- `python3 -m unittest agents.marketing.tests.test_marketing_system.DistributionLaneExecutorTests.test_marketing_execution_board_hides_reddit_execution_check_after_aligned_rerun_exists agents.marketing.tests.test_marketing_system.DistributionLaneExecutorTests.test_marketing_execution_board_ignores_overwritten_distribution_brief_from_old_reddit_log agents.marketing.tests.test_distribution_lane_selector_repair_pause.DistributionLaneSelectorRepairPauseTests.test_report_guard_blocks_reddit_execution_check_override agents.marketing.tests.test_distribution_lane_selector_repair_pause.DistributionLaneSelectorRepairPauseTests.test_reddit_cooldown_blocks_reddit_execution_check_override -q`
- `python3 agents/marketing/outcome_execution_board_runner.py`

## New truthful state
- `drafts/marketing_execution_board_latest.md` no longer surfaces a Reddit manual discussion asset.
- The latest board now says no truthful do-now packet exists in the current review window and points to the remaining blocked/manual-only publisher targets instead.
- The follow-through runner converted the slot into a Codeberg-first manual publisher review asset for `TLDL` and `ComputingForGeeks` instead of inventing another Reddit move.

## Next metric
- Watch for a genuine blocker change or a new sendable publisher/contact path before resurfacing another do-now distribution lane.

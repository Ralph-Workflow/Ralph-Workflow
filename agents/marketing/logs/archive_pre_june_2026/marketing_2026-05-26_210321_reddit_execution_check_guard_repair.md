# Reddit execution-check guard repair
Generated: 2026-05-26T21:03:21+02:00

## Why this ran
After the short-window blocker cleared, lane selection was still force-routing to `reddit_execution_check` whenever the board was empty and the Reddit browser session was ready.
That was false-positive executability: the latest Reddit monitor state was `report_guard_skip` with partial coverage and only medium-low mention fit, so the watchdog could not truthfully ship a live action.

## Repair applied
- Added `_reddit_execution_check_actionable(now)` to `agents/marketing/distribution_lane_selector.py`.
- The selector now requires a currently actionable Reddit report (not just browser readiness / no cooldown) before overriding into `reddit_execution_check`.
- This blocks the empty-board loop from treating guarded Reddit state as the only executable lane.

## Verification
- `python3 -m unittest agents.marketing.tests.test_distribution_lane_selector_repair_pause.DistributionLaneSelectorRepairPauseTests.test_reddit_cooldown_blocks_reddit_execution_check_override agents.marketing.tests.test_distribution_lane_selector_repair_pause.DistributionLaneSelectorRepairPauseTests.test_report_guard_blocks_reddit_execution_check_override`
- Real selector re-check after patch returned `distribution_architecture_repair` instead of `reddit_execution_check` at 2026-05-26T21:03:21.

## Expected outcome
Stop wasting active-loop slots on guarded Reddit runs until the report is healthy enough to support a real post. That should improve the odds that the next slot either performs a truthful runtime repair or reaches a genuinely executable distribution lane.

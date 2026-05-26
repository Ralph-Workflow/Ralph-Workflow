# Distribution architecture guard-window truth repair

- Generated: `2026-05-26T23:01:54`
- Why now: the current execution board and latest lane both said the review window needed a same-window repair/follow-through, but `outcome_execution_board_runner` still reused a **2026-05-25** guard-follow-through artifact for the same fingerprint. That was fake progress.

## Repair applied
- Patched `agents/marketing/outcome_execution_board_runner.py` so guard-follow-through and guard-pause reuse only stays valid inside the current short review window.
- Patched `agents/marketing/run.py` with the same current-window staleness rule so the main marketer loop cannot revive stale guard artifacts either.
- Added regression coverage in:
  - `agents/marketing/tests/test_outcome_execution_board_runner.py`
  - `agents/marketing/tests/test_run_repair_mode.py`

## Verification
- `python3 -m unittest agents.marketing.tests.test_outcome_execution_board_runner agents.marketing.tests.test_run_repair_mode.RunRepairModeTests.test_guard_follow_through_reuse_is_stale_when_it_predates_current_short_window -q` → OK
- `python3 -m unittest agents.marketing.tests.test_run_repair_mode.RunRepairModeTests.test_main_reuses_existing_distribution_architecture_repair_when_truth_is_unchanged agents.marketing.tests.test_run_repair_mode.RunRepairModeTests.test_main_reuses_existing_distribution_architecture_guard_pause_when_truth_is_unchanged agents.marketing.tests.test_distribution_lane_selector_repair_pause.DistributionLaneSelectorRepairPauseTests.test_repeated_distribution_architecture_repairs_trigger_third_strike_reason -q` → OK
- `python3 agents/marketing/outcome_execution_board_runner.py` → OK; selected `distribution_architecture_repair` for the current fingerprint instead of reusing the stale prior-day guard-follow-through artifact.

## Result
- The loop now respects review-window truth for distribution-architecture guard reuse.
- That keeps the next slot available for a truthful same-window repair instead of suppressing it with a stale alias.

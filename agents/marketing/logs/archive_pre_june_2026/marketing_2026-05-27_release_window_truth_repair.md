# Release-window truth repair for empty-board escalation

- Timestamp: `2026-05-27T16:34:09+02:00`
- Status: `executed`
- Target: `distribution_lane_selector / active release-window empty-board guard`
- Shared findings reused:
  - `agents/marketing/logs/market_intelligence_latest.json`
  - `agents/marketing/logs/marketing_workflow_audit_latest.json`
  - `agents/marketing/logs/distribution_lane_latest.json`
  - `drafts/marketing_execution_board_latest.md`
  - `agents/marketing/logs/adoption_metrics_latest.json`

## What changed
- Separated **release-window active/cleared** truth from the **short-window congestion threshold**.
- Prevented `no_short_window_idle_empty_board` from firing while a live release window still exists.
- Added a regression test for the real failure mode: one recent live external action, active release window, guarded empty board, and a newer repair already logged after guard pause start.

## Verification
- Passed:
  - `python3 -m unittest agents.marketing.tests.test_distribution_lane_selector_repair_pause.DistributionLaneSelectorRepairPauseTests.test_active_release_window_without_congestion_keeps_guard_pause_when_concrete_repair_already_ran_after_pause_started agents.marketing.tests.test_distribution_lane_selector_repair_pause.DistributionLaneSelectorRepairPauseTests.test_active_short_window_keeps_guard_pause_when_concrete_repair_already_ran_after_pause_started agents.marketing.tests.test_distribution_lane_selector_repair_pause.DistributionLaneSelectorRepairPauseTests.test_cleared_short_window_escalates_to_repair_after_newer_repair_for_same_fingerprint -v`
- After patch, `choose_distribution_lane(now=2026-05-27T16:24:00)` no longer selected `distribution_architecture_repair`; it fell back to `owned_content` with release timestamp `2026-05-27T18:35:08`.
- Refreshed execution-board truth snapshot at `2026-05-27T16:34:22.832687`; it now records `selected_lane=owned_content` and `do_now_lane_available=false` instead of another duplicate architecture repair.

## Outcome
Stopped a false-cleared-window repair loop and refreshed the board/selector truth surfaces for the scheduled post-hold rerun.

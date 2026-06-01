# Measurement Hold Release Overdue-Idle Cron Repair
Generated: 2026-05-26T13:01:28+02:00

## Why this repair ran
- The current execution board still says there is no truthful do-now packet before the short review window clears.
- Live cron state still contained an overdue `marketing-measurement-hold-release` one-shot (`80efd280-69cb-4d92-b0e6-8795a96ecebb`) even though the fresh post-hold rerun was already scheduled for `2026-05-26T13:14:38`.
- Leaving overdue duplicate wakeups alive risks blurring or stealing the first truthful post-hold slot.

## What changed
- Patched `agents/marketing/distribution_lane_executor.py` so overdue idle one-shot release jobs are treated as conflicting cron entries during reschedule cleanup.
- Updated current-wake lookup to prefer a future or running release over a stale overdue leftover.
- Added regression coverage in `agents/marketing/tests/test_distribution_lane_executor_measurement_hold.py`.
- Removed live stale cron job `80efd280-69cb-4d92-b0e6-8795a96ecebb`.

## Verification
- `python3 -m unittest agents.marketing.tests.test_distribution_lane_executor_measurement_hold.DistributionLaneExecutorMeasurementHoldTests.test_measurement_hold_scheduler_removes_overdue_idle_release_job_before_reschedule agents.marketing.tests.test_distribution_lane_executor_measurement_hold.DistributionLaneExecutorMeasurementHoldTests.test_current_measurement_hold_release_run_prefers_future_job_over_overdue_idle_one agents.marketing.tests.test_distribution_lane_executor_measurement_hold.DistributionLaneExecutorMeasurementHoldTests.test_measurement_hold_scheduler_removes_stale_running_release_job_before_reschedule -q` → OK
- `python3 -m unittest agents.marketing.tests.test_run_repair_mode.RunRepairModeTests.test_main_reuses_existing_distribution_architecture_guard_pause_when_truth_is_unchanged agents.marketing.tests.test_outcome_execution_board_runner agents.marketing.tests.test_distribution_lane_selector_repair_pause.DistributionLaneSelectorRepairPauseTests.test_guarded_empty_board_pauses_after_guard_follow_through_already_logged -q` → OK
- Live cron state now shows a single `marketing-measurement-hold-release` job: `3aa0e51d-40a8-4327-87ec-ae45babfc02f` scheduled for `2026-05-26T13:14:38+02:00`.

## Outcome
The post-hold rerun path is cleaner now: one truthful wake, aligned with the current re-entry contract, and less chance of duplicate-slot churn.

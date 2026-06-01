# Measurement-hold skip reuse repair

- Generated: 2026-05-26T20:24:32.351522
- Goal: Stop fake-progress churn during active measurement holds by reusing the current skip artifact when hold truth is unchanged.

## What was wrong
- The loop was still writing fresh `measurement_hold_skip` logs during the same unchanged hold window.
- The current execution board is still truthfully empty until `2026-05-26T20:55:18`, so duplicate skip artifacts were just noise.

## What changed
- Added `run._latest_measurement_hold_skip_log()` to find an existing matching cooldown-skip artifact.
- Updated `run._write_measurement_hold_skip_log()` to reuse that artifact when `source_log` and `hold_until` still match.
- Added regression coverage for both reuse and truth-change cases.

## Verification
```
python3 -m unittest agents.marketing.tests.test_run_repair_mode.RunRepairModeTests.test_write_measurement_hold_skip_log_reuses_matching_log_in_same_window agents.marketing.tests.test_run_repair_mode.RunRepairModeTests.test_write_measurement_hold_skip_log_writes_new_when_hold_truth_changes agents.marketing.tests.test_run_repair_mode.RunRepairModeTests.test_main_reuses_existing_distribution_architecture_guard_pause_when_truth_is_unchanged -v
```

Result: **OK**

## Expected effect
- The next cron ticks inside the same hold window should reuse the existing skip artifact instead of generating more fake-progress logs.

# Primary-repo-flat status churn guard repair
Generated: 2026-05-28T03:58:45+02:00

## Why this ran
- Codeberg is still flat in the active measurement window, so fake progress inside the hold window is actively harmful.
- The current lane is `measurement_hold` until 2026-05-28T09:12:15.
- The hold follow-through path was still able to rewrite the same primary-repo-flat status packet and make packet churn look like fresh work.

## What changed
- Added a fingerprint-based reuse guard to the primary-repo-flat status packet writer in `agents/marketing/distribution_lane_executor.py`.
- Patched `_refresh_manual_execution_assets(...)` so it only reports a primary-repo-flat status refresh when the packet truth actually changed.
- Added regression coverage in `agents/marketing/tests/test_distribution_lane_executor_measurement_hold.py`.
- Refreshed today's shared status packet once so the latest artifact now carries the churn guard metadata.

## Verification
- `python3 -m unittest agents.marketing.tests.test_distribution_lane_executor_measurement_hold.DistributionLaneExecutorMeasurementHoldTests.test_primary_repo_flat_status_packet_reuse_skips_duplicate_rewrite agents.marketing.tests.test_distribution_lane_executor_measurement_hold.DistributionLaneExecutorMeasurementHoldTests.test_refresh_manual_execution_assets_omits_primary_status_when_truth_unchanged -v` → OK
- `python3 -m unittest agents.marketing.tests.test_distribution_lane_executor_measurement_hold -q` → OK

## Outcome
Future hold-window follow-through passes will reuse the current primary-repo-flat status packet unless the recent-contact set or non-sendable target set actually changes, which reduces fake packet churn while preserving the Codeberg-first truth.

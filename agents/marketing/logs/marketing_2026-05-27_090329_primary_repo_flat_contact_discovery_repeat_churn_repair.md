# Primary-repo-flat contact discovery repeat-churn repair
Generated: 2026-05-27T09:03:29+02:00

## Why this ran
- `drafts/marketing_execution_board_latest.md` still had no truthful do-now packet.
- `agents/marketing/logs/distribution_lane_latest.json` had already escalated to `distribution_architecture_repair`.
- `agents/marketing/logs/marketing_workflow_audit_latest.json` called the repeated primary-repo-flat packet refreshes repetitive and low-signal.
- `agents/marketing/logs/adoption_metrics_latest.json` still showed flat Codeberg adoption.

## Repair shipped
- Added a 48-hour prepared-only packet churn detector in `agents/marketing/run.py`.
- Empty-board runs now refresh `primary_repo_flat_contact_discovery_latest.json` when that churn threshold is hit, even if the artifact is not yet stale by age.
- Added regression coverage in `agents/marketing/tests/test_run_repair_mode.py`.

## Verification
- `python3 -m unittest agents.marketing.tests.test_run_repair_mode.RunRepairModeTests.test_refresh_primary_repo_flat_contact_discovery_for_empty_board_refreshes_stale_artifact agents.marketing.tests.test_run_repair_mode.RunRepairModeTests.test_refresh_primary_repo_flat_contact_discovery_for_empty_board_skips_fresh_artifact agents.marketing.tests.test_run_repair_mode.RunRepairModeTests.test_refresh_primary_repo_flat_contact_discovery_for_empty_board_forces_refresh_after_repeated_prepared_only_packet_churn -q` → OK

## Expected effect
The next empty-board rerun should stop trusting a merely fresh-by-timestamp publisher discovery artifact when the same packet keeps getting prepared without delivery. It will search for fresh publisher targets sooner instead.

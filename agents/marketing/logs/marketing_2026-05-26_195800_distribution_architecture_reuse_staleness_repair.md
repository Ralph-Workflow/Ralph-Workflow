# Distribution Architecture Reuse Staleness Repair

- Generated: `2026-05-26T19:58:00`
- Why now: the main marketing loop was still emitting duplicate same-fingerprint architecture repairs during the active hold window even after the standalone execution-board runner had been fixed.

## Shared findings reused
- `distribution_lane_latest.json` → active lane truth is still `distribution_architecture_guard_pause` in the current hold window.
- `outcome_execution_board_latest.json` → standalone outcome-runner reuse was already repaired, so the remaining churn source was `run.py`.
- `marketing_2026-05-26_192143_distribution_architecture_churn_guard_repair.json` → duplicate same-fingerprint repairs were still landing from the main loop.
- `marketing_2026-05-26_194110_outcome_runner_reuse_repair.md` → isolated the remaining bug to the main loop's staleness/reuse check.

## Repair applied
- Removed the mtime-based invalidation from `run.py`'s distribution-architecture reuse check.
- Kept the fail-closed guard: reuse still goes stale if the referenced artifact or execution log disappears.
- Added regression coverage proving newer latest-alias files do not force a fresh repair when the fingerprint truth is unchanged.

## Verification
- `python3 -m unittest agents.marketing.tests.test_run_repair_mode.RunRepairModeTests.test_distribution_architecture_guard_execution_stale_only_when_artifact_or_log_missing agents.marketing.tests.test_run_repair_mode.RunRepairModeTests.test_distribution_architecture_guard_execution_ignores_latest_alias_mtime_churn agents.marketing.tests.test_run_repair_mode.RunRepairModeTests.test_main_reuses_existing_distribution_architecture_repair_when_truth_is_unchanged agents.marketing.tests.test_outcome_execution_board_runner -q` → OK
- `python3 agents/marketing/run.py` → OK
- Live hold-window run reused `/home/mistlight/.openclaw/workspace/agents/marketing/logs/marketing_2026-05-26_060554_distribution_architecture_guard_pause.json` instead of creating another duplicate `distribution_architecture_churn_guard_repair` log.

## Result
- The current hold window now stays truthful: no more fake-progress repair churn from `run.py` while the fingerprint and blocker state are unchanged.
- The scheduled post-hold release at `2026-05-26T20:55:18` remains the next honest chance for a fresh executable lane.

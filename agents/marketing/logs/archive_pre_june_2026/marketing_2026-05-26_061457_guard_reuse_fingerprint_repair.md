# Guard reuse fingerprint repair

- Generated: `2026-05-26T06:14:57.808654`
- Problem: repeated guard-pause churn was still generating fresh execution logs inside the same unchanged hold window.
- Fix: persist execution-board fingerprints on guard logs, allow semantic reuse for legacy logs, and stop treating auto-refreshed latest artifacts as stale truth by mtime alone.
- Files: `agents/marketing/run.py`, `agents/marketing/tests/test_run_repair_mode.py`
- Tests: `python3 -m unittest agents.marketing.tests.test_run_repair_mode.RunRepairModeTests.test_latest_distribution_architecture_guard_execution_accepts_legacy_reason_match_without_fingerprint agents.marketing.tests.test_run_repair_mode.RunRepairModeTests.test_distribution_architecture_guard_execution_stale_only_when_artifact_or_log_missing agents.marketing.tests.test_run_repair_mode.RunRepairModeTests.test_main_reuses_existing_distribution_architecture_guard_pause_when_truth_is_unchanged -v`
- Verification: current helper lookup now resolves an existing guard-pause execution for the unchanged 2026-05-26 window instead of missing it.

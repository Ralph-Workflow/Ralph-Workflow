# StackOverflow exhausted-slot proof-asset branch repair

- Generated: `2026-05-26T06:27:19+02:00`
- Problem: after the post-cooldown StackOverflow slot burned without a fresh answer, the selector could still fall back to `measurement_hold` even when a truthful `repo_conversion_proof_asset` branch should be available.
- Fix: add a dedicated `repo_conversion_proof_asset` branch for the exhausted-slot state, but gate it behind `not _execution_board_has_no_truthful_do_now_packet(now)` so third-strike empty-board repair/guard paths still win when the board is genuinely empty.
- Files: `agents/marketing/distribution_lane_selector.py`, `agents/marketing/tests/test_distribution_lane_selector_repair_pause.py`
- Tests: `python3 -m unittest agents.marketing.tests.test_distribution_lane_selector_repair_pause.DistributionLaneSelectorRepairPauseTests.test_choose_distribution_lane_prefers_repo_proof_asset_after_exhausted_stackoverflow_slot agents.marketing.tests.test_distribution_lane_selector_repair_pause.DistributionLaneSelectorRepairPauseTests.test_choose_distribution_lane_escalates_guarded_empty_board_to_concrete_repair agents.marketing.tests.test_run_repair_mode.RunRepairModeTests.test_refresh_distribution_lane_after_execution_skips_duplicate_action_log`
- Verification: the new selector test now prefers `repo_conversion_proof_asset` for the exhausted StackOverflow scenario, while the guarded empty-board escalation test still resolves to `distribution_architecture_repair` instead of being shadowed by the new branch.
- Runtime check: `python3 agents/marketing/run.py` still truthfully reuses the active `distribution_architecture_guard_pause` for the unchanged 2026-05-26 review window, confirming the repair did not create fake outbound churn.

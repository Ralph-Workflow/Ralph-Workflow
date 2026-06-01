# Distribution lane execution: distribution_architecture_repair

- Timestamp: `2026-05-26T19:31:35+02:00`
- Goal: keep the post-hold rerun from falling back into another fake-empty distribution-architecture repair loop.

## What I changed
- Patched `agents/marketing/distribution_lane_selector.py` so the primary-repo-flat prepared-only repeat guard no longer suppresses the already-current publisher packet after the documented short-window release has actually cleared.
- Patched `_manual_outreach_assets_waiting_for_execution(...)` with the same release-aware rule so the execution board can surface that packet again after the hold instead of staying falsely empty.
- Added regression coverage for both behaviors in `agents/marketing/tests/test_distribution_lane_selector_repair_pause.py`.

## Why this was the highest-leverage action
- The current board is truthfully empty until `2026-05-26T20:55:18`.
- The same primary-repo-flat packet had already been prepared twice, and that repeat guard risked suppressing the *current* packet even after the hold window cleared.
- Without this fix, the scheduled post-hold rerun could have landed in another empty-board repair churn loop instead of surfacing the existing Codeberg-first packet for real follow-through.

## Verification
- `python3 -m unittest agents.marketing.tests.test_distribution_lane_selector_repair_pause.DistributionLaneSelectorRepairPauseTests.test_primary_repo_flat_manual_asset_reappears_after_post_hold_release_even_if_repeat_blocked agents.marketing.tests.test_distribution_lane_selector_repair_pause.DistributionLaneSelectorRepairPauseTests.test_post_hold_release_reuses_current_primary_repo_flat_packet_even_if_prep_repeat_threshold_was_hit -q`
- `python3 -m unittest agents.marketing.tests.test_distribution_lane_selector_repair_pause.DistributionLaneSelectorRepairPauseTests.test_primary_repo_flat_manual_asset_is_hidden_when_post_hold_only_and_repeat_blocked agents.marketing.tests.test_distribution_lane_selector_repair_pause.DistributionLaneSelectorRepairPauseTests.test_post_hold_only_primary_repo_flat_packet_yields_guard_pause_instead_of_fake_do_now -q`

## Expected effect
- Before `2026-05-26T20:55:18`: the packet stays hidden and the board remains truthfully empty.
- After `2026-05-26T20:55:18`: the same current publisher packet can become the truthful follow-through lane instead of being blocked just because it was previously prepared during the hold.

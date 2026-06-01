# Rolling short-window truth repair — 2026-05-26 20:24 Europe/Berlin

## Action
Patched the distribution lane selector so a post-hold rerun refreshes execution-board truth when newer live actions extend the rolling short review window past the board's earlier release time.

## Why this was the highest-leverage executable move now
- Codeberg adoption is still flat, so a stale do-now packet would waste the slot.
- The current board says no truthful do-now packet exists before 2026-05-26T20:55:18.
- A selector probe at 2026-05-26T20:56:00 showed a bug: it resurfaced `primary_repo_flat_contact_handoff_packet` even though newer live actions had extended congestion until 2026-05-26T22:47:35.
- Blocking that branch alone exposed another bad fallback into `owned_content`, so the truthful fix was a concrete `distribution_architecture_repair` path.

## Files changed
- `agents/marketing/distribution_lane_selector.py`
- `agents/marketing/tests/test_distribution_lane_selector_repair_pause.py`

## Verification
- `python3 -m unittest agents.marketing.tests.test_distribution_lane_selector_repair_pause.DistributionLaneSelectorRepairPauseTests.test_choose_distribution_lane_refreshes_board_when_rolling_short_window_extends_past_post_hold_release agents.marketing.tests.test_distribution_lane_selector_repair_pause.DistributionLaneSelectorRepairPauseTests.test_primary_repo_flat_manual_asset_reappears_after_post_hold_release_even_if_repeat_blocked agents.marketing.tests.test_distribution_lane_selector_repair_pause.DistributionLaneSelectorRepairPauseTests.test_post_hold_release_reuses_current_primary_repo_flat_packet_even_if_prep_repeat_threshold_was_hit -v`
- Selector probe:
  - `2026-05-26T20:24:00` → `distribution_architecture_guard_pause`
  - `2026-05-26T20:56:00` → `distribution_architecture_repair`

## Expected outcome
The first rerun after the earlier post-hold timestamp will refresh the execution-board truth when congestion has been extended, instead of counting a stale packet or owned-content fallback as real progress.

# Post-hold board truthfulness repair
Generated: 2026-05-26T04:50:47+02:00

## Why this was the highest-leverage action now
- Codeberg adoption is still flat, so another fake-active lane would waste the slot.
- The execution board only exposed a post-hold publisher packet, but the selector could still treat that packet as a live do-now manual asset.
- That mismatch let the loop fall back to `owned_content`/`measurement_hold` paths instead of escalating into the truthful distribution-architecture guard/repair path.
- Fixing that truthfulness bug improves the already-scheduled post-hold rerun instead of re-delivering blocked packets in the current window.

## Shared findings reused
- `agents/marketing/logs/distribution_lane_latest.json` → latest reasons explicitly said the primary-repo-flat packet was post-hold only.
- `drafts/marketing_execution_board_latest.md` → the only waiting packet was blocked until the short-window congestion clears.
- `agents/marketing/logs/marketing_workflow_audit_latest.json` / `.md` → same-run code/test/process repair is the correct move while outcome signals stay flat.
- `agents/marketing/logs/adoption_metrics_latest.json` → Codeberg remains the primary success gate.
- `agents/marketing/logs/reddit_post_analysis_latest.json` → Reddit was still not a truthful substitute lane.

## Repair applied
- Patched `agents/marketing/distribution_lane_selector.py` so a board that is explicitly post-hold-only stays non-truthful even if a current manual packet file exists on disk.
- Kept stale explicit empty-board markers subordinate to genuinely waiting manual assets, so the broader empty-board logic still behaves correctly.
- Added regression coverage for both the direct board check and the full selector path when the runtime release timestamp is missing but the board still documents an active short-window hold.

## Verification
- `python3 -m unittest agents.marketing.tests.test_distribution_lane_selector_repair_pause.DistributionLaneSelectorRepairPauseTests.test_execution_board_post_hold_only_packet_counts_as_no_truthful_do_now agents.marketing.tests.test_distribution_lane_selector_repair_pause.DistributionLaneSelectorRepairPauseTests.test_execution_board_post_hold_only_packet_stays_non_truthful_even_if_manual_asset_exists agents.marketing.tests.test_distribution_lane_selector_repair_pause.DistributionLaneSelectorRepairPauseTests.test_choose_distribution_lane_repairs_empty_post_hold_board_even_without_runtime_release_timestamp -q` → `OK`
- `python3 -m unittest agents.marketing.tests.test_distribution_lane_selector_repair_pause agents.marketing.tests.test_distribution_lane_executor_measurement_hold -q` → `OK` (113 tests)
- Live selector spot-check at `2026-05-26T02:37:00Z` now returns `distribution_architecture_guard_pause` instead of another fake do-now content fallback.

## Expected marketing effect
- The loop will stop misclassifying post-hold-only publisher packets as immediately executable lanes.
- Hold-window reruns should re-enter on a truer distribution-architecture state instead of spending another slot on misleading owned-content/idle fallbacks.
- That improves the odds that the scheduled post-hold slot surfaces a genuinely executable Codeberg-first action.
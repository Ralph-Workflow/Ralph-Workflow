# Primary-repo-flat packet currency guard repair
Generated: 2026-05-25T00:26:00+02:00

## Why this was the highest-leverage action now
- The short review window is still active until 2026-05-25T02:05:05, so sending another external/manual action now would blur measurement.
- Fresh publisher discovery now includes new executable targets (`ToolChase`, `Beam`), which means a stale packet-delivery guard would keep the board trapped in the wrong state.
- The execution board must distinguish between three cases truthfully: stale packet, refreshed packet still blocked by the hold window, and genuinely re-deliverable packet after the hold clears.

## Shared findings reused
- `agents/marketing/logs/adoption_metrics_latest.json` → Codeberg is still flat and remains the primary outcome gate.
- `agents/marketing/logs/distribution_lane_latest.json` → current lane is still `measurement_hold` with short-window release at `2026-05-25T02:05:05`.
- `drafts/marketing_execution_board_latest.md` → the prior board still treated the publisher packet as already delivered instead of reflecting the refreshed target set.
- `agents/marketing/logs/primary_repo_flat_contact_discovery_latest.json` → fresh executable publisher targets now include `ToolChase` and `Beam`.
- `agents/marketing/logs/reddit_post_analysis_latest.json` → Reddit reuse risk remains elevated, so this slot was better spent on a truth-preserving runtime repair.
- `agents/marketing/logs/comparison_backlink_queue_latest.json` → comparison follow-through is already current and should not consume this slot.

## Repair applied
- Tightened `agents/marketing/distribution_lane_executor.py` so prior primary-repo-flat manual-delivery logs only block the lane when the delivered packet is still current for the same target set and still contains live-listing proof.
- Added a currency check to the execution board so a stale publisher packet is not treated as available just because a file exists.
- Added hold-window gating so a refreshed publisher packet is acknowledged truthfully during the active congestion window instead of being surfaced as a do-now asset too early.
- Updated discovery/executor regression tests to cover stale-packet hiding, refreshed-packet blocking during the hold window, advertise-page discovery, and placeholder-email filtering.
- Refreshed the canonical publisher packet and execution board from the latest discovery so the board now points to the truthful post-hold lane.

## Verification
- `python3 -m unittest agents.marketing.tests.test_distribution_lane_executor_measurement_hold agents.marketing.tests.test_primary_repo_flat_contact_discovery agents.marketing.tests.test_distribution_lane_selector_repair_pause agents.marketing.tests.test_marketing_system`
- Result: `OK` (180 tests).
- Refreshed board: `/home/mistlight/.openclaw/workspace/drafts/marketing_execution_board_latest.md`
- Refreshed packet: `/home/mistlight/.openclaw/workspace/drafts/primary_repo_flat_contact_handoff_packet_latest.md`

## Expected marketing effect
- The post-hold rerun scheduled for 2026-05-25T02:05:05 now sees a truthful fresh publisher packet (`ToolChase`, `Beam`) instead of suppressing it behind an out-of-date delivery log.
- The active hold window is still respected, so the loop avoids fake progress while materially improving the next executable Codeberg-first slot.

# Post-hold Release Alignment Repair
Generated: 2026-05-26T13:25:00+02:00

## Why this repair ran
- The post-hold cron wake fired from a stale one-shot scheduled for 2026-05-26T13:14:38 even though the latest short review-window clearance had moved to 2026-05-26T13:22:23.
- The latest execution board and post-hold contract were still echoing that earlier rerun timestamp, which blurred the first truthful post-hold slot.

## What changed
- Patched `agents/marketing/distribution_lane_executor.py` so post-hold release truth always resolves to the later of the requested rerun time and the live short-window clearance.
- Hid stale scheduled reruns from the execution board when they fall before the current short-window release boundary.
- Removed the leftover stale cron one-shot `3aa0e51d-40a8-4327-87ec-ae45babfc02f`.
- Refreshed the latest execution board and post-hold re-entry contract with the repaired timing logic.

## Verification
- Targeted release-alignment tests passed.
- Broader marketing executor / outcome-board / selector regression suites passed (132 tests).
- `openclaw cron list --json` no longer shows a `marketing-measurement-hold-release` one-shot.
- Refreshed board: /home/mistlight/.openclaw/workspace/drafts/2026-05-26_marketing_execution_board.md
- Refreshed contract: /home/mistlight/.openclaw/workspace/drafts/post_hold_distribution_reentry_latest.md

## Outcome
- The early-release scheduling failure is repaired, stale rerun state is cleared, and future post-hold artifacts now track the live short-window clearance instead of an older wake time.

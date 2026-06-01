# Recent Live External Dedupe Repair
Generated: 2026-05-25T09:30:32+02:00

## Why this repair happened
- The selector was double-counting a single NxCode publisher send because the legacy SMTP log stored the email subject inside `channel.subject` while the canonical publisher-outreach log stored it at the top level.
- That false duplicate inflated short-window congestion and kept the loop in a stronger hold state than the real lane state justified.
- The execution board was still empty, so another fake packet refresh would have been noise rather than progress.

## Repair applied
- Taught `_live_external_event_key()` to reuse `channel.subject` and nested recipient data when deduping legacy/canonical email logs.
- Added regression coverage for the exact NxCode-shaped legacy log variant.
- Added regression coverage for idle `measurement_hold` states with an empty execution board and no short-window release, so that state escalates into `distribution_architecture_repair` instead of another fake-idle hold.

## Verification
- `python3 -m unittest agents.marketing.tests.test_distribution_lane_selector_repair_pause.DistributionLaneSelectorRepairPauseTests.test_recent_live_external_action_count_dedupes_legacy_email_when_subject_only_exists_inside_channel agents.marketing.tests.test_distribution_lane_selector_repair_pause.DistributionLaneSelectorRepairPauseTests.test_idle_measurement_hold_with_empty_execution_board_escalates_to_distribution_architecture_repair -q`
- Result: OK
- Live selector re-check at `2026-05-25T09:24:00` now reports `1 live external marketing action(s) already shipped in the last 6 hours` instead of `2`.

## Current truth after the repair
- NxCode is still a genuinely fresh live external action in the current 6-hour window.
- The execution board is still empty, but the false duplicate-send pressure is gone.
- If the board stays empty after the remaining live-action window is no longer the deciding blocker, the selector now has a tested path to escalate into structural repair instead of another fake-idle hold.

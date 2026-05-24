# Marketing execution — Reddit next-window truthfulness repair

- Timestamp: 2026-05-24 16:48 Europe/Berlin
- Action: stop the Reddit next-window packet generator from claiming a fresh packet when Reddit is execution-blocked or when zero actionable entries exist
- Channel: marketing loop runtime
- Status: executed

## Why this was the highest-leverage move now
- The live lane board is in `measurement_hold`, so another outbound packet would have been fake progress.
- `marketing_loop_runner_latest.json` showed `reddit_next_window_packet.py` still returning `status: "packet_generated"` with `entries: 0` even while `reddit_execution_status_latest.json` said Reddit was `network_security_blocked`.
- That kept a blocked channel looking active and polluted the follow-through surface instead of pushing the system toward real executable lanes.

## Shared findings/artifacts reused
- `agents/marketing/logs/marketing_loop_runner_latest.json`
- `agents/marketing/logs/reddit_execution_status_latest.json`
- `agents/marketing/logs/marketing_workflow_audit_latest.json`
- `agents/marketing/logs/distribution_lane_latest.json`
- `drafts/marketing_execution_board_latest.md`

## Files changed
- `/home/mistlight/.openclaw/workspace/agents/marketing/reddit_next_window_packet.py`
- `/home/mistlight/.openclaw/workspace/agents/marketing/tests/test_reddit_next_window_packet.py`

## What changed
- Added a runtime gate that reads `reddit_execution_status_latest.json` and skips packet generation when Reddit execution is fail-closed (`network_security_blocked`, `fail_closed`, `blocked`, `login_blocked`).
- Preserved truthful machine output by returning `status: "channel_blocked_skip"` with no packet paths instead of pretending a fresh packet exists.
- Changed zero-entry runs to report `status: "no_actionable_entries"` rather than `packet_generated`.
- Added regression coverage for both blocked-lane skip behavior and zero-entry truthfulness.

## Verification
- `python3 -m unittest agents.marketing.tests.test_reddit_next_window_packet -v`
- `python3 -m py_compile agents/marketing/reddit_next_window_packet.py`
- Runtime check: `python3 agents/marketing/reddit_next_window_packet.py` → `status: "channel_blocked_skip"`

## Expected outcome
- The marketing loop stops manufacturing Reddit packet activity while the Reddit lane is blocked.
- Follow-through surfaces stay cleaner during measurement holds, so the next real executable lane is easier to identify and measure against Codeberg adoption.

# Distribution architecture runtime truth repair
Generated: 2026-05-25T08:48:46+02:00

## Why this was the highest-leverage action now
- The latest distribution decision already pointed at `distribution_architecture_repair`, not new outbound.
- The execution board still says there is no truthful do-now packet in the current review window.
- Active Apollo / curator / publisher review windows are already saturated enough that fresh outbound right now would blur measurement.
- The fastest way to improve real Codeberg-moving odds was to harden selector truthfulness so the loop stops burning future slots on stale or misleading packet decisions.

## Shared findings reused
- `agents/marketing/logs/distribution_lane_latest.json` → the current lane had already escalated to `distribution_architecture_repair` because the empty board persisted after prior guard/follow-through churn.
- `drafts/marketing_execution_board_latest.md` → no truthful do-now handoff packet remains in the current review window.
- `agents/marketing/logs/marketing_workflow_audit_latest.json` / `.md` → same-run runtime repairs are explicitly in scope while Codeberg remains flat.
- `agents/marketing/logs/adoption_metrics_latest.json` → Codeberg adoption is still the primary success gate.

## Repair applied
- Patched `agents/marketing/distribution_lane_selector.py` so verified publisher contact routes treat public contact pages and Telegram as manual-executable channels instead of falsely downgrading them.
- Tightened active-short-window primary-repo-flat packet refresh eligibility so the loop does not force that packet merely because StackOverflow is exhausted while another manual-contact lane is still present.
- Preserved the active-short-window refresh path when live outbound is genuinely saturated and no competing manual-contact queue is waiting, so the next slot can still surface a truthful Codeberg-first publisher packet when that is actually the best move.

## Verification
- `python3 -m unittest agents.marketing.tests.test_distribution_lane_selector_repair_pause agents.marketing.tests.test_distribution_lane_executor_measurement_hold`
- Result: `OK` (75 tests).

## Expected marketing effect
- Future runs are less likely to hide real sendable publisher/manual routes behind false “non-executable” classifications.
- The selector will stop forcing the primary-repo-flat packet during active short windows when that would just displace a truer follow-through / repair state.
- When the packet refresh really is the highest-leverage move, the active-window path still stays available instead of collapsing into more guard churn.

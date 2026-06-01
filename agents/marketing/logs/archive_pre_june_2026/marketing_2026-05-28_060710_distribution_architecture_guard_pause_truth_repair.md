# Marketing runtime repair — empty-board guard-pause truth

- Timestamp: 2026-05-28T06:07:10+02:00
- Action type: `distribution_architecture_guard_pause_truth_repair`
- Outcome: executed

## Why this was the highest-leverage action
The active hold window still had no truthful do-now packet, but the selector was treating historical re-entry repairs as absent and was also letting a suppressed manual-only publisher asset keep the board from reading as truly empty. That was pushing the loop back toward fake measurement-hold churn.

## What changed
- Carried historical `active_loop_prompt_repair` + `post_hold_reentry_contract_repair` forward once a bridge repair exists, instead of forgetting them in later hold windows.
- Taught empty-board detection to ignore suppressed manual-only primary-repo-flat follow-through assets that the execution board itself says are not do-now.
- Stopped the manual-follow-through hold override from downgrading a guarded empty-board architecture state back into `measurement_hold` once the board is explicitly empty and the re-entry repairs are already in force.
- Added regression coverage for both selector-truth bugs.

## Verification
- `python3 -m unittest agents.marketing.tests.test_distribution_lane_selector_repair_pause -q`
- `python3 agents/marketing/distribution_lane_selector.py`
- Verified post-repair selector result: `distribution_architecture_guard_pause`

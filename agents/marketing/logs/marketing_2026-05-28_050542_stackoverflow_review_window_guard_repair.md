# StackOverflow review-window guard repair

- Timestamp: 2026-05-28T05:05:42.415086
- Action: Repaired the StackOverflow demand-capture lane so an already-delivered packet in the current review window does not suppress fresh candidate discovery.

## Why this was the highest-leverage move
- `marketing_execution_board_latest.md` says the current StackOverflow packet was already delivered in this review window and must not be redelivered.
- `marketing_2026-05-28_stackoverflow_manual_delivery.json` identifies the exact delivered question URL.
- `stackoverflow_answer_lane_latest.json` was still capable of stopping early on that same question, which could hide later search hits and waste the scheduled rerun.
- Other live lanes remain inside active measurement windows, so improving fresh demand-capture odds was the strongest truthful same-run repair.

## What changed
- Added a review-window blocker that treats the manually delivered StackOverflow question as exhausted for the current window.
- Removed the early-stop behavior that halted discovery just because a recent draft URL appeared.
- Added regression coverage for both cases.

## Verification
- `python3 -m unittest agents.marketing.tests.test_stackoverflow_answer_lane -v`
- `python3 /home/mistlight/.openclaw/workspace/agents/marketing/stackoverflow_answer_lane.py`

## Outcome
- The rerun now truthfully excludes `https://stackoverflow.com/questions/79896243/autonomous-mode-wrapper-for-claude-code` and continues through the remaining searches.
- No fresh StackOverflow candidate emerged after that exclusion, so the lane remains truthfully empty for this review window instead of pretending the already-delivered packet is still actionable.

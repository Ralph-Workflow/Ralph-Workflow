# Marketing repair log — Reddit lane suspension

- Timestamp: 2026-05-23T17:20:10+02:00
- Action: Suspend stale Reddit autopost lane and force non-Reddit distribution

## Why this was the highest-leverage move now
- `marketing_workflow_audit_latest.json` marks `reddit_style_repetition` as a live failing tactic and keeps the repair state at `needs_execution`.
- `reddit_post_analysis.json` still shows a repeated opening and a 5/6 concentration in `r/ClaudeCode`.
- `adoption_metrics_latest.json` shows Codeberg flat, so more stale Reddit output would be negative leverage rather than growth.

## What changed
- Patched `agents/marketing/reddit_autopost.py` so it now reads the latest audit + Reddit retrospective and exits with `repair_blocked` before posting when the Reddit lane is under active repetition repair.
- Added regression tests in `agents/marketing/tests/test_reddit_autopost.py` for the repair-block logic.

## Verification
- `python3 -m py_compile agents/marketing/reddit_autopost.py agents/marketing/tests/test_reddit_autopost.py`
- `python3 -m unittest agents.marketing.tests.test_reddit_autopost -v`
- `python3 agents/marketing/reddit_autopost.py` returned `repair_blocked` with the expected audit/retro reasons.

## Expected outcome
The loop should stop wasting cycles on repetitive Reddit comments and shift future distribution effort toward non-Reddit channels that can send cleaner Codeberg-primary traffic.

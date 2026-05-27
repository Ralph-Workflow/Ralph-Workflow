# Directory review-window follow-through repair

Generated: 2026-05-27T06:41:06+02:00

## Why this ran
- Codeberg adoption is still flat in the latest window (`adoption_metrics_latest.json`).
- Reddit is still IP-blocked from this environment (`reddit_execution_status_latest.json`), so directory/backlink follow-through remains one of the few truthful non-Reddit lanes.
- `backlink_status_latest.json` shows three live directory listings and multiple 2026-05-23 / 2026-05-24 submissions still pending review.
- The selector still only treated directory confirmation as due after a recent submission burst, which undercounted matured review-window follow-through when live proof already existed.

## Repair shipped
- Added review-window-aware due detection to `distribution_lane_selector.py`.
- Matured pending directory rows now become eligible follow-through candidates when their status notes contain explicit review windows (for example `2-3 day review`).
- Preserved fail-closed behavior for vague pending notes with no explicit window instead of inventing urgency.
- Tightened directory-confirmation reasoning so a current secondary-surface repair packet stays the chosen truthful follow-through surface when it already exists.
- Added targeted tests in `agents/marketing/tests/test_directory_review_window_due.py`.

## Shared findings reused
- `agents/marketing/logs/adoption_metrics_latest.json`
- `agents/marketing/logs/backlink_status_latest.json`
- `agents/marketing/logs/reddit_execution_status_latest.json`
- `agents/marketing/logs/marketing_workflow_audit_latest.json`
- `drafts/marketing_execution_board_latest.md`

## Verification
- `python3 -m unittest agents.marketing.tests.test_directory_review_window_due agents.marketing.tests.test_distribution_lane_selector_repair_pause agents.marketing.tests.test_marketing_system`
- Result: passed (259 tests)

## Expected effect
- When the next truthful directory follow-through window matures, the selector can surface it without waiting for another synthetic submission burst.
- If a live secondary-surface repair packet is already current, the system now keeps that packet as the explicit do-now truth instead of drifting back to a generic directory-refresh reason.

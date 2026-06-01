# Marketing execution — directory confirmation Google 429 guard repair

- Timestamp: 2026-05-25 01:12 Europe/Berlin
- Action: **Refresh live directory proof and stop backlink confirmation from hammering Google after the first 429**
- Channel: **marketing runtime**
- Status: **executed**

## Why this was the highest-leverage move now
- The execution board still had no truthful do-now outbound packet inside the active short review window.
- The current chosen lane was already `directory_confirmation`, so the right move was to strengthen that shared proof artifact instead of inventing another packet.
- The fresh backlink refresh showed a real runtime failure mode: Google search checks were returning **HTTP 429** for the first query, but the tracker kept sending the remaining 17 queries anyway.
- That behavior wasted requests and made the lane noisier without improving measurement truth.

## Shared findings reused
- `agents/marketing/logs/adoption_metrics_latest.json`
- `agents/marketing/logs/distribution_lane_latest.json`
- `agents/marketing/logs/marketing_workflow_audit_latest.json`
- `drafts/marketing_execution_board_latest.md`
- `agents/marketing/logs/backlink_status_latest.json`
- `agents/marketing/logs/reddit_execution_status_latest.json`

## What changed
- Patched `agents/marketing/backlink_status.py`
  - detects a Google 429 / rate-limit response
  - skips the rest of the Google query burst after the first 429
  - records skipped follow-on queries explicitly instead of pretending they were independently checked
  - exposes `queries_skipped_after_rate_limit` and `google_rate_limit_encountered` in the shared summary
- Added regression coverage in `agents/marketing/tests/test_backlink_status.py`
- Refreshed `agents/marketing/logs/backlink_status_latest.json`

## Result right now
- Live listings detected: **2**
- Codeberg-first live listings: **1**
- GitHub-only live listings: **1**
- Google query checks unavailable this run: **18 / 18**
- Follow-on Google queries skipped after first 429: **17**

### Live listing routing truth
- **ToolWise** → routes to **Codeberg first**
- **SaaSHub** → currently a **GitHub-only** mirror listing

## Verification
- `python3 -m unittest agents.marketing.tests.test_backlink_status -v`
- `python3 -m py_compile agents/marketing/backlink_status.py`
- `python3 agents/marketing/backlink_status.py`

## Expected effect
Future directory-confirmation refreshes will fail closed after the first Google 429 instead of burning the whole search burst. That keeps the shared proof artifact more truthful and reduces fake measurement churn during hold windows.

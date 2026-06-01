# Marketing execution — directory confirmation repo-routing repair

- Timestamp: 2026-05-25 01:07 Europe/Berlin
- Action: **Refresh live directory proof and patch the tracker to distinguish Codeberg-first vs GitHub-only listings**
- Channel: **marketing runtime**
- Status: **executed**

## Why this was the highest-leverage move now
- The execution board had **no truthful do-now handoff packet** inside the active short review window.
- Same-family directory and curator bursts are paused, so another outbound packet here would have been fake progress.
- The chosen lane was already `directory_confirmation`, but the shared proof artifact still treated all live listings as roughly equivalent.
- Since Codeberg is the primary adoption target, the tracker needed to say which live listings actually route to Codeberg first.

## Shared findings reused
- `agents/marketing/logs/adoption_metrics_latest.json`
- `agents/marketing/logs/distribution_lane_latest.json`
- `agents/marketing/logs/marketing_workflow_audit_latest.json`
- `drafts/marketing_execution_board_latest.md`
- `agents/marketing/logs/market_intelligence_latest.json`
- `agents/marketing/logs/reddit_post_analysis_latest.json`

## What changed
- Patched `agents/marketing/backlink_status.py`
  - extracts listing outbound links
  - classifies live listings as `codeberg_primary`, `github_only`, `both`, or `unknown`
  - rolls that routing truth into the shared summary counters
- Added regression coverage in `agents/marketing/tests/test_backlink_status.py`
- Patched `agents/marketing/distribution_lane_executor.py` so the directory-confirmation artifact now shows routing truth, not just “live vs pending”
- Refreshed `agents/marketing/logs/backlink_status_latest.json`
- Regenerated `drafts/directory_confirmation_execution_latest.md`

## Result right now
- Live listings detected: **2**
- Codeberg-first live listings: **1**
- GitHub-only live listings: **1**
- Search queries indexed: **1 / 18**

### Live listing routing truth
- **ToolWise** → routes to **Codeberg first**
- **SaaSHub** → currently a **GitHub-only** mirror listing

## Verification
- `python3 -m unittest agents.marketing.tests.test_backlink_status -v`
- `python3 -m py_compile agents/marketing/backlink_status.py agents/marketing/distribution_lane_executor.py`
- `python3 agents/marketing/backlink_status.py`
- regenerated `drafts/2026-05-24_directory_confirmation_execution.md`

## Expected effect
The next active curator/comparison/manual-contact lane can cite truthful third-party proof and weight **ToolWise** higher because it supports the Codeberg-primary CTA, instead of flattening it together with mirror-first listings.

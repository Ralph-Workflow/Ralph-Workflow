# Marketing execution — directory secondary-surface truth repair

- Timestamp: 2026-05-25 20:09 Europe/Berlin
- Action: **Detect already-live third-party secondary surfaces and flag GitHub-only routing on them**
- Channel: **marketing runtime**
- Status: **executed**

## Why this was the highest-leverage move now
- The execution board was still **truthfully empty** inside the active short review window.
- Another same-family publisher/contact burst would have blurred measurement instead of creating a cleaner Codeberg adoption read.
- The shared backlink artifact was undercounting real third-party surfaces, which meant the loop could not see a live SaaSHub page still sending repo intent to GitHub-only.
- That made directory follow-through look more finished than it really was.

## Shared findings reused
- `agents/marketing/logs/adoption_metrics_latest.json`
- `agents/marketing/logs/distribution_lane_latest.json`
- `agents/marketing/logs/marketing_workflow_audit_latest.json`
- `drafts/marketing_execution_board_latest.md`
- `agents/marketing/logs/backlink_status_latest.json`
- `agents/marketing/logs/market_intelligence_latest.json`

## What changed
- Patched `agents/marketing/backlink_status.py`
  - adds `secondary_check_urls` support for live third-party surfaces beyond the main listing URL
  - records `secondary_check_results` and `secondary_surface_targets`
  - rolls secondary-surface routing truth into summary counters
- Added regression coverage in `agents/marketing/tests/test_backlink_status.py`
- Refreshed `agents/marketing/logs/backlink_status_latest.json`

## Result right now
- Direct live listings detected: **2**
- Direct live listings pointing to Codeberg: **2**
- Live secondary surfaces detected: **2**
- Secondary surfaces pointing to GitHub-only: **1**
- Secondary surfaces with unknown repo target: **1**

### New surfaced truth
- **SaaSHub product page** → includes **Codeberg + GitHub + site**
- **SaaSHub alternatives page** (`/ralph-workflow-alternatives`) → includes **GitHub + site**, but **not Codeberg**
- **SaaSHub workflow category page** (`/best-workflow-automation-software`) → mentions Ralph Workflow, but exposes **no direct repo link** in the captured surface

## Verification
- `python3 -m unittest agents.marketing.tests.test_backlink_status -v`
- `python3 -m py_compile agents/marketing/backlink_status.py`
- `python3 agents/marketing/backlink_status.py`

## Expected effect
The next truthful directory-confirmation follow-through can target an actual Codeberg-routing gap on a page that is already live, instead of wasting another slot pretending SaaSHub is fully resolved just because the main listing page is fixed.

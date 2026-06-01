# Marketing execution — backlink tracking expansion

- Timestamp: 2026-05-24 05:36 Europe/Berlin
- Action: **Expand backlink tracking to cover today's fresh Claude ecosystem submissions and refresh live status**
- Channel: **marketing runtime**
- Status: **executed**

## Why this was the highest-leverage move now
- The active lane is `measurement_hold`, so another new burst would have been churn.
- Two of today's best high-intent submissions — **Claude Code Alternatives** and **Claude Stack** — were real external actions, but the tracker could not measure them yet.
- Adding them to the shared tracker turns today's shipping into visible follow-through and makes future review windows more honest.

## Shared findings reused
- `agents/marketing/logs/adoption_metrics_latest.json`
- `agents/marketing/logs/distribution_lane_latest.json`
- `agents/marketing/logs/marketing_workflow_audit_latest.json`
- `agents/marketing/logs/market_intelligence_latest.json`
- `agents/marketing/outreach-log.md`

## What changed
- Added **Claude Code Alternatives** tracking to `agents/marketing/backlink_status.py`
  - submit URL: `https://claude-code-alternatives.com/tool/create/`
  - candidate listing paths: `cli-agents/ralph-workflow/`, `ai-ides/ralph-workflow/`
- Added **Claude Stack** tracking to `agents/marketing/backlink_status.py`
  - submit URL: `https://www.claudestack.dev/submit`
  - candidate listing paths: `entries/ralph-workflow`, `category/workflows`
- Added matching search queries so future index checks include both new domains.

## Verification
- `python3 agents/marketing/backlink_status.py`
- `python3 -m py_compile agents/marketing/backlink_status.py`

## Result right now
- Live listings still detected: **2** (`SaaSHub`, `ToolWise`)
- Claude Code Alternatives: **not live yet / still pending**
- Claude Stack: **not live yet / still pending**
- Refreshed artifact: `agents/marketing/logs/backlink_status_latest.json`

## Expected effect
The next measurement and review passes can now detect whether today's Claude-adjacent submissions actually become public listings, instead of letting those lanes sit as unmeasured pending work.

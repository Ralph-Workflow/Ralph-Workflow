# SaaSHub live listing correction — 2026-05-24

## Why this was the highest-leverage move
- Codeberg adoption is flat, and the latest audit says to pause more same-family directory/curator bursts.
- SaaSHub is already a live third-party listing, so fixing its routing is stronger than creating another pending submission.
- ToolWise already points to Codeberg correctly, but SaaSHub still routes users to the website first and exposes a wrong GitHub repo button.

## What I verified
- Refreshed `backlink_status_latest.json` via `python3 agents/marketing/backlink_status.py`
- Current live listings confirmed: **2** (`SaaSHub`, `ToolWise`)
- ToolWise HTML contains the Codeberg primary repo
- SaaSHub HTML does **not** contain the Codeberg primary repo and includes a GitHub button to `github.com/mistlight/Ralph-Workflow`

## Action executed
1. Posted a correction comment on the live SaaSHub page with the Codeberg-primary repo and correct GitHub mirror.
2. Confirmed that comment by email.
3. Sent a direct correction email to `stan@saashub.com` asking for the live listing to point to:
   - Codeberg primary: `https://codeberg.org/RalphWorkflow/Ralph-Workflow`
   - GitHub mirror: `https://github.com/Ralph-Workflow/Ralph-Workflow`

## Files / logs
- Email body: `/home/mistlight/.openclaw/workspace/drafts/2026-05-24_saashub_listing_correction_email.txt`
- SMTP send log: `/home/mistlight/.openclaw/workspace/agents/marketing/logs/marketing_2026-05-23_220815_saashub_listing_correction.json`
- Structured action log: `/home/mistlight/.openclaw/workspace/agents/marketing/logs/marketing_2026-05-24_saashub_live_listing_correction.json`

## Expected outcome
A currently live third-party listing starts sending qualified evaluators to **Codeberg first** instead of a misrouted GitHub target or the generic website CTA.

## Review point
Check by **2026-05-31** whether the SaaSHub listing has been corrected or replied to. If not, do not keep poking the same target; switch the next fresh action to another executable high-intent lane.

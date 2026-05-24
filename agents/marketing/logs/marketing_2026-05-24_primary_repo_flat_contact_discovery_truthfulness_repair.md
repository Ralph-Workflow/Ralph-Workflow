# Primary-repo-flat contact discovery truthfulness repair
Generated: 2026-05-24T23:33:05+02:00

## Why this ran
- The latest `primary_repo_flat_contact_discovery_latest` artifact still listed AXME Code, WyeWorks, and Bollwerk / Werkstatt as contact-ready even though live publisher outreach already shipped for them in the current 7-day review window.
- The execution board was already warning against reusing those targets, so the discovery artifact had become the stale truth source.
- I also dry-ran the remaining `ctxt.dev / Signum` Telegram path and confirmed it is still blocked from this Matrix-bound runtime (`Cross-context messaging denied`), so a truthfulness/process repair was the strongest legitimate move right now.

## Shared findings reused
- `agents/marketing/MARKETING_SELF_IMPROVEMENT.md`
- `agents/marketing/MARKETING_WORKFLOW_PRINCIPLES.md`
- `agents/marketing/FOUR_MARKETING_QUESTIONS.md`
- `agents/marketing/ADOPTION_FUNNEL_NEXT.md`
- `agents/marketing/logs/market_intelligence_latest.json`
- `agents/marketing/logs/marketing_workflow_audit_latest.{json,md}`
- `agents/marketing/logs/distribution_lane_latest.{json,md}`
- `drafts/marketing_execution_board_latest.md`
- `agents/marketing/outreach-log.md`
- `agents/marketing/logs/primary_repo_flat_contact_discovery_latest.json`

## What changed
- Patched `agents/marketing/primary_repo_flat_contact_discovery.py` to filter out targets with recent live `publisher_email_outreach` sends inside the active 7-day review window.
- Added regression coverage in `agents/marketing/tests/test_primary_repo_flat_contact_discovery.py`.
- Regenerated the latest discovery artifacts.

## Current truth after repair
- Remaining untouched target in this repair set: `ctxt.dev / Signum`
- Omitted from the latest discovery artifact because they were already contacted in-window: `AXME Code`, `Bollwerk / Werkstatt`, `WyeWorks`
- Verified remaining channel reality: `ctxt.dev / Signum` has a confirmed Telegram contact path, but this runtime still cannot send Telegram from the current Matrix-bound context.

## Verification
- `python3 -m unittest agents.marketing.tests.test_primary_repo_flat_contact_discovery` ✅
- `python3 agents/marketing/primary_repo_flat_contact_discovery.py` ✅

## Expected outcome
Future primary-repo-flat follow-through stops treating already-contacted publishers as fresh contact-ready targets and keeps the untouched `ctxt.dev / Signum` path isolated as the only remaining target in this repair set.

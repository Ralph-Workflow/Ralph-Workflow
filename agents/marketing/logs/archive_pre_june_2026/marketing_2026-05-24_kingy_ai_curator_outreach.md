# Kingy AI curator outreach — 2026-05-24

- Target: `Kingy AI — Cursor / Cursor SDK vs. Claude Code vs. Codex`
- Recipient: `info@kingy.ai`
- Subject: `Ralph Workflow for Kingy AI`
- When sent: `2026-05-24 12:08 CEST`
- Primary repo: https://codeberg.org/RalphWorkflow/Ralph-Workflow

## Why this was the move
- Codeberg adoption is still flat in the current window.
- Kingy AI was still marked `prepared` in the live curator queue, so this was a real unsent target rather than another packet refresh.
- GitHub auth is unavailable here for direct PR submission, but Kingy AI exposed a legitimate public email route, so SMTP outreach was the strongest executable path from this runtime.

## Shared artifacts reused
- `drafts/curator_outreach/2026-05-24/02_kingy-ai-cursor-cursor-sdk-vs-claude-code-vs-codex.md`
- `agents/marketing/logs/curator_outreach_queue_latest.json`
- `agents/marketing/logs/adoption_metrics_latest.md`
- `agents/marketing/logs/marketing_workflow_audit_latest.json`
- `agents/marketing/logs/market_intelligence_latest.json`

## Verification
- SMTP acceptance log: `agents/marketing/logs/marketing_2026-05-24_100830_kingy_ai_curator_email.json`
- SMTP server: `smtp.ionos.com:587` over STARTTLS
- Helper compile check: `python3 -m py_compile agents/marketing/send_curator_email.py`

## Same-run truthfulness repair
- Updated `curator_outreach_queue_latest.json` so Kingy AI is now `sent_via_email_fallback`.
- Refreshed `drafts/curator_handoff_packet_latest.md` and `drafts/marketing_execution_board_latest.md` so they stop advertising Kingy AI as still waiting.

## Review window
- Reply / mention review: `2026-06-07 12:08 CEST`
- Backlink / listing review: `2026-06-21 12:08 CEST`

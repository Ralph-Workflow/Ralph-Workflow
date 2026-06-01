# Ralph Workflow Distribution Reset — Fresh Target Discovery
Generated: 2026-05-24T01:31:00+02:00

## Why this shipped
The active loop correctly chose `distribution_reset`, but a packet alone was not enough. The queue was saturated, so this run turned the reset into fresh untouched target discovery.

## Shared findings reused
- `market_intelligence_latest.json`
- `comparison_backlink_queue_latest.json`
- `marketing_workflow_audit_latest.json`
- `outreach-log.md`

## New untouched targets added now

1. **Claude Code Alternatives** — https://claude-code-alternatives.com/tool/create/
   - Why it matters: high-intent comparison surface already ranking Claude Code alternatives and adjacent CLI agents.
   - Proof found: live submit form at `/tool/create/`.
   - Next path: submit Ralph Workflow as a Codeberg-first alternative.

2. **AI IDE** — https://aiide.dev/
   - Why it matters: coding-tool directory audience aligned with autonomous coding and AI IDE evaluation.
   - Proof found: directory positioning on homepage plus curator contact `support@aiide.dev` in page HTML.
   - Next path: curator email / listing suggestion with Codeberg-first CTA.

3. **AI Coding Stack** — https://aicodingstack.io/docs/getting-started
   - Why it matters: community-maintained AI coding metadata directory with CLI/tool coverage.
   - Proof found: docs explicitly instruct contributors to add tools via manifests; repo linked at `https://github.com/aicodingstack/aicodingstack.io`.
   - Next path: prepare PR/manual handoff for manifest addition when GitHub-auth path is usable.

4. **AI Resources** — https://airesources.dev/category/agents/
   - Why it matters: third-party AI resources directory with an `/Agents` category adjacent to Ralph Workflow’s use case.
   - Proof found: project repo linked at `https://github.com/catalinpit/airesources`.
   - Next path: prepare PR/manual handoff for new agent entry when GitHub-auth path is usable.

## Constraint handling
- Did **not** add another same-family directory submission burst.
- Did **not** add another curator-contact burst into an active measurement window.
- Kept **Codeberg primary** and **GitHub mirror** in every next-path recommendation.

## Outcome
This run added **4 fresh third-party backlink/citation targets** so the next distribution execution can advance a genuinely new lane instead of logging saturated follow-through as progress.

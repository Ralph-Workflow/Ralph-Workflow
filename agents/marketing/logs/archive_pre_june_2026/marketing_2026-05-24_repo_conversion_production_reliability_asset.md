# Marketing action — repo conversion production-reliability asset

- **When:** 2026-05-24 08:13 Europe/Berlin
- **Action:** Shipped a new Codeberg-first repo conversion guide for the strongest preserved high-intent pain frame: production reliability for autonomous AI workflows
- **Status:** executed

## Why this was the highest-leverage move now
- Codeberg adoption is still flat in the active measurement window.
- Directory, curator, and Apollo lanes already have fresh actions inside live review windows.
- StackOverflow is in a real cooldown until 2026-05-24 11:24 Europe/Berlin, and GitHub auth is unavailable from this runtime.
- The strongest preserved demand signal is still the production-reliability workflow question, so the best same-run move was to turn that pain frame into a repo-facing guide that helps evaluators on the Codeberg path instead of generating another overlapping packet.

## Shared findings reused
- `agents/marketing/MARKETING_SELF_IMPROVEMENT.md`
- `agents/marketing/MARKETING_WORKFLOW_PRINCIPLES.md`
- `agents/marketing/FOUR_MARKETING_QUESTIONS.md`
- `agents/marketing/ADOPTION_FUNNEL_NEXT.md`
- `agents/marketing/logs/market_intelligence_latest.json`
- `agents/marketing/logs/marketing_workflow_audit_latest.json`
- `agents/marketing/logs/marketing_workflow_audit_latest.md`
- `agents/marketing/logs/adoption_metrics_latest.md`
- `agents/marketing/logs/stackoverflow_answer_lane_latest.json`
- `drafts/stackoverflow_answer_handoff_packet_latest.md`
- `Ralph-Site/content/blog/how-to-structure-autonomous-ai-agent-workflows-for-production-reliability.md`

## Files changed
- `content/guides/autonomous_ai_workflows_production_reliability.md`
- `docs/README.md`
- `content/guides/unattended_ai_coding_workflow.md`
- `content/guides/review_ai_coding_output_before_merge.md`
- `content/guides/claude_code_codex_workflow.md`

## What changed
- Added a new repo-facing guide focused on production reliability for autonomous AI workflows.
- Routed that guide into the docs switchboard.
- Threaded it into the three most relevant conversion/proof guides so evaluators can move from unattended-workflow pain to a concrete Codeberg-first explanation without leaving the repo path.

## Docs review note
Reviewed in order:
1. `README.md`
2. `START_HERE.md`
3. `docs/README.md`

### What changed
Added one new guide and linked it from the docs switchboard and adjacent proof/conversion pages.

### Why it belongs on these surfaces
This is a high-intent evaluator asset, not a homepage positioning change. It belongs in docs and adjacent proof guides because it answers a concrete reliability objection close to the Codeberg evaluation path.

### What was pruned / shortened
No top-level README links were added. The top surface stayed tight to avoid turning README into a link farm.

### Duplication reduction
The production-reliability pain frame now has one repo-facing destination instead of being split between a site blog post, StackOverflow packet, and ad hoc future replies.

### Why the top-level experience is better now
Evaluators who arrive with a reliability objection can now stay inside the repo/docs journey, get a sharper answer, and still land on Codeberg first.

## Verification
- Read back `content/guides/autonomous_ai_workflows_production_reliability.md`
- Read back `docs/README.md`
- Confirmed all new relative links resolve locally
- `python3 -m py_compile agents/marketing/stackoverflow_answer_lane.py`

## Expected outcome
A clearer Codeberg-first conversion path for high-intent evaluators who care about whether autonomous AI coding can hold up in production.

## Measurement contract
- **Review by:** 2026-05-31 08:13 Europe/Berlin
- **Primary success gate:** any attributable reuse of this guide in future high-intent answers or any Codeberg-first adoption movement during the next measurement window
- **Replacement condition:** if this guide does not get reused and Codeberg stays flat after the window matures, replace this lane with a different executable demand-capture or proof asset instead of writing another adjacent reliability explainer

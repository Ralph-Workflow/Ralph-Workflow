# Repo conversion comparison-fit asset — 2026-05-24

## Action shipped
Created a new Codeberg-first evaluator page:
- `docs/when-to-use-ralph-workflow.md`

Then linked it into the repo conversion path from:
- `README.md`
- `START_HERE.md`
- `docs/README.md`

## Why this was the highest-leverage move right now
Fresh external lanes were already inside overlap or measurement windows, and the shared audit/adoption artifacts still pointed to a conversion bottleneck.

So instead of generating another reset packet or overlapping outreach burst, this run shipped a repo-facing asset for evaluators who are comparing Ralph Workflow against chat/editor-native tools and generic orchestration setups.

## Shared findings reused
- `agents/marketing/logs/market_intelligence_latest.json`
- `agents/marketing/logs/market_intelligence_consumption_latest.json`
- `agents/marketing/logs/marketing_workflow_audit_latest.json`
- `agents/marketing/ADOPTION_FUNNEL_NEXT.md`

## Docs review note
Reviewed in order:
1. `README.md`
2. `START_HERE.md`
3. `docs/README.md`

### What changed
Added one new chooser page and threaded it into the top-level repo journey.

### Why it belongs on these surfaces
This is a repo conversion asset for people already evaluating the product. It clarifies fit and differentiation close to the Codeberg-first CTA instead of leaving that judgment scattered across future channel replies.

### What was pruned
- README route no longer links directly to the example first-task page there.
- START_HERE no longer uses the short-slot link for unattended-workflow framing.

### Duplication reduction
The "is this for me?" comparison now lives in one page instead of being re-explained ad hoc in multiple packets.

### Why the top-level experience is better now
The evaluator path is cleaner:
- start the first run
- pick the task
- decide whether you need a workflow at all
- inspect the workflow shape

## Verification
- Read back `docs/when-to-use-ralph-workflow.md`
- Inspected diff for the four changed docs files

## Expected outcome
A clearer Codeberg-first evaluator path for developers comparing Ralph Workflow with Aider, Claude Code, Cursor, Continue, Copilot, or generic agent orchestration tools.

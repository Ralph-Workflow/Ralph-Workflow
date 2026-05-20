# Ralph Workflow Docs Quality Rubric

This rubric is the primary standard for agentic docs review.

## Canonical source of truth
- `agents/marketing/RALPH_WORKFLOW_POSITIONING.md`

## Core judgment
The docs system should explain Ralph Workflow as:
- **the operating system for autonomous coding**
- an **AI agent orchestrator**
- built on a **simple Ralph-loop core**
- **powerful in composition** because the simple core can be composed into larger workflows
- shipping with a **strong default workflow for writing software**
- something users can adopt **as-is first** and extend later

## Required quality criteria
Judge all of these at once:
1. marketing positioning alignment
2. factual / product accuracy
3. no internal developer-only concepts sold as end-user value
4. copyedited wording that reads clearly and naturally
5. marketable framing without hype sludge
6. good information hierarchy
7. low link anxiety on top-level surfaces
8. coherent README -> START_HERE -> docs map journey
9. clear audience fit / non-fit
10. promoted next-click pages reinforce the same story instead of fighting it

## Hard failures
Fail if any of these are true on top-level/promoted public docs:
- leads with artifacts, review bundles, handoff structure, workflow plumbing, transcript-vs-diff, merge-decision, or similar internal process residue
- sounds like the product is mainly about reviewable output rather than orchestration / workflow power
- README, START_HERE, and docs map do not work as one route
- copy is technically compliant but still confusing, clunky, or hard to trust
- promoted docs route evaluators into stale framing
- the user would reasonably need to remind the docs agent about these same criteria again

## Evidence expectations
A passing review must cite concrete files/pages and explain:
- what is working
- what is still broken
- which pages most need remediation
- whether the loop is healthy enough that the user should not need to repeat the same instruction again

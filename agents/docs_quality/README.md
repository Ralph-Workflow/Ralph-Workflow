# Ralph Docs Quality Loop (Runtime-Owned)

This is the runtime-owned enforcement loop for Ralph Workflow docs quality.

Its job is not to rubber-stamp that files exist.
Its job is to stop top-level docs from drifting away from the canonical Ralph Workflow positioning.

## Canonical source of truth

The watchdog must treat this file as the canonical positioning reference:

- `/home/mistlight/.openclaw/workspace/agents/marketing/RALPH_WORKFLOW_POSITIONING.md`

If README, START_HERE, docs map, or promoted first-click pages contradict that document, the docs loop must fail.

## Scope

Target repos:
- `/home/mistlight/RalphWithReviewer`
- `/home/mistlight/.openclaw/workspace/repos/Ralph-Workflow/github-mirror`

Primary surfaces watched:
- primary repo root `README.md`
- primary repo `ralph-workflow/README.md`
- mirror repo root `README.md`
- mirror repo `START_HERE.md`
- mirror repo `docs/README.md`
- promoted next-click docs targets

## End-to-end rule

A successful docs-quality run must:
1. inspect the current docs state
2. run the deterministic checker: `agents/docs_quality/ralph_docs_check.py`
3. run the editorial audit: `agents/docs_quality/ralph_docs_editorial_audit.py`
4. run the agentic review: `agents/docs_quality/ralph_docs_agentic_review.py`
5. treat agentic review as the primary quality judge and deterministic checks as secondary tripwires
   - exact standing rule: agentic review is the primary quality judge
6. apply only conservative deterministic repairs when they are unquestionably safe
7. rerun checker + editorial audit + agentic review after any repair attempt
8. run the independent verifier: `agents/docs_quality/ralph_docs_verify.py`
9. record verifier signoff in `agents/docs_quality/ralph_verifier_latest.md`
10. fail hard if the docs still drift from positioning
11. never self-certify success after shallow fixes or typo-only edits

## What the loop must protect

The docs loop must protect these Ralph Workflow truths:

- Ralph Workflow is **the operating system for autonomous coding**
- the simple Ralph loop stays understandable at the core
- that simple core composes into more complex workflows
- that simplicity makes the system easier to configure, reason about, and extend
- Ralph Workflow is an **AI agent orchestrator**
- the shipped default workflow is already strong for writing software
- users can start with the default and build on top of it later
- the product should feel **simple at the center, powerful in composition**

## What top-level docs must not lead with

Top-level/public docs must not lead with:

- artifacts
- review bundles
- reviewable-result framing as the main differentiator
- transcript-vs-diff framing
- merge-decision framing
- internal handoff structure
- internal workflow plumbing
- logs as the primary trust story

Those may appear only as secondary proof details when they materially help evaluation.

## Quality criteria the loop must enforce

All of these criteria matter at the same time:

- canonical marketing positioning alignment
- factual/product accuracy
- no accidental exposure of internal/developer-only concepts as end-user value
- copy that is understandable, marketable, and easy to scan
- information architecture that helps evaluators find the right next page quickly
- README -> START_HERE -> docs map coherence as one journey
- wording that sounds like a product explanation, not internal process residue
- top-level routes that stay simple instead of sprawling into link farms

## Failure conditions

The docs loop must fail if any of these happen on top-level surfaces:

- README.md drifts from the canonical positioning doc
- `README.md` drifts from the canonical positioning doc
- simple-core / composable-workflow / default-workflow truths disappear from the first screen
- artifact or merge/debate framing dominates the first screen
- public docs explain internals before user value
- the docs sound like process receipts instead of product positioning
- the verifier signs off while obvious contradictions remain
- the loop catches typos but misses top-level positioning drift
- the user would reasonably need to repeat the same docs-agent instruction again

## Repair policy

The runner must be conservative.

Allowed deterministic repairs:
- watchdog-owned status files
- safe routing/metadata normalization with no product-copy judgment involved
- other narrowly-scoped changes that are obviously mechanical

Disallowed deterministic repairs:
- rewriting major product copy from canned templates without a fresh editorial audit
- silently reintroducing stale positioning
- replacing a comprehensive docs review with a typo-level patch

If the problem is editorial or positional, the loop should fail loudly instead of pretending it was repaired.

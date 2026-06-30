<!--
  Review note (docs rubric §"Required review note for meaningful docs changes"):
  - What changed: rewrote the opening paragraph so the page leads with the
    canonical autopilot positioning language instead of the older "AI agent
    orchestration system built around a simple Ralph-loop core" lead category.
  - Why it belongs here: this is the proof-framing page that lives under the
    "Proof / framing" section of the manual; its lead must agree with the
    rest of the manual and the README.
  - What was pruned: nothing material; the "supporting evidence, in the
    right order" review standard is preserved.
  - How the route is clearer: the lead now matches the canonical autopilot
    framing used by the README and the manual home.
-->

# What Good Ralph Workflow Output Looks Like

Ralph Workflow is **the autopilot for coding agents** — a free and open-source operating system for autonomous coding, an AI agent orchestrator built around a simple Ralph-loop core that becomes powerful through composition.
**Hand it a well-specified coding task, let the agents plan, build, verify, and fix, and come back to reviewable, tested work.**
The default workflow is strong enough to adopt as-is, before you customize anything.
This page is supporting proof for that composable workflow and its strong default workflow, not the main product pitch.

Use this page after you already understand the workflow and want a review standard for the morning-after handoff.
Start with the product story and operator route first, then use this page to judge whether a run produced something worth trusting.

## What to evaluate first

The first question is not whether the transcript sounds smart.
The first question is what the software does now.

Good output usually means:

- the task scope is recognizable from the result
- the repo is in a better state than before
- meaningful checks ran and their outcome is explicit
- the change can be reviewed against a real written spec
- the human can decide what to do next without reconstructing the whole run

## Supporting evidence, in the right order

Use evidence in this order:

1. **working behavior** — what changed in the software
2. **real checks** — tests, integration checks, or other meaningful validation
3. **written scope** — whether the result matches the promised task
4. **supporting artifacts** — logs, diffs, or deeper traces if you need them

Logs and transcripts can be useful.
They just should not be the main promise.

## What weak output looks like

Be skeptical when a run gives you:

- lots of narration but unclear product change
- a diff with no convincing checks
- a confident summary for a vague task
- artifacts that sound organized but do not make the result easier to judge

Ralph Workflow depends on real engineering guardrails.
If the repo does not have them, the honest outcome may be limited proof rather than full trust.

## Where to go next

- for the shortest first-run path: [START_HERE.md](../START_HERE.md)
- for choosing a task with a real finish line: [first-task-guide.md](./first-task-guide.md)
- for why specs matter to output quality: [spec-driven-ai-agent.md](./spec-driven-ai-agent.md)

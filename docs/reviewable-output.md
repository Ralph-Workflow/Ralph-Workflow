# What Good Ralph Workflow Output Looks Like

Ralph Workflow is a free and open-source AI agent orchestration system built around a simple Ralph-loop core.
This page is supporting proof for that composable workflow system and its strong default workflow, not the main product pitch.


Use this page after you already understand the workflow and want a review standard for the morning-after handoff.
This page is supporting proof for Ralph Workflow's default unattended coding flow, not the main product pitch.
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

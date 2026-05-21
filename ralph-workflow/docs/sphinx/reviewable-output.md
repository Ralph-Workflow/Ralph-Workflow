# What Good Ralph Workflow Output Looks Like

Use this page after you understand the workflow and want a review standard for the morning-after handoff.
This page is supporting proof, not the main product pitch.

Ralph Workflow is the operating system for autonomous coding: a free and open-source composable loop framework and AI orchestrator built around a simple core loop inspired by the original Ralph loop.
That simple core composes into a stronger composable workflow for substantial, well-specified repo work, and the default workflow is already strong enough to start with before you customize anything.

## What a trustworthy result should answer quickly

A strong result should let you answer these questions without diving into raw logs first:

- What changed?
- What checks ran?
- What still looks risky?
- What should I inspect first?

If the output cannot answer those quickly, the handoff is weak even if the branch is large.

## What strong output usually includes

Strong output usually includes:

- a concise task/result summary
- the important files or components that changed
- the checks that ran and whether they passed
- known gaps, caveats, or follow-up risks
- enough context for a human to inspect the software directly

## What to evaluate first

1. inspect the software behavior or diff summary
2. inspect the checks that ran
3. inspect the remaining risks or open questions
4. only then fall back to deeper logs if something looks wrong

That order keeps proof secondary to actual software judgment.

## What to do next

- Need the full operator path? Return to [Getting Started](getting-started.md).
- Need config/operator reference? Open [Reference](reference.md).
- Need first-run task guidance? Open [first-task-guide.md](first-task-guide.md).

# Claude Code "Run Until Done" Still Needs a Reviewable Finish

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple core loop inspired by the original Ralph loop.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


If you are searching for **Claude Code run until done** or the newer **`/goal`-style finish mode**, the useful question is not whether the session can keep going longer.

The useful question is this:

> **When it stops, do you get something you can actually review and decide to merge?**

Ralph Workflow is a **free and open-source** orchestration CLI that runs the coding agents you already use **on your own machine**.

It is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

What makes it different is the finish state: Ralph Workflow hands back a **strong software result** — a real diff, checks that ran, artifacts you can inspect, and clear open questions — instead of a longer session plus another confident done claim.

Why use it now? Because if Claude Code can now push further on its own, the next bottleneck is not raw autonomy. The bottleneck is whether the morning-after handoff is trustworthy enough to act on.

## "Run until done" solves persistence, not the human review

A longer-running Claude Code mode can help when you want the agent to:

- keep iterating without constant nudges
- stay on one task longer before handing control back
- reduce stop-and-start friction in an otherwise promising workflow
- push a bounded task further while you are away from the keyboard

That is real progress.

But it still does not automatically answer the harder engineering questions:

1. **What changed?**
2. **What checks actually ran?**
3. **What still needs human judgment?**
4. **Would you merge this?**

If the run lasts longer but the morning still starts with transcript archaeology, the workflow is not finished just because the session was.

## The real problem is trust in the finish state

Most developers do not actually want maximum autonomy.

They want a result that is cheap to inspect and boring to review.

When "run until done" still feels risky, the missing pieces are usually:

- one bounded task instead of an open-ended session
- acceptance criteria before code starts
- checks that run during the workflow
- a fail-closed handoff when the result is weak
- repo-local artifacts that make re-entry easy the next morning

That is the gap Ralph Workflow is built to close.

## What Ralph Workflow adds on top of Claude Code

Ralph Workflow does **not** replace Claude Code.

It wraps the agent you already use in a repo-native workflow that makes the finish state easier to trust:

- what changed
- what checks ran
- what was fixed before handoff
- what still needs human judgment
- whether you would merge it

That is more useful than merely extending the session.

## What a trustworthy morning-after handoff looks like

If the run went well, the handoff should look roughly like this:

```text
Task: Add empty-project-name validation to the CLI create flow

Changed files:
- cli/create.py
- tests/test_create.py

Checks run:
- unit tests for create flow
- lint / formatting checks if applicable

Open questions:
- should reserved names be rejected too?
- should whitespace be trimmed before validation?
```

That is the standard to hold unattended work to: **proof of completion, not just a stronger done mode**.

If you want to inspect that artifact shape first, open the public [example review bundle](./example-review-bundle.md).

## When Ralph Workflow is the better fit

Ralph Workflow is the stronger path when:

- Claude Code is already useful, but you want a cleaner morning-after review path
- `/goal` or "run until done" reduces interruptions but still leaves a fuzzy human review
- the task is clear enough to hand off overnight
- you care more about the result summary than the live transcript

If you need live exploration on a fragile task, stay interactive. If you want a bounded overnight handoff you can judge honestly in the morning, Ralph Workflow is the better fit.

## Best next step if "run until done" still leaves a fuzzy morning-after handoff

Do not stop at a longer session.

Use **Codeberg** as the main public home:

- **Inspect the primary repo on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Star or watch on Codeberg if the finish state earns trust:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report finish-receipt or first-run friction on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Use GitHub only as the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

Then use [After Your First Ralph Workflow Run](after-your-first-run.md) so a promising trial becomes a visible primary-repo trust signal and a rough one becomes a useful issue.

## Fastest honest first run

1. Keep Claude Code already installed and authenticated on your own machine
2. Pick one real backlog task with clear acceptance criteria
3. Run Ralph Workflow overnight
4. Review the diff, checks, and artifacts in the morning
5. Ask: **does the implementation hold up?**

If you want the shortest path, start with [Getting Started](getting-started.md).

If the blocker is still approval babysitting, read [claude-code-approval-mode.md](./claude-code-approval-mode.md).

If the blocker is the broader overnight automation path, read [run-claude-code-overnight-without-babysitting.md](./run-claude-code-overnight-without-babysitting.md).

If you want the sharper product comparison, read [ralph-workflow-vs-claude-code.md](./ralph-workflow-vs-claude-code.md).

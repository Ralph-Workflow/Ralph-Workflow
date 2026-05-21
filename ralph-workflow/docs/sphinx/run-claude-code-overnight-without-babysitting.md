---
orphan: true
---

# Run Claude Code Overnight Without Babysitting

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple core loop inspired by the original Ralph loop.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


If you are searching for **how to run Claude Code overnight without babysitting**, the real question is not just how to leave the terminal open longer.

The real question is this:

> **Can you come back to code you can actually review and decide to merge?**

Ralph Workflow is **the operating system for autonomous coding**: a **free and open-source composable loop framework and AI orchestrator** that runs the coding agents you already use **on your own machine**.

It is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

What makes it different is the handoff: Ralph Workflow leaves you with a **strong software result** — a real diff, checks that ran, artifacts you can inspect, and a clear morning-after merge question.

Why use it now? Run one real backlog task tonight and judge the result tomorrow instead of hovering over an unattended session and hoping it stayed on track.

## What "without babysitting" should actually mean

For overnight Claude Code work, "without babysitting" should not mean:

- letting a long chat run and praying it stays coherent
- waking up to a transcript instead of a diff
- seeing a done claim without a clean proof path
- spending the morning reconstructing what actually changed

It should mean:

- one bounded task
- one unattended run on your own machine
- checks that actually ran
- a result you can review in normal engineering terms

That is the gap Ralph Workflow is meant to close.

## What Ralph Workflow adds on top of Claude Code

Ralph Workflow does **not** replace Claude Code.

It wraps the agent you already use in a repo-native overnight workflow so the finish state is easier to trust:

- what changed
- what checks ran
- what still needs human judgment
- whether you would merge it

That is more useful than just making the session longer.

## What a trustworthy overnight handoff looks like

If the run went well, the morning-after result should look something like this:

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

That is the real standard: **proof of completion, not just a confident done claim**.

If you want to inspect that shape before you install anything, open the public [Example Review Bundle](example-review-bundle.md).

## Best next step if Claude Code is already your default

Use **Codeberg** as the main public home:

- **Inspect the primary repo on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Star / watch / fork on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report first-run friction on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Use GitHub only as the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

Keeping the trust signals on Codeberg makes the primary repo a clearer evaluation surface.

## Fastest honest first run

1. Keep Claude Code already installed and authenticated on your own machine
2. Pick one real backlog task with clear acceptance criteria
3. Run Ralph Workflow overnight
4. Review the diff, checks, and artifacts in the morning
5. Ask: **does the implementation hold up?**

If you want the shortest path, start with [Getting Started](getting-started.md).
If the blocker is still Claude Code approval babysitting, read [Claude Code Approval Mode Is Not an Unattended Workflow](claude-code-approval-mode.md).
If you want the broader automation framing first, read [Claude Code Automation for Real Repo Work](claude-code-automation.md).
If you want the sharper product comparison, read [Ralph Workflow vs Claude Code](ralph-workflow-vs-claude-code.md).

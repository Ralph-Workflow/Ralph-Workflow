<!--
  Review note (docs rubric §"Required review note for meaningful docs changes"):
  - What changed: rewrote the opening paragraph so the page leads with the
    canonical autopilot positioning language instead of the older "AI agent
    orchestrator built around a simple ... Ralph-loop core" lead category.
  - Why it belongs here: this page is part of the maintained Sphinx manual;
    it must agree with the README and the manual home so the product story
    is coherent across surfaces (rubric hard failure: surfaces fight each
    other).
  - What was pruned: nothing material; the page's page-specific argument is
    preserved.
  - How the route is clearer: the lead now matches the canonical autopilot
    framing used by the README and the manual home.
-->

# Run Claude Code Overnight Without Babysitting

Ralph Workflow is **the autopilot for coding agents** — a free and open-source operating system for autonomous coding, an AI agent orchestrator built around a simple Ralph-loop core that becomes powerful through composition.
**Hand it a well-specified coding task, let the agents plan, build, verify, and fix, and come back to reviewable, tested work.**
The default workflow is strong enough to adopt as-is, before you customize anything.

If you are searching for **how to run Claude Code overnight without babysitting**, the real question is not just how to leave the terminal open longer.

The real question is this:

> **Can you come back to code you can actually review and decide to merge?**

Ralph Workflow is a **free and open-source** orchestration CLI that runs the coding agents you already use **on your own machine**.

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

Ralph Workflow does **not** replace Claude Code. It wraps the agent you already use in a repo-native overnight workflow so the finish state is easier to trust:

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

If you want to inspect that shape before you install anything, open the public [example review bundle](./example-review-bundle.md).

## Best next step if Claude Code is already your default

Use **Codeberg** as the main public home:

- **Inspect the primary repo on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Star / watch / fork on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report first-run friction on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **GitHub mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

Keeping the trust signals on Codeberg makes the primary repo a clearer evaluation surface.

## Fastest honest first run

1. Keep Claude Code already installed and authenticated on your own machine
2. Pick one real backlog task with clear acceptance criteria
3. Run Ralph Workflow overnight
4. Review the diff, checks, and artifacts in the morning
5. Ask: **does the implementation hold up?**

If you want the shortest path, start with [../START_HERE.md](../START_HERE.md).

If the blocker is still Claude Code approval babysitting, read [claude-code-approval-mode.md](./claude-code-approval-mode.md).

If you want the broader automation framing first, read [claude-code-automation.md](./claude-code-automation.md).

If you want the sharper product comparison, read [ralph-workflow-vs-claude-code.md](./ralph-workflow-vs-claude-code.md).

# Claude Code Automation for Real Repo Work

If you are searching for **Claude Code automation**, the real question is not just how to make Claude keep typing while you are away.

The real question is: **can you come back to a reviewable result instead of a long session and a confident done claim?**

Ralph Workflow is a **free and open-source** orchestration CLI that runs the coding agents you already use **on your own machine**.

It is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

What makes it different is the finish line: Ralph Workflow hands back a **reviewable result** — diff, checks, artifacts, and enough context to decide whether the run actually earned a merge.

Why use it now? Keep Claude Code in the loop, inspect the source on **Codeberg** first, run one real backlog task tonight, and judge the result tomorrow with one question: **would I merge this?**

## What useful Claude Code automation should actually solve

If you already like Claude Code, raw automation still leaves hard problems:

- the task can drift while you are away
- the handoff can be a transcript instead of a clean diff
- tests can be claimed without an obvious review path
- morning-after re-entry can be slower than the actual coding

Useful Claude Code automation should make the **finish state** clearer, not just the runtime longer. That is the gap Ralph Workflow is meant to close.

## What Ralph Workflow adds on top of Claude Code

Ralph Workflow does **not** replace Claude Code. It wraps the agent you already use in a repo-native unattended flow so the result is easier to judge in normal engineering terms:

- what changed
- what checks ran
- what still needs human judgment
- whether you would merge it

That matters more than just keeping the session alive longer.

## What a good automated Claude Code handoff looks like

A strong overnight run should come back looking roughly like this:

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

That is the promise worth holding automation to: **proof of completion, not just a done claim**.

If you want to inspect that shape before you install anything, open the public [example review bundle](./example-review-bundle.md).

## Best next step if Claude Code is already your default

Use **Codeberg** as the main public home:

- **Inspect the source on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Star / watch / fork on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report first-run friction on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **GitHub mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

Keeping the main trust signals on Codeberg makes the primary repo a clearer evaluation surface.

## Fastest honest first run

1. Keep Claude Code already installed and authenticated on your own machine
2. Pick one real backlog task with clear acceptance criteria
3. Run Ralph Workflow overnight
4. Review the diff, checks, and artifacts in the morning
5. Ask: **would I merge this?**

If you want the shortest path, start with [../START_HERE.md](../START_HERE.md).

If you want the clearest contrast first, read [ralph-workflow-vs-claude-code.md](./ralph-workflow-vs-claude-code.md).

If you want the best task filter before you run anything, read [when-unattended-coding-fits.md](./when-unattended-coding-fits.md).

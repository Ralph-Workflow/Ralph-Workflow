# Start Here: Use Ralph Workflow on One Real Task

> **GitHub is the mirror. Codeberg is the primary repo.**
> Inspect and follow Ralph Workflow on Codeberg first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

Ralph Workflow is a **free and open-source** tool that orchestrates coding agents you already use **on your own machine**.

If you only want the shortest honest first run, this is it.
The only question that matters afterward is: **Would I merge this?**
If you cannot judge a merge decision afterward, this is probably not for you yet.

## Before you start

Have these ready:

- one real git repo you care about
- Python 3.12+
- one supported agent CLI already installed
- working auth for that agent

Ralph does **not** replace Claude Code, Codex CLI, or OpenCode.
It orchestrates the agent you already have.

## Pick the right first task

Do **not** start with a vague demo.
Start with one real backlog task you already care about.

Good first tasks:

- a bounded feature slice
- a refactor with tests
- a cleanup task with a clear finish line
- repetitive implementation work that is easy to judge afterward

Bad first tasks:

- risky production surgery
- vague exploration
- tiny edits where setup dominates
- work that depends on constant mid-run steering

## Install and run

```bash
pipx install ralph-workflow
cd /path/to/your/project
ralph --init
ralph --diagnose
$EDITOR PROMPT.md
ralph
```

## Write a spec small enough to judge

Paste something this concrete into `PROMPT.md`:

```markdown
# Goal

Add validation so the CLI rejects empty project names before creating files.
Keep the rest of the flow unchanged.

## Acceptance criteria

- Empty or whitespace-only project names fail with a clear error
- No project files are created for invalid names
- Existing valid-name behavior stays unchanged
- Tests cover the new validation
```

Describe **what** success looks like.
Do not over-prescribe the implementation unless that detail really matters.

## What to look for tomorrow

A useful run should leave you with:

- a real diff
- changed files you can inspect
- checks that actually ran
- clear notes about what changed
- open questions where uncertainty remains

The evaluation question is simple:

> **Would I merge this?**

## If the run feels weak

Do not guess.
Use the result to decide the next action:

- promising result → follow or star the primary repo on Codeberg
- confusing result → open an issue on Codeberg
- weak result → tighten the task and run again

Codeberg issues: <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>

## Next pages only if you need them

- handoff standard — [docs/reviewable-output.md](./docs/reviewable-output.md)
- first task help — [docs/first-task-guide.md](./docs/first-task-guide.md)
- prompt templates — [docs/first-task-prompt-templates.md](./docs/first-task-prompt-templates.md)
- docs map — [docs/README.md](./docs/README.md)

**Primary repo next step**

Use the primary repo on Codeberg for the real project relationship:
<https://codeberg.org/RalphWorkflow/Ralph-Workflow>

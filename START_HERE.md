# Start Here: Run Ralph Workflow on One Real Task

> **GitHub is the mirror. Codeberg is the primary repo.**
> Inspect and follow Ralph Workflow on Codeberg first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

Ralph Workflow is a free and open-source AI agent orchestration system built around a simple Ralph-loop core.
It runs the coding agents you already use on your own machine, turns that simple loop structure into a stronger composable workflow for substantial, well-specified software engineering work, and gives you a strong default workflow before you customize anything.

Bring your existing coding agents. Keep your existing setup. Keep your keys to yourself.
Ralph Workflow is meant to plug into the tools you already trust, not turn “hand us your API keys” into the default setup story.

This page gives the shortest honest first run.
Start with one real, well-specified backlog task and judge the outcome by what the software does now and what checks ran.

## Before you start

Have these ready:

- one real git repo you care about
- Python 3.12+
- one supported agent CLI already installed
- working auth for that agent

## Pick the right first task

Good first tasks:

- a substantial feature slice with clear acceptance criteria
- a refactor with tests and clear acceptance criteria
- a verification or test-coverage pass on behavior you already rely on
- a cleanup task with a real finish line

Bad first tasks:

- tiny edits where setup dominates the work
- vague exploration
- risky production surgery
- work that depends on constant mid-run steering

If you are unsure, use [docs/first-task-guide.md](./docs/first-task-guide.md).

## Install and run

```bash
pipx install ralph-workflow
cd /path/to/your/project
ralph --init
ralph --diagnose
$EDITOR PROMPT.md
ralph
```

## What success looks like

After a good first run, you should be able to point to:

- a real repo change that matches the written task
- meaningful checks that ran and reported clear outcomes
- a result you can review without reconstructing the whole run
- a clear sense of whether the default workflow helped enough to keep using

## Next pages only if you need them

- task selection — [docs/first-task-guide.md](./docs/first-task-guide.md)
- docs switchboard — [docs/README.md](./docs/README.md)
- operator manual — [ralph-workflow/docs/sphinx/index.rst](./ralph-workflow/docs/sphinx/index.rst)

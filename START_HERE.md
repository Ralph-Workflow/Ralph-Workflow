# Start Here: Run Ralph Workflow on One Real Task

> **GitHub is the mirror. Codeberg is the primary repo.**
> Inspect and follow Ralph Workflow on Codeberg first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

Ralph Workflow is a free and open-source AI agent orchestration system built around a simple Ralph-loop core.
It runs the coding agents you already use, on your own machine. The simple Ralph loop composes into complex workflows and ships with a strong default workflow for substantial, well-specified software engineering. Use the default as-is, then customize when you're ready.

This page gives the shortest honest first run.
Start with one real, well-specified backlog task and judge the outcome by what the software does now and what checks ran.
Bring your existing coding agents, keep your existing setup, and keep your keys to yourself.

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

Use `ralph --init` first to set up or repair the baseline bundle, then run `ralph --diagnose` to confirm capability health before your first task.

- `ralph --init` provisions the default local work surface, web helpers, and shipped baseline skills for a first run that is ready to use.
- `ralph --diagnose` is the pre-flight check; it shows which baseline helpers are healthy, missing, unreachable, degraded, or need repair.

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

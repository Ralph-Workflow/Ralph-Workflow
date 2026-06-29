# Start Here: Run Ralph Workflow on One Real Task

> **Codeberg is the primary repo.** Star, watch, and report issues there:
> <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> GitHub is a read-only mirror.

Ralph Workflow is **the autopilot for coding agents** — a free and open-source
AI agent orchestrator that runs the coding agents you already use on your own
machine. The simple Ralph loop composes into a stronger workflow for
substantial, well-specified software engineering. Use the default as-is, then
customize when you're ready.

This page is the **fastest honest first run**: one real, well-specified
backlog task, judged by what the software does now and what checks ran. Bring
your existing coding agents, keep your existing setup, and keep your keys to
yourself.

## Is this for me?

If you have engineering work that's **too big to babysit and too risky to
trust blindly**, this is for you. If your task is a one-line fix or a vague
exploration, it's not — start with a chat session instead.

## Before you start

Have these ready:

- One real git repo you care about
- Python 3.12+
- One supported agent CLI already installed **and authenticated** — see
  [Agent CLI lifecycle](ralph-workflow/docs/sphinx/agents.md) for the
  selection and trust-boundary story

## Pick the right first task

Good first tasks:

- A substantial feature slice with clear acceptance criteria
- A refactor with tests and clear acceptance criteria
- A verification or test-coverage pass on behavior you already rely on
- A cleanup task with a real finish line

Bad first tasks:

- Tiny edits where setup dominates the work
- Vague exploration
- Risky production surgery
- Work that depends on constant mid-run steering

If you're unsure, use [first-task-guide.md](ralph-workflow/docs/sphinx/first-task-guide.md).

## Install and run

```bash
pipx install ralph-workflow
cd /path/to/your/project
ralph --init
ralph --diagnose
$EDITOR PROMPT.md
ralph
```

Run those commands from a human-operated shell outside any Ralph-managed
agent session.

- `ralph --init` provisions the default local work surface, web helpers, and
  shipped baseline skills for a first run that is ready to use.
- `ralph --diagnose` is the **pre-flight check** — it shows which baseline
  helpers are healthy, missing, unreachable, degraded, or need repair before
  you spend a real run on them. See
  [diagnostics.md](ralph-workflow/docs/sphinx/diagnostics.md) for what each
  check proves.

## What success looks like

After a good first run, you should be able to point to:

- A real repo change that matches the written task
- Meaningful checks that ran and reported clear outcomes
- A `development_result` artifact you can review without reconstructing the
  whole run
- A clear sense of whether the default workflow helped enough to keep using

The morning-after review matters more than the running transcript. Open the
diff, run the program, exercise the feature, check the receipts.

## Next pages

- [first-task-guide.md](ralph-workflow/docs/sphinx/first-task-guide.md) —
  choose the right task before you draft your spec
- [diagnostics.md](ralph-workflow/docs/sphinx/diagnostics.md) — what
  `ralph --diagnose` actually checks
- [agents.md](ralph-workflow/docs/sphinx/agents.md) — selection,
  authentication, and invocation for every supported agent
- [after-your-first-run.md](ralph-workflow/docs/sphinx/after-your-first-run.md)
  — turn a real run into a Codeberg signal
- [docs/README.md](docs/README.md) — the docs map (routes by intent)
- [ralph-workflow/docs/sphinx/index.rst](ralph-workflow/docs/sphinx/index.rst)
  — the maintained operator manual
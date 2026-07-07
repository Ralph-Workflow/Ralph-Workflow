# Start Here: try Ralph Workflow on one real backlog task

> **Codeberg is primary:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> GitHub is a read-only mirror:
> <https://github.com/Ralph-Workflow/Ralph-Workflow> (verify: repo-exists)

This is the shortest serious first-run path for a repo visitor.

## What kind of evaluator this path is for

You are considering Ralph Workflow for **one real backlog task** — the
kind of ambitious, well-specified work that is too big to babysit and
too risky to trust blindly. If a task needs more than one prompt, more
than one verification step, or more trust than you want to place in a
single agent session, this path is for you.

## One realistic first-run goal

Pick **one real backlog task** you already want done. Run it unattended.
Judge the result by what the software actually does and what checks ran —
the morning-after review matters more than the running transcript.

## Prerequisites

Have these ready before you start:

- One real git repo you care about
- Python 3.12+
- One supported agent CLI already installed **and authenticated** (see
  [Agent CLI lifecycle](ralph-workflow/docs/sphinx/agents.md) for the
  selection and trust-boundary story)

## Exact first steps

The canonical six-step install → init → diagnose → spec → run → review
sequence is in [`README.md`](README.md#start-your-first-run). Run those
commands from a human-operated shell outside any Ralph-managed agent
session.

- `ralph --init` provisions the default local work surface and shipped
  baseline skills.
- `ralph --diagnose` is the **pre-flight check** — it shows which
  baseline helpers are healthy, missing, unreachable, degraded, or
  need repair before you spend a real run on them.

## What success looks like

A successful first run produces two concrete signals you can read the
morning after.

### `ralph --diagnose` should report all-healthy before you start

After step 4, the pre-flight report should show every line green, with
no missing, degraded, or needs-repair signals:

```text
Ralph Workflow Diagnostics
—————————————————————————————
✓ Git repository
✓ Configuration
✓ Agents (claude, opencode)
✓ MCP servers (3 upstreams reachable)
✓ Workspace files
✓ Capability state (12/12 healthy)
✓ Pre-flight policy validation

All checks passed. Ready for `ralph`.
```

If a line is red, `--diagnose` tells you what is missing, unreachable,
or degraded. Fix that line before you spend a real run on it. The full
failure-mode table is in
[diagnostics.md](ralph-workflow/docs/sphinx/diagnostics.md).

### A successful run leaves a finish-receipt you can review

After step 6 returns, you should find a `development_result` artifact
that names the change, the checks, and the reviewer focus without
reconstructing the run. The canonical finish-receipt block is in
[`README.md`](README.md) under "What a run leaves you". A successful
run leaves a short artifact you can read in under a minute: outcome,
changed files, checks, reviewer focus.

The morning-after review matters more than the running transcript.
Open the diff, run the program against your real environment, exercise
the feature, check the receipts, then decide the next action.

## Where to go next

- [README.md](README.md) — back to the public storefront
- [docs/README.md](docs/README.md) — the docs map (routes by intent)
- [ralph-workflow/docs/sphinx/index.rst](ralph-workflow/docs/sphinx/index.rst) —
  the maintained operator manual
- [ralph-workflow/docs/sphinx/diagnostics.md](ralph-workflow/docs/sphinx/diagnostics.md) —
  what `ralph --diagnose` actually checks
- [ralph-workflow/docs/sphinx/agents.md](ralph-workflow/docs/sphinx/agents.md) —
  selection, authentication, and invocation for every supported agent
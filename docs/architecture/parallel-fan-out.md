# Parallel fan-out (architecture)

This page documents the parallel fan-out architecture: how Ralph
Workflow plans express parallelization intent, and how the executing
agent dispatches sub-agents to satisfy it. The canonical, maintained
explanation lives in
[`ralph-workflow/docs/sphinx/parallel-mode.md`](../ralph-workflow/docs/sphinx/parallel-mode.md);
this page is the architecture-side reference.

## Same-workspace execution

The parallel fan-out uses a **same-workspace** execution model. There
are no separate per-worker checkouts and no post-development merge
step. Workers share the checkout and are isolated from each other
with path restrictions.

## Path isolation via `allowed_directories`

Each work unit in a plan declares an `allowed_directories` scope. The
executing agent dispatches a sub-agent per unit, scoped to that
unit's directories. This is the path-isolation mechanism that keeps
workers from stepping on each other.

## How plans express parallelization intent

A plan communicates parallelization intent to the executing agent
through two shapes:

- `work_units` — same-workspace agent-driven chunks. Each unit
  carries an `allowed_directories` scope.
- `parallel_plan` — read-mostly chunks (parallel exploration,
  investigation, or doc analysis) where the sub-agents work on
  disjoint inputs.

## Worker state

Per-worker state is scoped to `.agent/workers/<unit_id>/` (artifacts,
logs, tmp, handoffs). Per-worker prompt payloads are written under
`.agent/workers/<unit_id>/tmp/prompt_payloads/` so concurrent workers
cannot overwrite each other's payload files.

## See also

- [`ralph-workflow/docs/sphinx/parallel-mode.md`](../ralph-workflow/docs/sphinx/parallel-mode.md) —
  the canonical page
- [`ralph-workflow/docs/sphinx/advanced-pipeline-configuration.md`](../ralph-workflow/docs/sphinx/advanced-pipeline-configuration.md) —
  pipeline policy knobs

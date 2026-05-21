---
orphan: true
---

# Parallel Mode

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple core loop inspired by the original Ralph loop.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


> **New to Ralph Workflow?** Start with the [Getting Started](getting-started.md) walkthrough — it explains the same flow with more context.

When the planning phase produces two or more work units, Ralph Workflow can fan development out across multiple workers in parallel. In v1, those workers all operate against the same git checkout and are isolated from each other with path restrictions and per-worker artifact namespaces.

v1 supports only same-workspace parallel mode — there are no per-worker git branches and no post-development merge step.

## Configuration

Parallelization is configured per phase in `.agent/pipeline.toml`. To enable fan-out for the development phase:

```toml
[phases.development.parallelization]
mode = "same_workspace"
max_parallel_workers = 4
max_work_units = 50
```

| Field | Description |
|-------|-------------|
| `mode` | Must be `"same_workspace"` (only supported mode in v1) |
| `max_parallel_workers` | Maximum number of concurrent development workers |
| `max_work_units` | Upper bound on the number of work units the planning artifact may declare |

If a phase does not declare `[phases.<phase>.parallelization]`, the pipeline fails closed
when a plan declares 2+ work units for that phase.

## How it works

1. The planning phase produces a `plan.json` artifact declaring multiple `work_units`.
2. Ralph Workflow validates the plan for same-workspace safety (disjoint `allowed_directories`, no reserved paths).
3. Each work unit runs as a parallel worker against the shared checkout.
4. Workers are isolated by `allowed_directories` — each worker may only edit its declared subdirectories.
5. Per-worker state is scoped to `.agent/workers/<unit_id>/` (artifacts, logs, tmp, handoffs).
6. Workers coordinate through the `mcp__ralph__coordinate` tool exposed by the MCP server.
7. When all workers complete, the pipeline continues to the normal analysis phase. Post-development coordination is state aggregation only; the shared checkout already contains all worker edits.

## Work unit structure

Each work unit in the planning artifact declares:

- `unit_id` — unique identifier for the work unit (alphanumeric, `_`, `-`, max 64 chars)
- `description` — human-readable description of the task
- `allowed_directories` — list of relative subdirectories the worker is permitted to modify

Every work unit **must** declare at least one entry in `allowed_directories`. Entries must:

- Be non-empty relative paths (no `..`, no absolute paths)
- Not reference reserved paths: `.agent`, `.git`, `.worktrees`, `.`
- Not overlap with another work unit's `allowed_directories` (segment-aware prefix check)

## Worker success criteria

A parallel worker is considered successful when it produces artifact evidence under
`.agent/workers/<unit_id>/artifacts/`. The worker must submit a valid artifact (e.g.,
`development_result`) via the MCP `submit_artifact` tool before exiting.

The worker's process exit code is retained as diagnostic information only and does not
determine success or failure on its own.

Per-worker prompt payloads are written under `.agent/workers/<unit_id>/tmp/prompt_payloads/`
so concurrent workers cannot overwrite each other's payload files.

## Related pages

- [Concepts](concepts.md) — work units and parallel execution terminology
- [Recovery](recovery.md) — recovery behavior and failure classification
- [Configuration Reference](configuration.md) — pipeline config fields

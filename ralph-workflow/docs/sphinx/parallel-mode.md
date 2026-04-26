# Parallel Mode

> **New to Ralph Workflow?** Start with the [Getting Started](getting-started.md) walkthrough — it explains the same flow with more context.

When the planning phase produces two or more work units, Ralph Workflow fans development
out across multiple workers running in parallel. All workers operate directly on the
same git checkout (same-workspace mode) and are isolated from each other through
path restrictions and per-worker artifact namespaces.

## Configuration

Override parallel execution settings in `.agent/pipeline.toml`:

```toml
[parallel_execution]
max_parallel_workers = 4
max_work_units = 50
```

| Field | Description |
|-------|-------------|
| `max_parallel_workers` | Maximum number of concurrent development workers |
| `max_work_units` | Upper bound on the number of work units the planning artifact may declare |

## How it works

1. The planning phase produces a `plan.json` artifact declaring multiple `work_units`.
2. Ralph Workflow validates the plan for same-workspace safety (disjoint `allowed_directories`, no reserved paths).
3. Each work unit runs as a parallel worker against the shared checkout.
4. Workers are isolated by `allowed_directories` — each worker may only edit its declared subdirectories.
5. Per-worker state is scoped to `.agent/workers/<unit_id>/` (artifacts, logs, tmp, handoffs).
6. Workers coordinate through the `mcp__ralph__coordinate` tool exposed by the MCP server.
7. When all workers complete, the pipeline continues to the next phase. There is no merge-back step.

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

## Related pages

- [Concepts](concepts.md) — work units and parallel execution terminology
- [Recovery](recovery.md) — recovery behavior and failure classification
- [Configuration Reference](configuration.md) — pipeline config fields

# Parallel Mode

> **New to Ralph Workflow?** Start with the [Getting Started](getting-started.md) walkthrough — it explains the same flow with more context.

When the planning phase produces two or more work units, Ralph Workflow fans development
out across multiple git worktrees simultaneously.

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
2. Ralph Workflow validates the work unit count against `max_parallel_workers` and `max_work_units`.
3. Each work unit is executed in its own git worktree with its own MCP session.
4. Workers coordinate through the `mcp__ralph__coordinate` tool exposed by the MCP server.
5. When all workers complete, their results are merged back to the main worktree.

## Work unit structure

Each work unit in the planning artifact declares:

- `unit_id` — unique identifier for the work unit
- `description` — human-readable description of the task
- `allowed_directories` — optional list of directories the worker is permitted to modify

If `require_allowed_directories = true` is set in the pipeline policy, every work unit
must declare `allowed_directories`. This enforces isolation between parallel workers.

## Worker success criteria

A parallel worker is considered successful when it produces either:

1. A submitted artifact (e.g., `development_result`) via the MCP `submit_artifact` tool, or
2. Workspace changes (untracked or modified files detected by `git status`).

The worker's process exit code is retained as diagnostic information only and does not
determine success or failure on its own.

## Related pages

- [Concepts](concepts.md) — work units and parallel execution terminology
- [Recovery](recovery.md) — recovery behavior and failure classification
- [Configuration Reference](configuration.md) — pipeline config fields

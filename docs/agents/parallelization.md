# Parallel Development Mode

Ralph supports parallel development fan-out: when planning produces multiple work units, Ralph develops them simultaneously in the **same workspace** (same-workspace v1).

Only the **development** phase fans out in parallel. Review, fix, and commit phases always run serially afterward.

---

## How It Works

Workers share one repository checkout. Isolation comes from:

- **Path restrictions**: each worker is restricted to the directories it declared in `allowed_directories`
- **Per-worker namespaces**: scratch files, logs, and artifacts go under `.agent/workers/<unit_id>/`
- **Policy validation**: overlapping or missing edit areas are rejected at policy load time, before any worker launches

There are no per-worker git branches. When all workers finish, Ralph advances directly to the analysis phase.

## Activation

Parallelization activates automatically when the planning phase produces a `work_units` array with **more than one entry**.

| Work units | Behavior |
|------------|----------|
| 0 | Serial mode |
| 1 | Serial mode (no parallelism benefit) |
| 2+ | Parallel fan-out begins |

The planning artifact must include a `work_units` array:

```json
{
  "work_units": [
    {
      "unit_id": "api-endpoints",
      "description": "Implement REST API endpoints for user management",
      "allowed_directories": ["src/api/"],
      "dependencies": []
    },
    {
      "unit_id": "database-schema",
      "description": "Update database schema for user table",
      "allowed_directories": ["src/db/"],
      "dependencies": []
    }
  ]
}
```

---

## Authoring PROMPT.md for Parallel Mode

Write your planning prompt to explicitly request a `work_units` array in the final artifact. The key is to make the work decomposition concrete and independent.

### What to Ask For

```
After analyzing the requirements, produce a plan that:

1. Identifies distinct, independent areas of work (e.g., separate modules,
   different features, distinct infrastructure components)
2. For each area, specifies which directories the work will touch
3. Ensures units have no circular dependencies

Return your plan as a JSON artifact with a `work_units` array.
```

### Work Unit Requirements

Each `unit_id` must:
- Be 1-64 characters from `[a-zA-Z0-9_-]`
- Have a clear description of what it covers
- List `allowed_directories` to scope the agent's file access
- Declare any `dependencies` (other unit_ids that must complete first)

### Unit Dependency Rules

Units can declare dependencies. Ralph respects the dependency graph when scheduling:

```json
{
  "unit_id": "integration-tests",
  "description": "Write integration tests for API and database",
  "allowed_directories": ["tests/"],
  "dependencies": ["api-endpoints", "database-schema"]
}
```

Units without dependencies run as soon as a worker is available. Units with dependencies wait until their prerequisites complete.

### What to Avoid

- **Overlapping file scopes**: Two units touching the same files will be rejected at policy load time
- **Missing boundaries**: Vague descriptions lead to coordination problems
- **Circular dependencies**: Unit A depending on Unit B and B depending on A causes pipeline failure
- **Reserved paths**: Units must not declare `.agent`, `.git`, or `.` as allowed directories

---

## Policy Configuration

Parallelization is configured **per phase** under `[phases.<phase>.parallelization]` in `.agent/pipeline.toml`. A phase without this block **fails closed**: if planning declares 2+ work units, the pipeline exits with an error before any worker launches.

```toml
[phases.development.parallelization]
mode = "same_workspace"
max_parallel_workers = 4
max_work_units = 25
require_allowed_directories = true
post_fanout_verification = false
```

### Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `mode` | — | Only `"same_workspace"` is supported |
| `max_parallel_workers` | 8 | Maximum concurrent work units |
| `max_work_units` | 50 | Maximum work units accepted from planning artifact |
| `require_allowed_directories` | true | Whether each work unit must declare allowed directories |

---

## Dashboard Interpretation

In TTY mode, Ralph shows a live dashboard with worker progress.

### Status Labels

| Internal Status | Dashboard | Meaning |
|----------------|----------|---------|
| PENDING | WAIT | Scheduled but waiting for a free worker slot |
| RUNNING | RUN | Actively executing |
| SUCCEEDED | DONE | Completed successfully |
| FAILED | FAIL | Exited with an error |
| CANCELLED | CANCELLED | Killed by hard-kill or user interrupt |

### Dashboard Regions

```
┌─────────────────────────────────────────────────────────┐
│ Phase: development  Iteration: 1/5                      │
├─────────────────────────────────────────────────────────┤
│ UNIT              STATUS     PROGRESS                  │
├─────────────────────────────────────────────────────────┤
│ api-endpoints     RUN        12s                       │
│ database-schema   WAIT       queued                    │
├─────────────────────────────────────────────────────────┤
│ Dropped: 3 lines (buffer overflow in CI mode)          │
└─────────────────────────────────────────────────────────┘
```

The `Dropped` counter appears when output lines are discarded due to buffer limits in CI environments. It is informational only.

### Reading Worker Output

Worker output streams in real time. In dashboard mode, output renders in a scrollable region associated with that worker. In lines mode (non-TTY), output is prefixed with the unit_id.

---

## Non-TTY Fallback Behavior

Ralph falls back to lines mode when:

- `CI` environment variable is set to a truthy value
- `NO_COLOR` is present in the environment
- `TERM` is set to `dumb`
- Console reports it is not a terminal
- Console width is 60 characters or fewer

### Lines Mode Output

```
[api-endpoints] status=RUN
[database-schema] status=WAIT
[api-endpoints] Making API request to /users
[api-endpoints] status=DONE
[database-schema] status=RUN
```

Lines mode strips ANSI escape sequences automatically.

---

## Hard-Kill Semantics

When you press Ctrl-C during a parallel run, Ralph performs a hard-kill rather than graceful shutdown.

### First Ctrl-C

1. Kills all tracked subprocess groups via `SIGKILL`
2. Cancels the root task in the asyncio event loop
3. Saves a checkpoint with `interrupted_by_user=true`
4. Exits with code 130

Per-worker namespaces under `.agent/workers/` are preserved for post-mortem inspection.

### Second Ctrl-C

Calls `os._exit(130)` immediately with no cleanup.

### After Hard-Kill

The pipeline saves a checkpoint, so you can resume from where workers left off. Workers that had already completed will not be re-invoked on resume. Run `ralph cleanup` to remove stale per-worker namespaces.

---

## ralph cleanup Command

Removes stale per-worker namespaces after a hard-kill or failed parallel run.

```bash
# See what would be deleted (dry-run)
ralph cleanup --dry-run

# Remove stale namespaces (with confirmation)
ralph cleanup

# Remove without confirmation (for scripts)
ralph cleanup --force
```

Exit codes: `0` = cleaned successfully or nothing to clean; `1` = error (not in a git repository, etc.)

---

## Edit Area Safety

Same-workspace parallelism is **soft isolation**, not hard isolation. Safety comes from:

1. **Pre-flight validation**: overlapping `allowed_directories` are rejected before any worker launches
2. **Runtime fencing**: each worker can only write to its declared directories and its own namespace
3. **Artifact namespacing**: per-worker evidence lives under `.agent/workers/<unit_id>/artifacts/`

### Rejected Plans

Ralph rejects a plan before execution if:
- Two or more work units have overlapping `allowed_directories`
- Any work unit has an empty `allowed_directories` list
- Any work unit declares a reserved path (`.agent`, `.git`, `.`, or empty string)

The rejection message names the conflicting units and explains what a safe plan would look like.

### What Happens When Workers Are Done

When all workers complete successfully:
1. Ralph collects per-worker artifact evidence from `.agent/workers/<unit_id>/artifacts/`
2. Pipeline advances directly to the analysis phase — no merge step required
3. If `run_post_fanout_verification=True`, workspace-wide verification runs once serially

### Partial Failure

If some workers succeed and others fail:
- Pipeline transitions to `PHASE_FAILED`
- Error message names every failed worker (alphabetically sorted)
- Successful worker outputs remain in the shared workspace
- Ralph does not roll back partial edits — same-workspace mode does not support automatic rollback

---

## Troubleshooting

### "MCP port bind failed" Error

Each worker launches its own MCP server. If you see this error, another process is using the MCP port:

```bash
lsof -i :{MCP_PORT}
# or
ps aux | grep mcp
```

Kill stale processes and retry.

### "worker FAILED" Interpretation

1. Check the worker's output for the error message
2. Look at `.agent/workers/<unit_id>/logs/` for context
3. Common causes:
   - Agent crashed or was killed externally
   - Agent produced invalid output that Ralph could not parse
   - Worker attempted to write outside its declared edit area

### Pipeline Hangs After All Workers Complete

1. Press Ctrl-C to trigger hard-kill
2. Run `ralph cleanup --dry-run` to see remaining worker namespaces
3. Report the issue with the checkpoint and logs

---

## Summary

| Concern | Behavior |
|---------|----------|
| Parallelization trigger | Planning artifact contains 2+ work units |
| Isolation model | Same workspace, path-restricted per worker |
| Max concurrent workers | `max_parallel_workers` (default: 8) |
| Dashboard status labels | WAIT, RUN, DONE, FAIL, CANCELLED |
| Non-TTY mode | Lines with `[unit_id]` prefix |
| Hard-kill | First Ctrl-C: SIGKILL all workers, save checkpoint, exit 130 |
| Cleanup | `ralph cleanup [--dry-run] [--force]` |
| Overlapping edit areas | Rejected at policy load time, before launch |
| Partial failure | Pipeline fails, all failed unit_ids named, no automatic rollback |

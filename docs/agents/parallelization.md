# Parallel Development Mode

Ralph supports parallel development fan-out: when your planning phase produces multiple work units, Ralph can develop them simultaneously in the **same workspace** (same-workspace v1).

Only the **development** phase fans out in parallel. Review, fix, and commit phases always run serially after the parallel development phase completes.

---

## How Same-Workspace Parallelism Works

Workers share one repository checkout. Isolation comes from:

- **Path restrictions**: each worker is restricted to the directories it declared in `allowed_directories`
- **Per-worker namespaces**: scratch files, logs, and artifacts go under `.agent/workers/<unit_id>/`
- **Policy validation**: overlapping or missing edit areas are rejected at policy load time, before any worker launches

There are no per-worker git branches and no branch-integration step. When all workers finish, Ralph advances directly to the analysis phase. The shared workspace accumulates all changes in place.

---

## When parallelization activates

Parallelization activates automatically when the planning phase produces a `work_units` array in its artifact with **more than one entry**.

If planning produces:
- **0 work units**: Pipeline runs in serial mode, unchanged from non-parallel behavior
- **1 work unit**: Pipeline runs in serial mode (no parallelism benefit)
- **2+ work units**: Ralph activates parallel fan-out, spawning multiple agents simultaneously

The planning artifact must include a `work_units` array like:

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

Ralph parses this array during the transition from planning to development. If the array exists and has multiple entries, parallel mode begins immediately.

---

## Authoring PROMPT.md for Parallel Mode

Write your planning prompt to explicitly request a `work_units` array in the final artifact. The key is to make the work decomposition concrete and independent.

### What to Ask For

Tell Ralph to decompose the work into self-contained units that can be developed independently:

```
After analyzing the requirements, produce a plan that:

1. Identifies distinct, independent areas of work (e.g., separate modules,
   different features, distinct infrastructure components)
2. For each area, specifies which directories the work will touch
3. Ensures units have no circular dependencies

Return your plan as a JSON artifact with a `work_units` array.
```

### What Makes a Good Work Unit

Each `unit_id` must:
- Be 1-64 characters from `[a-zA-Z0-9_-]`
- Have a clear description of what it covers
- List `allowed_directories` to scope the agent's file access
- Declare any `dependencies` (other unit_ids that must complete first)

### Unit Dependency Rules

Units can declare dependencies on other units. Ralph respects the dependency graph when scheduling:

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
- **Missing boundaries**: Vague descriptions like "implement features" lead to coordination problems
- **Circular dependencies**: Unit A depending on Unit B and B depending on A causes the pipeline to fail
- **Reserved paths**: Units must not declare `.agent`, `.git`, or `.` as allowed directories

---

## Policy Configuration

Parallelization is configured **per phase** under `[phases.<phase>.parallelization]` in `.agent/pipeline.toml`.
The global `[parallel_execution]` block has been removed — a `ValidationError` is raised if it appears.

```toml
[phases.development.parallelization]
mode = "same_workspace"
max_parallel_workers = 4
max_work_units = 25
require_allowed_directories = true
post_fanout_verification = false
```

A phase without a `[phases.<phase>.parallelization]` block **fails closed**: if the planning artifact
declares 2+ work units for that phase, the pipeline exits with an error before any worker is launched.

### mode

The parallelization mode. Only `"same_workspace"` is supported in v1.

### max_parallel_workers

Maximum number of concurrent work units running at once.

- **Default**: 8
- **Minimum**: 1

Reduce this value if you hit API rate limits or want to limit resource usage.

### max_work_units

Maximum total work units Ralph will accept from a planning artifact.

- **Default**: 50
- **Minimum**: 1

If your planning phase produces more than this limit, Ralph rejects the artifact and the pipeline fails.

### require_allowed_directories

Whether each work unit must declare `allowed_directories`.

- **Default**: true

When true, units without `allowed_directories` cause a validation error.

---

## Dashboard Interpretation

When running in a terminal (TTY mode), Ralph shows a live dashboard that updates as workers progress.

### Status Labels

Each worker displays one of these labels:

| Internal Status | Dashboard Label | Meaning |
|----------------|----------------|---------|
| PENDING        | WAIT           | Scheduled but waiting for a free worker slot |
| RUNNING        | RUN            | Actively executing |
| SUCCEEDED      | DONE           | Completed successfully |
| FAILED         | FAIL           | Exited with an error |
| CANCELLED      | CANCELLED      | Killed by hard-kill or user interrupt |

### Dashboard Regions

```
┌─────────────────────────────────────────────────────────┐
│ Phase: development  Iteration: 1/5                      │
├─────────────────────────────────────────────────────────┤
│ UNIT              STATUS     PROGRESS                  │
├─────────────────────────────────────────────────────────┤
│ api-endpoints     RUN        12s                       │
│ database-schema   WAIT       queued                    │
│ auth-layer        WAIT       queued                    │
├─────────────────────────────────────────────────────────┤
│ Dropped: 3 lines (buffer overflow in CI mode)          │
└─────────────────────────────────────────────────────────┘
```

### Dropped Lines Counter

The dashboard shows a `Dropped` counter when output lines are discarded due to buffer limits. This happens in CI environments where output volume exceeds what the rendering thread can process. The counter appears at the bottom of the dashboard in lines mode and is informational only.

### Reading Worker Output

Worker output streams to the dashboard in real time. Each line appears as it is emitted by the subprocess. In dashboard mode, output is rendered in a scrollable region associated with that worker. In lines mode (non-TTY), output is prefixed with the unit_id for correlation.

---

## Non-TTY Fallback Behavior

When Ralph detects a non-TTY environment, it switches from the live dashboard to a lines-based output format.

### Detection Triggers

Ralph falls back to lines mode when any of these conditions are true:

- `CI` environment variable is set to a truthy value
- `NO_COLOR` is present in the environment
- `TERM` is set to `dumb`
- The console reports it is not a terminal
- Console width is 60 characters or fewer

### Lines Mode Output

Instead of a live dashboard, each event is printed as a simple line:

```
[api-endpoints] status=RUN
[database-schema] status=WAIT
[api-endpoints] Making API request to /users
[api-endpoints] Received 200 response
[api-endpoints] status=DONE
[database-schema] status=RUN
...
```

### CI Environment Recommendations

When running in CI, ensure your terminal width is adequate or set `CI=true` explicitly to get consistent lines-mode output. Lines mode strips ANSI escape sequences automatically.

---

## Hard-Kill Semantics

When you press Ctrl-C during a parallel run, Ralph performs a hard-kill rather than graceful shutdown. This ensures all worker processes are terminated immediately.

### First Ctrl-C

The first interrupt:
1. Kills all tracked subprocess groups via `SIGKILL`
2. Cancels the root task in the asyncio event loop
3. Saves a checkpoint with `interrupted_by_user=true`
4. Exits with code 130

Per-worker namespaces under `.agent/workers/` are preserved on disk for post-mortem inspection.

### Second Ctrl-C

If you press Ctrl-C again within the event loop shutdown window, Ralph calls `os._exit(130)` immediately with no cleanup. This is a last resort.

### After Hard-Kill

The pipeline saves a checkpoint, so you can resume from where workers left off. Workers that had already completed will not be re-invoked on resume. Run `ralph cleanup` to remove stale per-worker namespaces when you are done.

---

## ralph cleanup Command

After a hard-kill or failed parallel run, stale per-worker namespaces may remain in `.agent/workers/`. The cleanup command removes them.

### Usage

```bash
# See what would be deleted (dry-run)
ralph cleanup --dry-run

# Remove stale namespaces (with confirmation prompt)
ralph cleanup

# Remove without confirmation (for scripts)
ralph cleanup --force
```

### What It Cleans

The cleanup command:
1. Scans `.agent/workers/unit-*` directories
2. Removes each stale per-worker namespace directory
3. Reports the number of namespaces removed

### When to Run

- After a hard-kill interrupt
- After a pipeline failure that left `.agent/workers/` state behind
- Before inspecting a fresh run's worker output

### Exit Codes

- `0`: No stale worker namespaces found, or all cleaned successfully
- `1`: Error (not in a git repository, etc.)

---

## Edit Area Safety

Same-workspace parallelism is **soft isolation**, not hard isolation. Safety comes from:

1. **Pre-flight validation**: overlapping `allowed_directories` are rejected before any worker launches
2. **Runtime fencing**: each worker can only write to its declared directories and its own namespace under `.agent/workers/<unit_id>/`
3. **Artifact namespacing**: per-worker success evidence lives under `.agent/workers/<unit_id>/artifacts/` and cannot be confused with another worker's evidence

### Rejected Plans

Ralph rejects a plan before execution if:

- Two or more work units have overlapping `allowed_directories`
- Any work unit has an empty `allowed_directories` list
- Any work unit declares a reserved path (`.agent`, `.git`, `.`, or empty string)

The rejection message names the conflicting units and explains what a safe plan would look like.

### What Happens When Workers Are Done

When all workers complete successfully:

1. Ralph collects per-worker artifact evidence from `.agent/workers/<unit_id>/artifacts/`
2. The pipeline advances directly to the analysis phase — no merge step required
3. If `run_post_fanout_verification=True`, workspace-wide verification runs once, serially, after all workers finish

### Partial Failure

If some workers succeed and others fail:

- The pipeline transitions to `PHASE_FAILED`
- The error message names every failed worker (alphabetically sorted)
- Successful worker outputs remain in the shared workspace
- Ralph does not roll back partial edits — same-workspace mode does not support automatic rollback

---

## Troubleshooting

### "MCP port bind failed" Error

Each worker launches its own MCP server. If you see:

```
MCP server failed to start: Address already in use
```

Another process is using the MCP port. Check for leftover MCP servers from previous runs:

```bash
lsof -i :{MCP_PORT}
# or
ps aux | grep mcp
```

Kill stale processes and retry.

### "worker FAILED" Interpretation

When a worker exits with FAIL status:

1. Check the worker's output for the error message
2. Look at `.agent/workers/<unit_id>/logs/` for context
3. Common causes:
   - Agent crashed or was killed externally
   - The agent produced invalid output that Ralph could not parse
   - The worker attempted to write outside its declared edit area

The unit_id in the FAIL message tells you which work unit failed.

### Pipeline Hangs After All Workers Complete

If the dashboard shows all workers as DONE but the pipeline does not advance:

1. Press Ctrl-C to trigger hard-kill
2. Run `ralph cleanup --dry-run` to see remaining worker namespaces
3. Report the issue with the checkpoint and logs

---

## Example End-to-End Run Transcript

Below is an annotated transcript of a parallel development run.

### Starting the Pipeline

```
$ ralph

Phase: planning
  Planning your development task...
```

The planning agent analyzes the requirements and produces a `work_units` array with three entries.

### Planning Artifact (Condensed)

```json
{
  "decision": "proceed",
  "work_units": [
    {
      "unit_id": "user-api",
      "description": "Implement user CRUD API endpoints",
      "allowed_directories": ["src/api/users/"],
      "dependencies": []
    },
    {
      "unit_id": "auth-service",
      "description": "Implement authentication service",
      "allowed_directories": ["src/auth/"],
      "dependencies": []
    },
    {
      "unit_id": "user-frontend",
      "description": "Implement user management UI",
      "allowed_directories": ["src/ui/users/"],
      "dependencies": ["user-api"]
    }
  ]
}
```

### Fan-Out Begins

```
Phase: development (parallel)
  3 work units scheduled, max 8 concurrent workers

UNIT            STATUS     PROGRESS
user-api        RUN        2s
auth-service    RUN        1s
user-frontend  WAIT       waiting for: user-api

Fan-out started: 2 workers active
```

### Workers Running

```
[user-api] Making API request to /api/users
[auth-service] Validating JWT token structure
[user-api] Received 200 response
[auth-service] Token validation passed
[user-api] status=DONE
[user-frontend] RUN  8s
```

### All Complete

```
UNIT            STATUS     PROGRESS
user-api        DONE       45s
auth-service    DONE       38s
user-frontend   DONE       62s

All workers complete. Advancing to analysis...
```

### Analysis and Complete

```
Phase: development_analysis
  Reviewing combined worker results...

Phase: review
  Running review agent...
  LGTM

Phase: complete
Pipeline completed successfully.
```

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

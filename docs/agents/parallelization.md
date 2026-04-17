# Parallel Development Mode

Ralph supports parallel development fan-out: when your planning phase produces multiple work units, Ralph can develop them simultaneously across multiple git worktrees.

Only the **development** phase fans out in parallel. Review, fix, and commit phases always run serially after the parallel development phase completes and merges.

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

- **Overlapping file scopes**: Two units touching the same files risk merge conflicts
- **Missing boundaries**: Vague descriptions like "implement features" lead to coordination problems
- **Circular dependencies**: Unit A depending on Unit B and B depending on A causes the pipeline to fail

---

## Policy Configuration

Parallel execution is controlled by two settings in your pipeline policy.

### max_parallel_workers

Maximum number of concurrent work units running at once.

- **Default**: 8
- **Minimum**: 1
- **Config file**: `.agent/ralph-workflow.toml` or `~/.config/ralph-workflow.toml`

```toml
[pipeline.parallel_execution]
max_parallel_workers = 4
```

Reduce this value if you hit API rate limits or want to limit resource usage.

### max_work_units

Maximum total work units Ralph will accept from a planning artifact.

- **Default**: 50
- **Minimum**: 1

```toml
[pipeline.parallel_execution]
max_work_units = 25
```

If your planning phase produces more than this limit, Ralph rejects the artifact and the pipeline fails.

### require_allowed_directories

Whether each work unit must declare `allowed_directories`.

- **Default**: true

When true, units without `allowed_directories` cause a validation error. Set to false if you want to allow unrestricted file access per unit.

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
3. Preserves worktrees on disk for post-mortem inspection
4. Saves a checkpoint with `interrupted_by_user=true`
5. Exits with code 130

Worktrees are **not** cleaned up automatically. After a hard-kill, you see orphaned worktrees in `.worktrees/unit-*` directories.

### Second Ctrl-C

If you press Ctrl-C again within the event loop shutdown window, Ralph calls `os._exit(130)` immediately with no cleanup. This is a last resort.

### After Hard-Kill

The pipeline saves a checkpoint, so you can inspect what happened. Run `ralph cleanup` to remove the orphaned worktrees when you are ready.

---

## ralph cleanup Command

After a hard-kill or failed parallel run, orphaned git worktrees may remain in `.worktrees/`. The cleanup command removes them.

### Usage

```bash
# See what would be deleted (dry-run)
ralph cleanup --dry-run

# Remove orphaned worktrees (with confirmation prompt)
ralph cleanup

# Remove without confirmation (for scripts)
ralph cleanup --force
```

### What It Cleans

The cleanup command:
1. Scans `.worktrees/unit-*` directories
2. For each orphaned worktree, destroys the worktree via git worktree remove
3. Deletes the tracking branch `ralph/unit-{unit_id}`
4. Reports the number of worktrees removed

### When to Run

- After a hard-kill interrupt
- After a pipeline failure that left worktrees behind
- Before starting a fresh parallel run (recommended)

### Exit Codes

- `0`: No orphaned worktrees found, or all cleaned successfully
- `1`: Error (not in a git repository, etc.)

---

## Merge Conflict Handling

When parallel workers complete, Ralph merges their branches back into the base branch (typically `main`). Conflicts can occur when two workers modify the same lines.

### Conflict Detection

If a `git merge --no-ff` fails during merge integration, Ralph records which units conflicted:

```
WorkersMergeConflictEvent: units [api-endpoints, auth-layer] caused conflicts
```

The pipeline transitions to `PHASE_FAILED` with an informative error message.

### What Ralph Does on Conflict

1. Aborts the failed merge via `git merge --abort`
2. Emits a `WorkersMergeConflictEvent` naming the conflicting unit IDs
3. Transitions the pipeline to `failed` phase
4. Preserves all worktrees on disk

### What Ralph Does NOT Do

- Ralph does not attempt to resolve merge conflicts automatically
- Ralph does not delete worktrees after conflicts
- Ralph does not retry the merge

### Resolving Conflicts Manually

After a merge conflict:

1. Inspect the conflicting worktrees:
   ```bash
   cd .worktrees/unit-api-endpoints
   git status
   ```

2. Resolve conflicts in each worktree using your normal git workflow

3. Commit the resolution:
   ```bash
   git add -A
   git commit -m "Merge conflict resolution"
   ```

4. Clean up when satisfied:
   ```bash
   ralph cleanup --force
   ```

### Preventing Conflicts

Reduce conflict risk by:
- Designing work units with non-overlapping file scopes
- Having later units depend on earlier units when they must touch shared files
- Running serial development for tightly coupled changes

---

## Troubleshooting

### "shallow clone" Error

If your repository is a shallow clone, git worktree operations may fail with messages like:

```
fatal: cannot checkout 'ralph/unit-api-endpoints'
```

**Fix**: Convert to a full clone:

```bash
git fetch --unshallow
```

Or clone the repository fresh without `--depth`.

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
2. Look at `ralph.log` or the checkpoint for context
3. Common causes:
   - Agent crashed or was killed externally
   - The agent produced invalid output that Ralph could not parse
   - A git operation failed within the worktree

The unit_id in the FAIL message tells you which work unit failed.

### Orphan Worktrees After Normal Exit

If the pipeline exits normally but you still see `.worktrees/` directories, this is a bug. Report it with:
- The checkpoint file
- The `ralph.log` output
- Steps to reproduce

### Pipeline Hangs After All Workers Complete

If the dashboard shows all workers as DONE but the pipeline does not advance:

1. Press Ctrl-C to trigger hard-kill
2. Run `ralph cleanup --dry-run` to see remaining worktrees
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

All workers complete. Merging branches...
```

### Merge Phase

```
Merge: auth-service -> main  [OK]
Merge: user-api -> main      [OK]
Merge: user-frontend -> main [OK]

Phase: review
  Running review agent...
```

### Review and Complete

```
Phase: review
  2 review passes configured

Pass 1/2: Reviewing changes...
  No issues found.

Pass 2/2: Final review...
  LGTM

Phase: complete
Pipeline completed successfully.
```

---

## Summary

| Concern | Behavior |
|---------|----------|
| Parallelization trigger | Planning artifact contains 2+ work units |
| Max concurrent workers | `max_parallel_workers` (default: 8) |
| Dashboard status labels | WAIT, RUN, DONE, FAIL, CANCELLED |
| Non-TTY mode | Lines with `[unit_id]` prefix |
| Hard-kill | First Ctrl-C: SIGKILL all workers, save checkpoint, exit 130 |
| Cleanup | `ralph cleanup [--dry-run] [--force]` |
| Merge conflict | Pipeline fails, conflicting unit_ids named, worktrees preserved |

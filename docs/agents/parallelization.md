# Parallel Development Mode

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.

## Default: agent-driven parallel plan execution

The bundled default sets `parallelism.dispatch_mode = "agent_subagents"` on the development phase (see `ralph/policy/defaults/pipeline.toml`). When planning produces a plan with `work_units` or `parallel_plan`, the executing **AI agent itself dispatches its own sub-agents** per the plan; Ralph Workflow does **not** run parallel workers in the bundled default. The declared `work_units` are informational and are read by the executing agent as parallelization intent.

The effect router (`ralph/pipeline/effect_router.py`) reads the `agent_subagents` flag and falls through to `InvokeAgentEffect` for the development phase. A WARNING is logged:

> Ralph-managed fan-out is dormant in this build; the executing AI agent is expected to dispatch its own sub-agents per the plan. The declared work_units are informational; the agent will read them as parallelization intent.

If you are authoring a planning prompt for parallel plan execution, write it to explicitly request a `work_units` or `parallel_plan` payload and let the executing agent decompose and dispatch.

## Dormant: Ralph-managed same-workspace fan-out

The Ralph-managed same-workspace fan-out machinery is **dormant** in this build. It is retained intact for future re-arming but is not the path that runs in the bundled default.

To re-arm Ralph-managed fan-out on a phase, set:

```toml
[blocks.development.phase.parallelization]
mode = "same_workspace"
dispatch_mode = "ralph_fan_out"   # the legacy path; the bundled default is "agent_subagents"
max_parallel_workers = 8
max_work_units = 50
```

When `dispatch_mode = "ralph_fan_out"`, the dormant machinery described below is re-invoked: `FanOutEffect` (in `ralph/pipeline/effects/`), `ralph/pipeline/fan_out.py`, and `ralph/pipeline/parallel/` are kept intact and will run as soon as the flag flips back.

The dormant marker is enforced by an audit at `ralph/testing/audit_parallelization_dormant.py` that runs under `make verify` and checks for the new wording in `planning.jinja`, the format doc, the effect-router WARNING, the bundled `pipeline.toml`, and the rubric dimension in `planning_analysis.jinja`.

For architectural detail on the dormant machinery, see `docs/architecture/parallel-fan-out.md`. The rest of this page covers the dormant-fan-out operator surface so it remains accurate if the flag is re-armed.

---

## Dormant Ralph-managed fan-out — operator surface

The operator-facing sections below describe the dormant Ralph-managed same-workspace fan-out path. They are kept verbatim so they remain accurate if `dispatch_mode` is re-armed; the bundled default does not exercise them.

### Activation (dormant path)

When `dispatch_mode = "ralph_fan_out"`, parallelization activates automatically when the planning phase produces a `work_units` array with **more than one entry**.

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

### Authoring PROMPT.md for parallel mode (dormant path)

Write your planning prompt to explicitly request a `work_units` array in the final artifact. The key is to make the work decomposition concrete and independent.

#### What to Ask For

```
After analyzing the requirements, produce a plan that:

1. Identifies distinct, independent areas of work (e.g., separate modules,
   different features, distinct infrastructure components)
2. For each area, specifies which directories the work will touch
3. Ensures units have no circular dependencies

Return your plan as a JSON artifact with a `work_units` array.
```

#### Work Unit Requirements

Each `unit_id` must:
- Be 1-64 characters from `[a-zA-Z0-9_-]`
- Have a clear description of what it covers
- List `allowed_directories` to scope the agent's file access
- Declare any `dependencies` (other unit_ids that must complete first)

#### Unit Dependency Rules

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

#### What to Avoid

- **Overlapping file scopes**: Two units touching the same files will be rejected at policy load time
- **Missing boundaries**: Vague descriptions lead to coordination problems
- **Circular dependencies**: Unit A depending on Unit B and B depending on A causes pipeline failure
- **Reserved paths**: Units must not declare `.agent`, `.git`, or `.` as allowed directories

### Policy Configuration (dormant path)

Parallelization is configured **per phase** under `[blocks.<phase>.phase.parallelization]` in the bundled `ralph/policy/defaults/pipeline.toml`. A phase without this block **fails closed**: if planning declares 2+ work units and the dormant flag is on, the pipeline exits with an error before any worker launches.

```toml
[blocks.development.phase.parallelization]
mode = "same_workspace"
dispatch_mode = "ralph_fan_out"   # the legacy path; the bundled default is "agent_subagents"
max_parallel_workers = 8
max_work_units = 50
require_allowed_directories = true
post_fanout_verification = false
```

#### Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `mode` | — | Only `"same_workspace"` is supported |
| `dispatch_mode` | `"agent_subagents"` | `"agent_subagents"` (default, bundled) or `"ralph_fan_out"` (dormant) |
| `max_parallel_workers` | 8 | Maximum concurrent work units (dormant path only) |
| `max_work_units` | 50 | Maximum work units accepted from planning artifact (dormant path only) |
| `require_allowed_directories` | true | Whether each work unit must declare allowed directories |

### Dashboard Interpretation (dormant path)

In TTY mode, Ralph shows a live dashboard with worker progress.

#### Status Labels

| Internal Status | Dashboard | Meaning |
|----------------|----------|---------|
| PENDING | WAIT | Scheduled but waiting for a free worker slot |
| RUNNING | RUN | Actively executing |
| SUCCEEDED | DONE | Completed successfully |
| FAILED | FAIL | Exited with an error |
| CANCELLED | CANCELLED | Killed by hard-kill or user interrupt |

#### Dashboard Regions

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

#### Reading Worker Output

Worker output streams in real time. In dashboard mode, output renders in a scrollable region associated with that worker. In lines mode (non-TTY), output is prefixed with the unit_id.

### Non-TTY Fallback Behavior (dormant path)

Ralph falls back to lines mode when:

- `CI` environment variable is set to a truthy value
- `NO_COLOR` is present in the environment
- `TERM` is set to `dumb`
- Console reports it is not a terminal
- Console width is 60 characters or fewer

#### Lines Mode Output

```
[api-endpoints] status=RUN
[database-schema] status=WAIT
[api-endpoints] Making API request to /users
[api-endpoints] status=DONE
[database-schema] status=RUN
```

Lines mode strips ANSI escape sequences automatically.

### Hard-Kill Semantics (dormant path)

When you press Ctrl-C during a parallel run, Ralph performs a hard-kill rather than graceful shutdown.

#### First Ctrl-C

1. Kills all tracked subprocess groups via `SIGKILL`
2. Cancels the root task in the asyncio event loop
3. Saves a checkpoint with `interrupted_by_user=true`
4. Exits with code 130

Per-worker namespaces under `.agent/workers/` are preserved for post-mortem inspection.

#### Second Ctrl-C

Calls `os._exit(130)` immediately with no cleanup.

#### After Hard-Kill

The pipeline saves a checkpoint, so you can resume from where workers left off. Workers that had already completed will not be re-invoked on resume. Run `ralph cleanup` to remove stale per-worker namespaces.

### ralph cleanup Command

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

### Edit Area Safety

Same-workspace parallelism is **soft isolation**, not hard isolation. Safety comes from:

1. **Pre-flight validation**: overlapping `allowed_directories` are rejected before any worker launches
2. **Runtime fencing**: each worker can only write to its declared directories and its own namespace
3. **Artifact namespacing**: per-worker evidence lives under `.agent/workers/<unit_id>/artifacts/`

#### Rejected Plans

Ralph rejects a plan before execution if:
- Two or more work units have overlapping `allowed_directories`
- Any work unit has an empty `allowed_directories` list
- Any work unit declares a reserved path (`.agent`, `.git`, `.`, or empty string)

The rejection message names the conflicting units and explains what a safe plan would look like.

#### What Happens When Workers Are Done

When all workers complete successfully:
1. Ralph collects per-worker artifact evidence from `.agent/workers/<unit_id>/artifacts/`
2. Pipeline advances directly to the analysis phase — no merge step required
3. If `post_fanout_verification=True`, workspace-wide verification runs once serially

#### Partial Failure

If some workers succeed and others fail:
- Pipeline transitions to `PHASE_FAILED`
- Error message names every failed worker (alphabetically sorted)
- Successful worker outputs remain in the shared workspace
- Ralph does not roll back partial edits — same-workspace mode does not support automatic rollback

### Troubleshooting (dormant path)

#### "MCP port bind failed" Error

Each worker launches its own MCP server. If you see this error, another process is using the MCP port:

```bash
lsof -i :{MCP_PORT}
# or
ps aux | grep mcp
```

Kill stale processes and retry.

#### "worker FAILED" Interpretation

1. Check the worker's output for the error message
2. Look at `.agent/workers/<unit_id>/logs/` for context
3. Common causes:
   - Agent crashed or was killed externally
   - Agent produced invalid output that Ralph could not parse
   - Worker attempted to write outside its declared edit area

#### Pipeline Hangs After All Workers Complete

1. Press Ctrl-C to trigger hard-kill
2. Run `ralph cleanup --dry-run` to see remaining worker namespaces
3. Report the issue with the checkpoint and logs

---

## Summary

| Concern | Behavior |
|---------|----------|
| Default dispatch mode | `agent_subagents` (executing AI agent dispatches its own sub-agents) |
| Dormant dispatch mode | `ralph_fan_out` (same-workspace fan-out via Ralph workers) |
| Parallelization trigger | Planning artifact contains 2+ work units (dormant path) |
| Isolation model (dormant path) | Same workspace, path-restricted per worker |
| Max concurrent workers (dormant path) | `max_parallel_workers` (default: 8) |
| Max work units (dormant path) | `max_work_units` (default: 50) |
| Dashboard status labels (dormant path) | WAIT, RUN, DONE, FAIL, CANCELLED |
| Non-TTY mode (dormant path) | Lines with `[unit_id]` prefix |
| Hard-kill (dormant path) | First Ctrl-C: SIGKILL all workers, save checkpoint, exit 130 |
| Cleanup | `ralph cleanup [--dry-run] [--force]` |
| Overlapping edit areas (dormant path) | Rejected at policy load time, before launch |
| Partial failure (dormant path) | Pipeline fails, all failed unit_ids named, no automatic rollback |
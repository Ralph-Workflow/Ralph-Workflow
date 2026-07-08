# Advanced Pipeline Configuration

> **New to Ralph Workflow?** Start with the [Getting Started](getting-started.md) walkthrough — it explains the same flow with more context.

This page is for operators who want to change **how Ralph Workflow itself runs work**.
Use it when you are reshaping the workflow graph, counters, routes, or recovery behavior rather than just swapping one agent setting.
The default workflow is already strong enough to start with unchanged; come here when you can name the behavior you want to improve.

The simple core is what makes deeper composition possible here.
Start with the default workflow first, then change `pipeline.toml` only when you can name the behavior you want to improve.

If your question is only about agents, retry counts, or verbosity, go back to [Configuration Reference](configuration.md). Use this page when you want to change the workflow graph.

## Which file am I editing?

- project-local advanced pipeline policy → `.agent/pipeline.toml`
- user-global default pipeline policy → `~/.config/ralph-workflow-pipeline.toml`
- bundled source of truth / default example → `ralph/policy/defaults/pipeline.toml`

In most real repos, you should start with **`.agent/pipeline.toml`** so you do not accidentally change every project.

After editing, run:

```bash
ralph --check-policy
ralph --explain-policy
ralph --diagnose
```

## What `pipeline.toml` controls

`pipeline.toml` is the policy file that defines Ralph Workflow’s execution graph.

It owns:

- phase definitions
- success / failure / loopback routing
- analysis decisions
- loop counters
- budget counters
- commit policy
- post-commit routes
- recovery policy
- parallel fan-out settings

This is the file you edit when you want to change **how the workflow behaves**, not just which agent runs a drain.

## The major sections

### `entry_block`

`entry_block` names the top-level block where the run starts. The default pipeline uses block-authored policy, so the entry point is a block name rather than a single phase name.

```toml
entry_block = "developer_iteration"
```

The loader resolves `entry_block` to the matching `[blocks.<name>]` definition and derives the initial phase from that block. If you author a custom block-authored workflow, make sure the value matches a declared block.

### `[blocks.*]`

Block-authored policy lets you group phases into reusable, named blocks. Each block has a `kind`:

- `kind = "individual"` — the block contains a single phase (`phase_name` + `phase` table).
- `kind = "group"` — the block contains an ordered list of child blocks (`child_blocks`), a `completion_block` that must succeed for the group to advance, optional `before_complete` cleanup blocks, and counters to increment or reset.

Example group block from the default policy:

```toml
[blocks.developer_iteration]
kind = "group"
child_blocks = [
  "planning",
  "planning_analysis",
  "development",
  "development_commit_cleanup",
  "development_commit",
  "development_analysis",
  "development_final_commit_cleanup",
  "development_final_commit",
  "complete",
  "failed_terminal",
]
completion_block = "development_final_commit"
before_complete = [
  "development_commit_cleanup",
  "development_commit",
  "development_final_commit_cleanup",
]
increments_counter = "iteration"
loop_resets = ["development_analysis_iteration", "commit_cleanup_iteration"]
```

Use `[blocks.*]` when you want to compose the workflow from reusable units rather than declaring a flat phase graph. Most operators can start with the bundled block layout and override only the `[phases.<name>]` details inside the blocks they want to change.

### `[loop_counters.*]`

Loop counters bound repeated analysis loops.

Example:

```toml
[loop_counters.development_analysis_iteration]
default_max = 10
description = "Development analysis loop iteration counter"
```

Use this when you want to cap how many times a phase can bounce between implementation and analysis.

### `[budget_counters.*]`

Budget counters track broader iteration budgets.

Example:

```toml
[budget_counters.iteration]
description = "Development iteration counter (developer cycles)"
tracks_budget = true
default_max = 5
```

Use this when you want post-commit routing to depend on remaining budget.

### `[phases.<name>]`

Each phase defines one step in the workflow graph.

Common fields include:

- `drain`
- `role`
- `prompt_template`
- `transitions`
- `loop_policy`
- `commit_policy`
- `parallelization`
- `artifact_history`
- `artifact_proof_policy`

Roles include:

- `execution`
- `analysis`
- `review`
- `commit`
- `verification`
- `terminal`

### `[phases.<name>.transitions]`

This controls where Ralph Workflow goes next.

Typical keys:

- `on_success`
- `on_failure`
- `on_loopback`

### `[phases.<name>.decisions.*]`

Analysis phases can map explicit decision vocabulary to targets.

Example:

```toml
[phases.development_analysis.decisions.completed]
target = "development_commit"
reset_loop = true

[phases.development_analysis.decisions.request_changes]
target = "development"
reset_loop = false
```

### `[phases.<name>.commit_policy]`

Commit phases define whether a commit advances budget and resets loops.

Example:

```toml
[phases.development_commit.commit_policy]
requires_artifact = true
skipped_advances_progress = true
increments_counter = "iteration"
loop_resets = ["development_analysis_iteration"]
```

### `[phases.<name>.parallelization]`

This is where same-workspace fan-out is configured.

Example:

```toml
[phases.development.parallelization]
dispatch_mode = "agent_subagents"
mode = "same_workspace"
max_parallel_workers = 8
max_work_units = 50
require_allowed_directories = true
post_fanout_verification = false
```

`dispatch_mode = "agent_subagents"` is the bundled default: under this value
the executing agent dispatches its own sub-agents per the plan's `work_units`
or `parallel_plan` (see the [planning prompt](../prompts/planning.jinja)
`## Agent-Driven Parallel Execution` guidance and the
[Parallel execution (agent-driven)](#parallel-execution-agent-driven) section
below for the long-form contract).
Ralph-managed fan-out is dormant. To opt back into the legacy worker flow,
override with `dispatch_mode = "ralph_fan_out"` and the pipeline falls back
to the same-workspace worker model with the coordination tool and per-worker
artifact namespaces.

Use this when you want a planning artifact to split work into multiple development units.

## Parallel execution (agent-driven)

> **Ralph-managed fan-out is dormant in this build.** The operator-facing
> parallel configuration above remains accurate for downstream callers
> that invoke their own parallel agents; the Ralph-managed fan-out
> feature is not exercised by `make verify`.

### What changed

Parallel plan execution is **delegated to the executing AI agent's native sub-agent / task tooling** (Claude Code sub-agents, OpenCode task tool, Codex sub-agents, AGY task tooling, etc.). Pi.dev is wired as a transport but has no documented sub-agent / task tooling per the public pi.dev design philosophy, so `work_units` and `parallel_plan` run sequentially in `unit_id` order for the `pi` transport.

The bundled `pipeline.toml` ships with `dispatch_mode = "agent_subagents"` on the development phase, so the executing agent is the actor that dispatches its own sub-agents and produces the matching `plan_items_proven` evidence. Ralph-managed fan-out is dormant in this build: the same-workspace fan-out worker machinery is retained in policy for future re-arming, but the bundled default does not use it for parallel plan execution.

### How plans express parallelization intent

A plan communicates parallelization intent to the executing agent through two shapes. Both are **agent-facing intent**, not Ralph fan-out instructions:

- `work_units` — same-workspace agent-driven chunks. The planner assigns each unit an `allowed_directories` scope; the executing agent dispatches a sub-agent per unit, scoped to that unit's directories, and produces the matching `plan_items_proven` evidence.
- `parallel_plan` — read-mostly chunks (e.g. parallel exploration, investigation, or doc analysis) where the executing agent's sub-agents work on disjoint inputs and the planner defines the per-unit scope contract.

A plan with no parallelizable work remains just as expressible as before — omit both shapes and the executing agent runs the plan sequentially.

### How the executing agent dispatches sub-agents

When a plan declares `work_units` or `parallel_plan`, the executing agent:

1. Reads the `allowed_directories` of each work unit.
2. Dispatches a sub-agent per unit in dependency order.
3. Aggregates each sub-agent's `plan_items_proven` evidence into the `development_result` artifact.

For capable agents, the agent's native sub-agent / task capability is enabled by default via `[agents.<name>] subagent_capability = true` in `ralph-workflow.toml` (see the [Configuration Reference](configuration.md) table for the per-agent default). Agents without usable sub-agent capability (e.g. `nanocoder` and `pi`) execute the same plan sequentially in `unit_id` order — no correctness loss.

The planning prompt (`planning.jinja`) carries the `## Agent-Driven Parallel Execution` block that tells the planner to write agent-facing intent (work units, dependencies, scope) and forbids routing parallel plan work through Ralph-managed coordination. The continuation template (`developer_iteration_continuation.jinja`) carries the matching `## PARALLEL EXECUTION` block so non-initial-iteration runs still receive the sub-agent dispatch guidance.

### Re-arming Ralph-managed fan-out (dormant)

Ralph-managed fan-out is retained in policy for future use. To opt back into the same-workspace worker model, set the development phase's `parallelization.dispatch_mode` to `ralph_fan_out` in `pipeline.toml`:

```toml
[phases.development.parallelization]
dispatch_mode = "ralph_fan_out"
mode = "same_workspace"
max_parallel_workers = 4
max_work_units = 50
```

Under `ralph_fan_out` the pipeline falls back to the legacy worker flow. The same-workspace model means there are no separate per-worker checkouts and no post-development merge step: workers share the checkout and are isolated from each other with path restrictions (`allowed_directories`) and per-worker artifact namespaces. Per-worker state is scoped to `.agent/workers/<unit_id>/` (artifacts, logs, tmp, handoffs). Per-worker prompt payloads are written under `.agent/workers/<unit_id>/tmp/prompt_payloads/` so concurrent workers cannot overwrite each other's payload files. Workers coordinate through the `mcp__ralph__coordinate` tool exposed by the MCP server.

The bundled default does not enable this path; the override is explicit and per-phase. See the `[phases.<name>.parallelization]` reference above for the full configuration.

### Policy v2 migration note (historical)

The historical migration from a top-level `[parallel_execution]` block to per-phase `[phases.<name>.parallelization]` (introduced in the policy v2 overhaul) moved `max_parallel_workers`, `max_work_units`, `require_allowed_directories`, and `post_fanout_verification` under the development phase. A bundled default `pipeline.toml` that ships a top-level `[parallel_execution]` block fails fast at validation: the loader raises `ValueError` and points the operator at `ralph --regenerate-config` to refresh the bundled template. Run `ralph --explain-policy` after the refresh to confirm the new layout. The error message names the replacement path so the fix is one line per moved field.

### `[[post_commit_routes]]`

These routes decide what happens after a successful commit phase based on budget state.

Typical budget states:

- `remaining`
- `exhausted`
- `no_review`

### `[default_phase_retry_policy]`

The default retry policy applies to every phase that does not declare its own override. It controls how many times a phase may be retried before the failure is escalated.

```toml
[default_phase_retry_policy]
max_retries = 3
retry_delay_ms = 1000
retry_in_session = false
```

| Key | Default | Description |
|-----|---------|-------------|
| `max_retries` | `3` | Maximum retry attempts per phase under this policy. |
| `retry_delay_ms` | `1000` | Base delay before a retry. |
| `retry_in_session` | `false` | When `true`, retries stay inside the same agent session; when `false`, each retry starts a fresh session. |

Use this when you want a single global retry behavior rather than per-phase retry tables.

### `[recovery]`

Recovery defines cycle caps and the terminal-failure route.

This is where you change how far the workflow is allowed to keep trying before it gives up.

## Common advanced user stories

### I want a longer development-analysis loop

Edit the matching `[loop_counters.*]` entry and the relevant analysis phase.

### I want a custom post-commit route

Edit `[[post_commit_routes]]`.

### I want a new phase in the workflow

Add a new `[phases.<name>]` block and ensure all transitions into and out of it are valid.

### I want the workflow to fail faster

Lower loop caps, budget caps, retry policy, or recovery-cycle limits.

### I want parallel development fan-out

Edit `[phases.<name>.parallelization]` on the execution phase that should split into work units.

## Safe editing workflow

1. Copy the relevant default shape from `ralph/policy/defaults/pipeline.toml`.
2. Make the change in `.agent/pipeline.toml` first.
3. Run `ralph --check-policy`.
4. Run `ralph --explain-policy` and read the rendered graph.
5. Run `ralph --diagnose` before trusting the next unattended run.

If `--explain-policy` looks wrong, the policy is not ready.

## What usually goes wrong

- adding a phase without valid transitions
- changing decision vocabulary in artifacts without updating phase decisions
- editing `ralph-workflow.toml` when the real change belongs in `pipeline.toml`
- changing loop/budget behavior without checking the rendered policy explanation

## Related

- [Configuration Reference](configuration.md)
- [Policy Explanation](configuration.md#inspecting-the-active-policy)
- [Advanced Artifact Configuration](advanced-artifact-configuration.md)

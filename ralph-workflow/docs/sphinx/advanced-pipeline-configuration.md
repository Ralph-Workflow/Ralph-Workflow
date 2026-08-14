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
default_max = 3
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

### `[phases.<name>.invocation_gate]`

An optional gate on an analysis phase that delays analysis until the upstream
execution phase has accumulated enough wall-clock time in the current outer
cycle. The bundled `development_analysis` phase ships one:

```toml
[phases.development_analysis.invocation_gate]
upstream_execution_phase = "development"
minimum_elapsed_seconds = 900.0
always_invoke_statuses = ["partial", "failed"]
```

| Key | Description |
|-----|-------------|
| `upstream_execution_phase` | Name of the execution phase whose cumulative timing is measured. Must have `role = "execution"` and match the phase reached by following this analysis phase's success and loopback transitions. |
| `minimum_elapsed_seconds` | Cumulative elapsed seconds at or above which analysis runs. Below it the committed result skips to the analysis phase's policy-declared success route without consuming an analysis cycle. |
| `always_invoke_statuses` | Execution result statuses that bypass the time threshold and always enter analysis. Valid values are the closed vocabulary declared by the upstream execution artifact: `completed`, `partial`, `failed`. |

The gate sums the **unrounded** `elapsed.total_seconds()` values from all
`upstream_execution_phase` timing records in the current outer development
cycle — not the separately truncated `elapsed_seconds` display field.
Fractional totals that straddle the threshold (for example, 899.9 vs 900.1)
are resolved correctly. The cycle-start marker resets at each outer lifecycle
commit so a short later cycle cannot borrow time from an earlier one.

Omitting `invocation_gate` preserves the existing behavior: every committed
development result enters analysis immediately.

With the bundled configuration, a `completed` result below 900 seconds skips
analysis (the cycle closes immediately through the final commit), while
`partial` and `failed` results always enter analysis and consume one analysis
cycle, regardless of elapsed time. Every status at or above 900 seconds enters
analysis.

### Analysis decision outcomes

When the gate admits a development result, the analysis agent produces a
decision artifact with one of three statuses. The pipeline routes each status
through the phase's `decisions` table:

- **`completed`** — all criterion verdicts are `met`; the result advances to
  the success route.
- **`request_changes`** — actionable development work remains. Every finding
  must include a `Remaining work:` statement naming the executable change, a
  concrete repository `Location:` (path, optionally with line/span), and
  identify either `Criterion:` or `Plan reference: [S-n]`. Placeholder
  locations such as `unknown` or `n/a` are rejected. The result loops back to
  development.
- **`failed`** — the analyzer found an impossible, contradictory, or unsafe
  condition (including `not evaluable` verdicts); the result follows the
  failure route. A failed decision closes the current cycle through the final
  commit; policy then starts a fresh planning and development cycle when outer
  cycle budget remains, or routes to the terminal failure phase when it does
  not.

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
below for the long-form contract). When AGY is selected with two or more work
units, routing fails explicitly rather than falling back to sequential dispatch
(this routing policy is unchanged by the note below).
`agy agents` reported no sub-agents on the measured stock v1.1.8 install. This
is a *subcommand listing* observation, not proof AGY lacks subagent
capability: a later v1.1.10 live-binary measurement found `define_subagent` /
`invoke_subagent` / `manage_subagents` in AGY's own tool list, and a capture
confirmed two subagents actually dispatched and completed in parallel through
those tools (see
[Agent Compatibility](agent-compatibility.md#agy) and the git-tracked
`tests/display/_fixtures/agy_wire_provenance.md`).
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

Parallel plan execution is **delegated to the executing AI agent's native sub-agent / task tooling** (Claude Code sub-agents, OpenCode task tool, Codex sub-agents, etc.). When AGY is selected for two or more work units, routing fails explicitly: `agy agents` reported no sub-agents on the measured stock v1.1.8 install. This is a *subcommand listing* observation, not proof AGY lacks subagent capability -- a later v1.1.10 live-binary measurement found `define_subagent` / `invoke_subagent` / `manage_subagents` in AGY's own tool list and confirmed two subagents dispatched and completed in parallel through those tools (see [Agent Compatibility](agent-compatibility.md#agy)). This routing-policy decision -- fail explicitly rather than fall back sequentially for AGY -- is a deliberate Ralph Workflow choice, unaffected by the corrected capability fact, and stays out of scope for the corresponding parsing-fidelity fix. Pi.dev likewise runs `work_units` and `parallel_plan` sequentially in `unit_id` order.

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

For capable agents, the agent's native sub-agent / task capability is enabled by default via `[agents.<name>] subagent_capability = true` in `ralph-workflow.toml` (see the [Configuration Reference](configuration.md) table for the per-agent default). AGY with two or more work units fails explicitly based on the measured stock-install observation above; it does not fall back sequentially. Nanocoder and Pi execute the same plan sequentially in `unit_id` order — no correctness loss.

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

A route may also match on `cycle_outcome`, the verdict the cycle carried
into its commit. Several routes into a final commit never record one —
an agent-chain `workflow_fallback`, a `result_status_post_commit`
override, a checkpoint written before cycle outcomes existed, or an
analysis phase that succeeded without emitting a decision. A commit
phase reached with no recorded verdict is routed as `completed` (with a
warning naming the phase and the substituted outcome) rather than
skipping the route table: falling through lands on the phase's
`on_success` transition, which for a final commit is the terminal — it
would end the whole run while the cycle budget still had room. A commit
phase that declares no routes at all keeps its `on_success` transition
unchanged.

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

### `[cycle_timebox]`

The cycle timebox imposes a configurable wall-clock limit on each
plan-to-final-commit development cycle. When the budget is exhausted,
subsequent development entries are redirected to the configured
finalization target (the final-commit cleanup phase) so the cycle
concludes with a real commit rather than looping indefinitely.

| Field | Default | Description |
|-------|---------|-------------|
| `duration_seconds` | `7200` (120 min) | Finite, positive. Total wall-clock budget per cycle. |
| `start_source` | `planning_analysis` | Source phase of the transition that starts the timer. |
| `start_entry` | `development` | Target phase of the start transition (phase whose entry begins the cycle). |
| `guarded_entry` | `development` | Phase where the deadline is enforced on re-entry. |
| `end_entry` | `development_final_commit_cleanup` | Phase whose entry clears timing. |
| `finalization_target` | `development_final_commit_cleanup` | Redirect target when the deadline is reached. |
| `finalization_cycle_outcome` | `completed` | Cycle outcome stamped on a redirect so `post_commit_routes` route the finished cycle normally. |

A redirect ends the cycle at the dev cycle's final commit — not the run.
The stamped `finalization_cycle_outcome` is what `post_commit_routes`
match on, so a timed-out cycle is followed by another planning cycle
while the `iteration` budget counter has room, and by the terminal phase
only once that budget is spent. Set the field to `failed` if a timed-out
cycle should instead end an out-of-budget run in the failure terminal.

The 80% soft-warning threshold is derived automatically: at the default
`7200`s duration the warning fires at `5760`s (96 min), giving the
agent a 24-minute window to triage and finalize. The warning is
injected into the development prompt and surfaces in the run-time
report; it does not interrupt an already-running invocation. Each
subsequent development invocation in the same cycle receives an updated
warning with the current elapsed and remaining time.

The warning does not rely on that prompt appendix alone, which an agent
loses to context compaction and which never reaches an invocation that
began before the warning point:

- **On every MCP tool result.** The deadline instant is fixed for the
  lifetime of an invocation, so the pipeline publishes it as wall-clock
  epochs (`RALPH_CYCLE_WARN_EPOCH`, `RALPH_CYCLE_DEADLINE_EPOCH`,
  `RALPH_CYCLE_FINALIZATION_TARGET`) in the environment inherited by the
  MCP server subprocess, which appends a banner with the remaining
  minutes to every tool result once the warning point passes. The
  publication is withdrawn for any invocation outside a guarded cycle,
  so a later phase never inherits a stale deadline.
- **On the live phase banner.** Every phase change during an active
  cycle carries a `[cycle timebox ...]` item with consumed/remaining
  minutes, and a deadline-forced finalization is labelled as such, so an
  operator can see the budget draining without waiting for the
  end-of-run report.

#### Relationship to other limits

The cycle timebox is independent of the existing **60-minute
per-invocation development-phase limit** (the soft timeout that bounds a
single agent invocation) and the analysis-loop iteration cap. The
cycle timebox bounds the *full* plan-to-final-commit cycle — across
development, intermediate commit, development analysis, and every
loopback — while the 60-minute limit bounds a *single* phase invocation
and resets on each fresh attempt. Neither limit substitutes for the
other; both are enforced independently.

#### Timer reset and checkpoint behavior

The timer starts only when routing advances from `start_source` to
`start_entry` — by default, the `planning_analysis` → `development`
transition. Time spent in planning or planning analysis before that
handoff does not count. An unrelated route into the same phase (for
example, a loopback from development analysis) does not start or reset
the cycle.
The deadline is preserved across every development, intermediate-commit,
and development-analysis phase in the same cycle and is **not** reset or
extended when an agent emits output, a phase succeeds, development
analysis requests changes, or an intermediate commit completes. Timing
ends when routing enters the final-commit path (`end_entry`); final
commit execution time is excluded. After final commit, no new timer
starts until the next planner-to-development handoff.

Consumed cycle time is persisted via a serialized consumed-seconds
counter, so checkpoint/resume does not grant a fresh full budget to the
same cycle. On resume, the runtime combines the persisted elapsed total
with a fresh monotonic anchor to continue the same deadline. An older
checkpoint that predates this feature and has no cycle timing state
initializes safely from the resume time without a migration failure and
without charging pre-resume time.

#### Warning guidance and honest incomplete-work reporting

When the 80% threshold is reached, the development agent's prompt
instructs it to prioritize the highest-value remaining plan steps,
reassess whether any step is infeasible within the remaining time
(dependency, missing authority, excessive scope, technical blocker), and
report incomplete or infeasible steps honestly. A warned `partial` or
`failed` development result must set `cycle_timebox_warned: true` in the
artifact frontmatter and include an `## Incomplete Work` section. Each
incomplete-work item must use a stable-ID bracket (e.g. `[S-4]`), a
`Reason:` field explaining why the step is incomplete or infeasible, and
an `Evidence:` field with a reproducible location (file, test, or
command). Items missing any of these three are rejected by artifact
validation — fabricated completion, weakened verification, and silent
omission are not accepted.

The bundled workflow declares a sensible default; no customization is
required.

#### Validation

`duration_seconds` must be finite and greater than zero; zero, negative,
non-finite values, or unknown phase/transition targets fail policy
validation with a message that identifies the offending field.
`start_source`, `start_entry`, `guarded_entry`, `end_entry`, and
`finalization_target` must each reference a declared phase (or terminal
for `finalization_target`). The `start_source` → `start_entry` edge
must be a declared transition in the active graph — it can appear as a
phase transition, analysis decision target, bypass route,
post-commit route, or result-status post-commit route. The 80%
warning point is always derived from the configured duration, so a
custom value retains the same 80% behavior without a second setting.

#### Example: a shorter development cycle

```toml
[cycle_timebox]
duration_seconds = 3600
start_source = "planning_analysis"
start_entry = "development"
guarded_entry = "development"
end_entry = "development_final_commit_cleanup"
finalization_target = "development_final_commit_cleanup"
```

This reduces the budget to 60 minutes; the 80% warning fires at 48
minutes (2880 seconds). Only `duration_seconds` is required to change
the budget — the remaining fields are shown for completeness and match
the bundled defaults.

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

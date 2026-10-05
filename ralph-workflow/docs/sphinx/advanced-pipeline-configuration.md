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
- **`failed`** — the analyzer found stronger or not-evaluable evidence, such
  as an impossible, contradictory, or unsafe condition. Under bundled defaults
  it loops back like `request_changes`, with an explicit mandate to determine
  whether and how the failure can be resolved and to perform actionable work.
  The label itself never fails the pipeline; terminal failure is reserved for
  real pipeline or recovery exhaustion.

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
or `parallel_plan` (see the [planning prompt](../prompts/planning.jinja)'s shared planning guidance and the
[Parallel execution (agent-driven)](#parallel-execution-agent-driven) section
below for the long-form contract). When AGY is selected with two or more work
units, routing uses the same supported agent_subagents path as other
native-subagent transports (see the note below): the executing AGY agent is
expected to dispatch its own subagents, and the run fails observably only when
the measured dispatch/result evidence is missing or uncorrelated.
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
`max_parallel_workers` bounds simultaneous workers; additional ready units run in
later waves. The legacy `max_work_units` and `require_allowed_directories`
settings do not impose plan acceptance rules: total unit count and missing
`Directories:` cannot reject a plan. Ownership can also use exact `Paths:` or
fall back to the unit's steps' `Files:`; indeterminate scope stays with the
main session.

## Parallel execution (agent-driven)

> **Ralph-managed fan-out is dormant in this build.** The operator-facing
> parallel configuration above remains accurate for downstream callers
> that invoke their own parallel agents; the Ralph-managed fan-out
> feature is not exercised by `make verify`.

### What changed

Parallel plan execution is **delegated to the executing AI agent's native sub-agent / task tooling** (Claude Code sub-agents, OpenCode task tool, Codex sub-agents, AGY `define_subagent` / `invoke_subagent` / `manage_subagents`, etc.). When AGY is selected for two or more work units, routing follows the same supported agent_subagents path: `agy agents` reported no sub-agents on the measured stock v1.1.8 install, but that is a *subcommand listing* observation, not proof AGY lacks subagent capability -- a later v1.1.10 live-binary measurement found `define_subagent` / `invoke_subagent` / `manage_subagents` in AGY's own tool list and confirmed two subagents dispatched and completed in parallel through those tools (see [Agent Compatibility](agent-compatibility.md#agy)). AGY parallel runs fail observably only when the measured subagent dispatch or result evidence is missing or uncorrelated, never merely because `agy agents` lists nothing. Subagents and parallel agents are always available; the planning prompt never falls back to a sequential capability branch.

The bundled `pipeline.toml` ships with `dispatch_mode = "agent_subagents"` on the development phase, so the executing agent is the actor that dispatches its own sub-agents and produces the matching `plan_items_proven` evidence. Ralph-managed fan-out is dormant in this build: the same-workspace fan-out worker machinery is retained in policy for future re-arming, but the bundled default does not use it for parallel plan execution.

### How plans express parallelization intent

A plan communicates parallelization intent to the executing agent through two shapes. Both are **agent-facing intent**, not Ralph fan-out instructions:

- `work_units` — same-workspace agent-driven chunks. Each unit's ownership combines `Paths:` (exact files) and `Directories:` (the declared directory limits), or — when the unit declares neither — its steps' `Files:` targets. The executor drops assignments under `.agent`, `.git`, and `.worktrees` (and their descendants) from the effective scope; ownership that resolves to those roots stays in the main session. The executing agent dispatches a sub-agent per unit, scoped to the unit's effective ownership, and produces the matching `plan_items_proven` evidence.
- `parallel_plan` — read-mostly chunks (e.g. parallel exploration, investigation, or doc analysis) where the executing agent's sub-agents work on disjoint inputs and the planner defines the per-unit scope contract. The same combined `Paths:` and `Directories:` ownership, step `Files:` fallback, and protected-root sanitization apply.

A plan with no parallelizable work remains just as expressible as before — omit both shapes and the executing agent runs the plan sequentially. A plan with no usable extracted step or unit IDs is still executed as one prose plan; the development result proves it with exactly one `- [plan] <proof>` entry.

### How the executing agent dispatches sub-agents

When a plan declares `work_units` or `parallel_plan`, the executing agent:

1. Reads each unit's effective ownership (`Paths:` plus `Directories:`, or the unit's steps' `Files:` targets when neither is declared).
2. Builds a wave of ready units whose dependencies are satisfied. The `max_parallel_workers` cap limits concurrent units in one wave, not the total unit count — later ready units run after earlier ones release.
3. Serializes conflicting ownership (file equality, directory ancestry, or directory/file containment) across waves; disjoint files in the same directory may run together.
4. Dispatches a sub-agent per ready unit, scoped to that unit's exact ownership, and collects the unit's `plan_items_proven` evidence.
5. Aggregates each sub-agent's `plan_items_proven` evidence into the `development_result` artifact, proving every work unit and every unowned step the runtime demands.

For capable agents, the agent's native sub-agent / task capability is enabled by default via `[agents.<name>] subagent_capability = true` in `ralph-workflow.toml` (see the [Configuration Reference](configuration.md) table for the per-agent default). The bundled dispatch path is `agent_subagents`; Ralph-managed fan-out is dormant and must be re-armed explicitly per phase. There is no linear capability fallback in the planning prompt: every configured agent is treated as supporting sub-agents and parallel agents.

The planning prompts recommend work units for independent responsibilities, shared contracts before consumers, and integration after fan-in. This is execution guidance, not a required plan format. The continuation template (`developer_iteration_continuation.jinja`) carries the matching `## PARALLEL EXECUTION` block so non-initial-iteration runs still receive the sub-agent dispatch guidance. The shared `shared/_parallel_execution.jinja` partial codifies the same wave / ownership / sanitization rules for the executing agent.

### Re-arming Ralph-managed fan-out (dormant)

Ralph-managed fan-out is retained in policy for future use. To opt back into the same-workspace worker model, set the development phase's `parallelization.dispatch_mode` to `ralph_fan_out` in `pipeline.toml`:

```toml
[phases.development.parallelization]
dispatch_mode = "ralph_fan_out"
mode = "same_workspace"
max_parallel_workers = 4
max_work_units = 50
```

Under `ralph_fan_out` the pipeline falls back to the legacy worker flow. The same-workspace model means there are no separate per-worker checkouts and no post-development merge step: workers share the checkout and are isolated from each other with path restrictions (`Paths:` / `Directories:` / `Files:` ownership, sanitized for `.agent`, `.git`, and `.worktrees`) and per-worker artifact namespaces. Per-worker state is scoped to `.agent/workers/<unit_id>/` (artifacts, logs, tmp, handoffs). Per-worker prompt payloads are written under `.agent/workers/<unit_id>/tmp/prompt_payloads/` so concurrent workers cannot overwrite each other's payload files. Workers coordinate through the `mcp__ralph__coordinate` tool exposed by the MCP server.

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
| `in_session_retry_escalation_limit` | `3` | Consecutive qualifying in-session retries allowed before escalating to an agent failure with standard cooldown and fallover (`[general]` config). |

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
| `duration_seconds` | `36000` (600 min) | Finite, positive number of seconds available to one complete plan-to-final-commit cycle. This is a cycle-wide budget, not a per-agent-session timeout. |
| `start_source` | `planning_analysis` | Source phase of the transition that starts the timer. |
| `start_entry` | `development` | Target phase of the start transition (phase whose entry begins the cycle). |
| `guarded_entry` | `development` | Phase where the deadline is enforced on re-entry. |
| `end_entry` | `development_final_commit_cleanup` | Phase whose entry clears timing. Routing out of the cycle by any other route clears it too. |
| `finalization_target` | `development_final_commit_cleanup` | Redirect target when the deadline is reached. |
| `finalization_cycle_outcome` | `completed` | Cycle outcome stamped on a redirect so `post_commit_routes` route the finished cycle normally. |

A redirect ends the cycle at the dev cycle's final commit, not the run.
The stamped `finalization_cycle_outcome` is what `post_commit_routes`
match on, so a timed-out cycle is followed by another planning cycle
while the `iteration` budget counter has room, and by the terminal phase
only once that budget is spent. Set the field to `failed` if a timed-out
cycle should instead end an out-of-budget run in the failure terminal.

The 80% warning threshold is derived automatically. At the default
`36000` second duration, it is reached at `28800` seconds (480 minutes),
leaving `7200` seconds (120 minutes) before the deadline.

To override the default, copy the `[cycle_timebox]` section into the
project's `.agent/pipeline.toml` and change `duration_seconds`. For
example, this gives each cycle a two-hour wall-clock budget:

```toml
[cycle_timebox]
duration_seconds = 7200
start_source = "planning_analysis"
start_entry = "development"
guarded_entry = "development"
end_entry = "development_final_commit_cleanup"
finalization_target = "development_final_commit_cleanup"
finalization_cycle_outcome = "completed"
```

Keep the other fields aligned with the active workflow graph. Removing
the section disables the cycle timebox for a fully custom pipeline.

The runtime publishes the warning and deadline as wall-clock epochs for
enforcement and artifact validation. It does not append warnings to agent
prompts or MCP tool results. When the deadline fires, the routing
component redirects the guarded entry to `finalization_target`. The
redirect is logged and named on the end-of-run report's
`[CT-2] Redirected cycles: N;` line. The report carries the cycle budget,
consumed time, and any redirect. The count survives across a multi-cycle
run; the recorded reason is for the most recent redirect.

### `[development_timebox]`

The development timebox is a separate `pipeline.toml` table for uninterrupted
development work; placing it in `agents.toml` fails validation and directs you
to `pipeline.toml`. Its bundled default warns at `4200` seconds (70 minutes) and redirects
at `5400` seconds (90 minutes). It is independent of `[cycle_timebox]`: changing
one limit does not reset, extend, or otherwise change the other timer.

| Field | Default | Description |
|-------|---------|-------------|
| `duration_seconds` | `5400` | Finite positive hard stop for development work. |
| `warning_seconds` | `4200` | Finite non-negative warning point, strictly less than `duration_seconds`. |
| `start_source` / `start_entry` | `planning_analysis` / `development` | Transition that starts the timer. |
| `guarded_entry` | `development` | Development entry guarded by the hard stop. |
| `end_entry` / `finalization_target` | `development_final_commit_cleanup` | Entry that ends timing and redirect target. |

Validation failures and same-phase retries do not reset or pause this timer.
The timer ends whenever routing leaves development, including the normal route
to final-commit cleanup; only a later `planning_analysis` → `development`
entry starts a fresh timer. Once the configured hard stop is reached, the next
attempt (including a same-phase retry) follows the existing finalization route.
The runtime publishes its warning and deadline as `RALPH_DEV_WARN_EPOCH` and
`RALPH_DEV_DEADLINE_EPOCH`; these names remain distinct from cycle epochs.
After `RALPH_DEV_WARN_EPOCH`, MCP requires a deliberate second
`declare_complete()` call for the same session and run identity. There is no
separate 60-minute per-invocation development timer. Unknown
`[development_timebox]` keys are rejected rather than ignored.

#### Relationship to other limits

The cycle timebox is independent of the development timebox and the
analysis-loop iteration cap. The cycle timebox bounds the *full*
plan-to-final-commit cycle — across development, intermediate commit,
development analysis, and every loopback — while the development timebox
bounds one uninterrupted development phase across validation failures and
same-phase retries. Its default 70-minute warning and 90-minute hard stop are
published to MCP as phase-wide epochs, so validation failures cannot reset
them. Neither limit substitutes for the other; both are enforced independently.

#### Timer reset and checkpoint behavior

The timer starts when routing advances from `start_source` to
`start_entry` — by default, the `planning_analysis` → `development`
transition. Time spent in planning or planning analysis before that
handoff does not count. An unrelated route into the same phase (for
example, a loopback from development analysis) does not start or reset
the cycle, and is therefore left untimed.

Two routes start a cycle without traversing that edge literally. When
`start_source` is skipped because its own loop budget is spent, the
cycle still starts on entry to `start_entry` — otherwise that whole
cycle would run with no deadline. And a checkpoint written before this
feature existed, resumed anywhere inside the loop, starts a fresh timer
from the resume point; the phases before the cycle (the entry phase and
`start_source`) and the finalization path from `end_entry` onward are
excluded, so planning time is never charged and no timer is started
where it could never be concluded.
The deadline is preserved across every development, intermediate-commit,
and development-analysis phase in the same cycle and is **not** reset or
extended when an agent emits output, a phase succeeds, development
analysis requests changes, or an intermediate commit completes. Timing
ends when routing enters the final-commit path (`end_entry`); final
commit execution time is excluded. Timing also ends when routing leaves
the cycle by any other route — a `result_status_post_commit` override or
an agent-chain `workflow_fallback` can return to planning without
passing through `end_entry`, and a timer left running there could never
stop, since starting one requires an inactive cycle. After the cycle
ends, no new timer starts until the next planner-to-development
handoff.

Consumed cycle time is persisted via a serialized consumed-seconds
counter, so checkpoint/resume does not grant a fresh full budget to the
same cycle. On resume, the runtime combines the persisted elapsed total
with a fresh monotonic anchor to continue the same deadline. An older
checkpoint that predates this feature and has no cycle timing state
initializes safely from the resume time without a migration failure and
without charging pre-resume time.

#### Artifact validation after the warning threshold

When the runtime determines that the 80% threshold has been reached, a
`partial` or `failed` development result must include an `## Incomplete Work` section.
Each incomplete-work item must use a stable-ID bracket (e.g. `[S-4]`), a
`Reason:` field explaining why the step is incomplete or infeasible, and
an `Evidence:` field with a reproducible location (file, test, or
command). Both fields go on **indented continuation lines** under the
item, spelled with their leading capital. Items missing any of these
three are rejected by artifact validation, and a bullet carrying no
stable-ID bracket is rejected rather than silently dropped, so silent
omission is not accepted. A warned `completed`
result is checked too: it must carry a `## Plan Items Proven` section
naming what was proved, so declaring completion is not a way around the
requirement to show your work. Validation cannot detect a fabricated
proof — what it enforces is that a claim made under warning is
accompanied by one.

Whether the cycle warned is decided by the runtime, not by the reporting
agent. Artifact validation reads the published warning epoch
(`RALPH_CYCLE_WARN_EPOCH`). A self-declared `cycle_timebox_warned: true`
frontmatter flag is also honoured, so a result validated outside its
warned invocation, such as a replay or hand-written report, keeps the
stricter reading.

The bundled workflow declares a sensible default; no customization is
required.

#### Validation

`duration_seconds` must be finite and greater than zero; zero, negative,
non-finite values, or unknown phase/transition targets fail policy
validation with a message that identifies the offending field.
`start_source`, `start_entry`, `guarded_entry`, `end_entry`, and
`finalization_target` must each reference a declared phase or a declared
terminal. The `start_source` → `start_entry` edge
must be a declared transition in the active graph — it can appear as a
phase transition, analysis decision target, bypass route,
post-commit route, or result-status post-commit route. The 80%
warning point is always derived from the configured duration, so a
custom value retains the same 80% behavior without a second setting.
Unknown keys in `[cycle_timebox]` are rejected rather than ignored: a
misspelled field would otherwise leave the default in force with no
indication the setting had no effect.

One case is disabled rather than rejected. If you customize the graph
without declaring your own `[cycle_timebox]`, the inherited default may
name phases or a start edge your graph does not have — it was written for
the bundled graph. Rather than fail the load, the timebox is switched off
and a warning is logged naming the field that did not fit. **That run has
no cycle deadline**, and every surface that would show one is silent, so
declare `[cycle_timebox]` explicitly whenever you rename or restructure
the phases it references.

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

## Timebox-aware development prompts and wrapup notice

The development phase surfaces a deadline-budget signal to the running agent
through the existing cycle-deadline environment helpers. When the runtime
publishes a `DEV_DEADLINE_EPOCH` and the agent renders the developer prompt
or the development wrapup notice, both surfaces use the same minutes
remaining convention (integer seconds `// 60`, clamped to `≥ 0`):

- The developer prompt includes a remaining-minutes warning and a force-cut
  sentence in `shared/_run_budget.j2` so the agent drives the current task
  to a verifiable state (a green suite, a passing focused test, or an
  explicit partial report) before the deadline and does not start new work
  it cannot finish.
- The development wrapup notice
  (`ralph.mcp.server._session_wrapup.development_wrapup_notice`) renders
  the same remaining minutes, suggests dispatching an independent ready
  group when ready steps remain, and instructs the agent to submit the
  development result before the cut rather than after it. The static text
  is byte-identical to the pre-timebox wording when no `DEV_DEADLINE_EPOCH`
  is published.

The shared minutes convention means the wrapup notice and the developer
prompt cannot diverge at the warning boundary.

## Work Units execution

Plan submission performs only the plan sanity check; a unit count, ownership
shape, dependency graph, and overlap do not reject an otherwise accepted plan.
`max_parallel_workers` is a concurrency limit, not a plan-size limit.
Additional ready units run in queued waves.

Large scope does not justify an assessment-only response or a request to split
the work without advancing it. The coordinator starts a safe, testable
increment while dispatching disjoint ready scopes within capacity; an
isolated worker does the same only within its assignment. These are prompt
instructions, not a guarantee of model behavior or a change to runtime
deadlines, review, or result eligibility.

When a step fails, the executor does not treat a local failure as a global
stop signal. The recovery loop is: inspect the concrete evidence, choose a
materially different hypothesis, pick a narrower increment, or take a
permitted reassignment, and continue the next independent ready reference.
Repeating an unchanged failing action indefinitely is forbidden; reporting
partial progress requires the canonical `partial` and `failed` rule, where
difficulty, elapsed time, and an exhausted iteration budget do not on their
own authorize either status.

Coordinators dispatch independent ready groups even when the plan does not
declare a `## Work Units` block: a plan that names a dependency graph with
exact authorized ownership (or step `Files:` ownership that resolves to
disjoint file sets) is dispatchable, and the executor may treat it as the
same ready group. Unknown ownership, malformed dependency graphs, and
unextractable unit IDs stay in the main session; the executor never invents
broad worker writes to bypass ownership uncertainty.

The executor uses only the orchestration tools actually exposed by the
running runtime. A full dispatch slot is a queued wave plus useful local
work on the next safe increment, not idle abandonment. When no native
sub-agent or task tool is exposed for an attempt, the executor falls back
to bounded sequential progress on the same dependency graph: pick the next
ready reference, implement it, verify it, and continue. Sequencing is not
permission to give up; it is the same plan executed step at a time.

Before transferring ownership from one writer to another, the executor
confirms the former writer has actually stopped — the assignment is closed,
the result is persisted, and no new work is in flight on the same path. Two
writers on the same file are never allowed; dependent steps release only
after the upstream writer's evidence is validated, not on a self-declared
completion. The shared `## PARALLEL EXECUTION` partial codifies these
ownership, wave, and ownership-transfer rules.

The executor derives ownership from `Paths:`, `Directories:` (the declared
directory limits), or step `Files:` when neither is declared. Conflicting
files and directory containment run serially; disjoint files in the same
directory may run together. Protected assignments (`.agent`, `.git`,
`.worktrees` and their descendants) are removed before worker briefs, and
unknown ownership or unextractable graphs remain main-session work. Brokered
write protections continue to enforce filesystem safety regardless of the
dispatch mode.

# Advanced Pipeline Configuration

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
mode = "same_workspace"
max_parallel_workers = 8
max_work_units = 50
require_allowed_directories = true
post_fanout_verification = false
```

Use this when you want a planning artifact to split work into multiple development units.

### `[[post_commit_routes]]`

These routes decide what happens after a successful commit phase based on budget state.

Typical budget states:

- `remaining`
- `exhausted`
- `no_review`

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
- [Policy Explanation](policy-explanation.md)
- [Advanced Artifact Configuration](advanced-artifact-configuration.md)

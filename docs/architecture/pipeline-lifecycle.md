# Pipeline Lifecycle

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


This document describes the end-to-end lifecycle of a Ralph Workflow run: how the pipeline moves through planning, development, analysis, commit, review, and fix phases, and how policy-defined orchestration drives every routing decision.

The implementation lives in `ralph-workflow/ralph/`. All module references point to that package.

Related docs:
- `event-loop-and-reducers.md` — event loop and reducer mechanics
- [`../legacy-rust/architecture/checkpoint-and-resume.md`](../legacy-rust/architecture/checkpoint-and-resume.md) — checkpoint persistence (legacy Rust-era reference; current runtime uses `ralph/pipeline/checkpoint.py` and the recovery controller)
- [`../legacy-rust/architecture/git-and-rebase.md`](../legacy-rust/architecture/git-and-rebase.md) — git baseline behavior (legacy Rust-era reference; current runtime uses `ralph/git/commit_cleanup.py`)

## Policy-Defined Orchestration

Ralph Workflow is a **policy-defined orchestration framework**. The workflow shape — phases, routing decisions, retry budgets, analysis loops, commit semantics, recovery paths, and terminal outcomes — is declared in `.agent/pipeline.toml`, `.agent/agents.toml`, and `.agent/artifacts.toml`. The runtime in `ralph/pipeline/` validates and enforces those declarations without any hardcoded knowledge of specific phase names or routing outcomes.

Every routing decision the reducer makes traces back to a policy field:

- Phase advancement follows `transitions.on_success` / `on_loopback` / `on_failure`
- Analysis loop targets come from `transitions.on_loopback`
- Review bypass routes come from `bypass_routes[phase.clean_outcome]`
- Commit post-routing comes from `post_commit_routes` guarded by budget counters
- Recovery terminal routing comes from `recovery.failed_route`

The phase graph in `pipeline.toml` determines what phases exist and how they connect. The runtime has no secret phase-name recognition.

## The Big Picture

A standard run moves through these phases:

```
planning → development → development_analysis → development_commit
        → review → review_analysis → fix → review_commit → complete
```

This sequence comes from the active `pipeline.toml`, not from hardcoded control flow. To inspect the active pipeline shape:

```bash
ralph --explain-policy
```

The ASCII diagram shows phases, happy-path routing, loopbacks, decision branches, and terminal outcomes derived from the current policy.

## Event Loop

The pipeline is driven by an explicit event loop in `ralph/pipeline/runner.py`:

```
state --orchestrate--> effect --handle--> event --reduce--> next_state
```

- **`PipelineState`** (`ralph/pipeline/state.py`): immutable snapshot of pipeline progress; the checkpoint payload.
- **`Effect`** (`ralph/pipeline/effects/`): an intention to perform I/O. Effects carry no side effects themselves.
- **`Event`** (`ralph/pipeline/events/`): a fact about something that happened. The reducer consumes events.
- **`reduce()`** (`ralph/pipeline/reducer.py`): pure function `(state, event, policy) → (new_state, effects)`. No I/O; fully deterministic; dispatches through policy for all routing.
- **`Orchestrator`** (`ralph/pipeline/orchestrator.py`): pure function that derives the next effect from the current state.

The loop terminates when `state.phase` reaches the configured terminal phase (typically `complete` or the `failed_route` declared in `recovery`).

## Phase Lifecycle

### Planning

The planning phase produces a structured plan artifact. The planning agent reads `PROMPT.md` and writes `.agent/artifacts/plan.json` (machine-readable) and `.agent/PLAN.md` (human/agent handoff).

Advancement: when the planning agent produces a valid plan artifact, the reducer emits `AGENT_SUCCESS` and routes to the next phase declared in `transitions.on_success`.

### Development

The development agent edits the repository to implement the plan. In parallel mode, multiple workers may each implement a subset of the work units declared by the plan.

The development phase can loop via `transitions.on_loopback` back to itself when the analysis phase decides additional work is needed.

### Development Analysis

The analysis agent inspects the diff against the plan and emits a structured decision artifact. The reducer handles two events:

- `ANALYSIS_SUCCESS`: routes via `transitions.on_success` (typically to `development_commit`)
- `ANALYSIS_LOOPBACK`: routes via `transitions.on_loopback` (back to `development`)

Routing comes exclusively from `transitions` — not from decision-key literals in the `decisions` map. The `decisions` map is a vocabulary contract for artifact validation, not a routing table.

The loop counter declared in `[loop_counters.<name>]` bounds iterations before the pipeline routes to the failure path.

### Development Commit

The commit agent generates a commit message and creates a commit. After a successful commit, the reducer routes using `post_commit_routes` guarded by budget counters. The `iteration` budget counter determines whether to continue with review or terminate.

If the diff is empty, the commit is skipped and routing proceeds as if the commit succeeded, without incrementing the commit counter.

### Review

The review agent inspects the committed diff and produces an issues artifact. Two events drive review routing:

- `REVIEW_CLEAN`: the reviewer found no issues. The reducer reads `phase.clean_outcome` from policy to look up the bypass route in `bypass_routes`. Falls back to `transitions.on_success` when `clean_outcome` is not set.
- `REVIEW_ISSUES_FOUND`: issues were found. The `review_outcome` label is read from the phase's `issues_outcome` policy field. Routing follows `transitions.on_loopback`.

Both `clean_outcome` and `issues_outcome` are required policy fields for `role='review'` phases (enforced by `validate_policy_completeness`).

### Review Analysis

Like development analysis, but for the review loop. The analysis agent decides whether the reviewer's issues have been adequately addressed.

- `ANALYSIS_SUCCESS` → `transitions.on_success` (typically `review_commit`)
- `ANALYSIS_LOOPBACK` → `transitions.on_loopback` (back to `fix`)

The `review_analysis_iteration` loop counter bounds the review-fix cycles.

### Fix

The fix agent addresses the issues identified during review. After fix succeeds, routing follows `transitions.on_success` (back to `review_analysis` or directly to `review`).

### Review Commit

After review issues are resolved, the review commit agent commits the fix. Post-commit routing is guarded by the `reviewer_pass` budget counter.

### Complete

The `complete` phase is the terminal success outcome. Its `role='terminal'` declaration in policy makes it terminal; the runtime enforces this through `validate_policy_completeness`.

## Recovery

When any phase exhausts its agent chain or reaches a non-recoverable failure, the reducer transitions to the route declared in `recovery.failed_route`. This is the policy-declared terminal failure path, not a hardcoded constant.

The recovery controller in `ralph/recovery/controller.py` handles failure classification, budget management, and agent retry/fallback decisions within each phase. Workflow-level recovery routing is always policy-owned.

## Pipeline Dependency Injection

The pipeline runtime is composed behind an explicit dependency-injection layer
so that the same session/agent/retry core can be consumed by both the main
pipeline and the plumbing commands (`--generate-commit`, smoke tests, etc.).

### `PipelineDeps` and `PipelineFactory`

`ralph/pipeline/factory.py` defines the public composition surface:

- `PipelineDeps` is a frozen, slots-based dataclass with 13 fields that bundle
  all injectable collaborators:
  - `display_context` — the `DisplayContext` used for all rendering.
  - `model_identity` — an optional pre-resolved `MultimodalModelIdentity` that
    the plumbing bridge passes to `build_session_mcp_plan` via
    `SessionModelOpts(model_identity=...)`. The main pipeline resolves model
    per-effect from `agent_config.model_flag` + transport, so this field is
    primarily for explicit plumbing injection.
  - `system_prompt_materializer` — produces the system prompt for a session.
  - `phase_prompt_materializer` — produces the phase-specific prompt.
  - `artifact_requirements_resolver` — resolves required artifacts for a phase.
  - `bridge_factory` — constructs the session bridge (`AgentSession` + workspace
    + MCP server).
  - `policy_bundle` / `policy_bundle_factory` — policy bundle override or
    factory.
  - `registry_factory`, `state_factory`, `recovery_controller_factory`,
    `marker_watcher_factory`, `snapshot_registry` — ProPipelineHooks overrides.
- `PipelineFactory` is a runtime-checkable Protocol with a single
  `build(config, display_context, *, pro_hooks=None) -> PipelineDeps` method.
- `build_default_pipeline_deps(config, display_context, *, pro_hooks=None)`
  returns a `PipelineDeps` populated with production defaults. When
  `pro_hooks` is supplied, `apply_pro_hooks_to_deps` resolves the policy bundle
  with the same three-way priority used by `run_loop.run`: override short-
  circuits, then factory, then defer to the default loader.

### Shared session bridge

`ralph/pipeline/session_bridge.py` is the single owner of `AgentSession`,
workspace, `McpServerExtras`, and `start_mcp_server` construction. It exposes:

- `build_session_bridge(...)` — builds and starts a session bridge.
- `bridge_env_for(bridge, run_id_label=...)` — returns the two-key MCP env dict
  (`MCP_ENDPOINT_ENV`, `MCP_RUN_ID_ENV`) used by plumbing commands.
- `reset_tool_registry_callback(bridge)` — returns the reset callback when the
  bridge exposes one.

This replaces the three previously duplicated bridge/env/reset construction
sites in `commit_plumbing`, `smoke`, and the main pipeline.

### Consumption

Both the main pipeline and the plumbing commands consume the same factory:

- `ralph/pipeline/run_loop.py:run()` accepts an optional `pipeline_deps`
  argument and reads registry, recovery, policy bundle, state, marker watcher,
  and snapshot registry from it.
- `ralph/pipeline/plumbing/commit_plumbing.py:run_commit_plumbing()` builds
  default deps when none are supplied and sources the bridge, model identity,
  and system-prompt materializer from the deps.
- `ralph/pipeline/plumbing/smoke_plumbing.py:run_smoke_plumbing()` sources the
  bridge and model identity from the deps; the thin CLI surface in
  `ralph/cli/commands/smoke.py` only handles option setup and delegates to it.

### Pro composition point

`build_default_pipeline_deps(..., pro_hooks=ProPipelineHooks(...))` absorbs all
seven `ProPipelineHooks` overrides at build time, so Ralph Workflow Pro can
inject policy, registry, state, recovery, marker watcher, and snapshot
registry through a single call. The helper lives in `factory.py` and uses
`dataclasses.replace` on the frozen `PipelineDeps`, keeping `ProPipelineHooks`
itself decoupled from `PipelineDeps`.

## Where to Look in Code

| Concern | Location |
|---------|----------|
| State machine core | `ralph/pipeline/reducer.py` |
| Phase orchestration | `ralph/pipeline/orchestrator.py` |
| Pipeline state | `ralph/pipeline/state.py` |
| Events | `ralph/pipeline/events/` |
| Effects | `ralph/pipeline/effects/` |
| Runner / event loop | `ralph/pipeline/runner.py` |
| Pipeline DI factory | `ralph/pipeline/factory.py` |
| Shared session bridge | `ralph/pipeline/session_bridge.py` |
| Main pipeline entry | `ralph/pipeline/run_loop.py` (`pipeline_deps` param) |
| Commit plumbing | `ralph/pipeline/plumbing/commit_plumbing.py` |
| Smoke plumbing | `ralph/pipeline/plumbing/smoke_plumbing.py` |
| Policy models | `ralph/policy/models/` |
| Policy validation | `ralph/policy/validation/` |
| Policy loading | `ralph/policy/loader.py` |
| Policy explanation | `ralph/policy/explain/`, `ralph/policy/render.py` |
| Phase handlers | `ralph/phases/` |
| Recovery | `ralph/recovery/` |

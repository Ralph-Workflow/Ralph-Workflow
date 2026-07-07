# Event Loop and Reducer Architecture

This document describes the pipeline event loop and reducer architecture: how `PipelineState`, events, and effects work together, and how the reducer dispatches routing decisions through policy rather than hardcoded match arms.

The implementation lives in `ralph-workflow/ralph/`. All module references point to that package.

Related docs:
- `pipeline-lifecycle.md` — end-to-end lifecycle (planning → development → commit → review → fix)
- [`../legacy-rust/architecture/logging-and-observability.md`](../legacy-rust/architecture/logging-and-observability.md) — per-run logging and observability (legacy Rust-era reference; current runtime uses `ralph/logging.py` and `ralph/logging_models.py`)

## Policy-Defined Orchestration

Ralph Workflow is a **policy-defined orchestration framework**. The reducer is decision-key-agnostic: it dispatches routing through `resolve_next_phase()` and `phase_def.decisions` / `phase_def.bypass_routes` declared in `pipeline.toml`, never through hardcoded outcome strings. Adding or renaming a decision key in policy is sufficient to change routing; no code change is required.

Every routing decision traces back to a policy field:

- `transitions.on_success`, `on_loopback`, `on_failure` — phase advancement
- `bypass_routes[phase.clean_outcome]` — review bypass routing
- `phase.issues_outcome` — review outcome label
- `post_commit_routes` guarded by budget counters — post-commit routing
- `recovery.failed_route` — terminal failure routing

## Core Contract

The pipeline is driven by an explicit event loop with strict separation of concerns:

- **`PipelineState`** (`ralph/pipeline/state.py`): immutable snapshot of pipeline progress. The checkpoint payload. State transitions happen only by applying reducer events.
- **`Effect`** (`ralph/pipeline/effects/`): an intention to perform I/O (invoke an agent, write a checkpoint, run git, etc.). Effects carry no side effects themselves.
- **`Event`** (`ralph/pipeline/events/`): a fact about something that happened. The reducer consumes events to produce the next state.
- **`reduce()`** (`ralph/pipeline/reducer.py`): pure function `(state, event, pipeline_policy, recovery) → (new_state, effects)`. No I/O; no logging; fully deterministic. All routing dispatches through `pipeline_policy`.
- **`Orchestrator`** (`ralph/pipeline/orchestrator.py`): pure function that derives the next effect from the current state.
- **`EffectHandler`** (`ralph/phases/` and `ralph/agents/`): impure executor that performs the effect and reports the outcome as an event.

The loop in `ralph/pipeline/runner.py` cycles:

```
state --orchestrate--> effect --handle--> event --reduce--> next_state
```

This separation enables:
- **Predictable execution**: same state + event sequence produces the same result
- **Testability**: reducers and orchestrators test without filesystem, network, or agents
- **Debuggability**: the event log explains what happened without reverse-engineering control flow
- **Resume/checkpoint**: state is the checkpoint; resume is load state + continue

## The Event Loop

1. **Orchestrate**: inspect `PipelineState` and choose the next `Effect`
2. **Handle**: execute the effect in the handler (I/O happens here)
3. **Emit**: return an `Event` (primary) and optional additional events
4. **Reduce**: compute next state by applying the event via `reduce()`
5. **Repeat** until `state.phase` reaches the configured terminal phase

## PipelineState

`PipelineState` is the canonical application state:

- Single source of truth for pipeline progress
- Checkpoint payload: serialize to JSON to resume later
- Reducer-owned: transitions happen only by applying reducer events

State includes the minimum information needed to deterministically derive the next `Effect`, explain why the pipeline is in its current phase, and safely resume after interruption.

State must not include mutable caches of external reality (filesystem, git status, network) or hidden control flags not driven by events.

## Reducer: Decision-Key-Agnostic Routing

The reducer in `ralph/pipeline/reducer.py` dispatches all phase routing through policy. Key routing helpers:

- `resolve_next_phase(phase, signal, policy)` (`ralph/pipeline/handoffs.py`): looks up `transitions.on_success`, `on_loopback`, or `on_failure` from policy.
- `resolve_post_commit_phase(state, policy)`: selects the post-commit target using `post_commit_routes` and budget counter state.

**Analysis routing** (`_handle_analysis_success`, `_handle_capped_analysis_loopback_policy_driven`): routes exclusively via `transitions.on_success` / `on_loopback`. The `decisions` map in policy is a vocabulary contract used by artifact validation — it is not inspected for routing.

**Review routing** (`_handle_review_clean`, `_handle_review_issues_found`):
- `REVIEW_CLEAN`: reads `phase_def.clean_outcome` from policy, looks up the bypass target in `phase_def.bypass_routes[clean_outcome]`. Falls back to `on_success` when no bypass is set.
- `REVIEW_ISSUES_FOUND`: reads `phase_def.issues_outcome` from policy as the `review_outcome` label. `issues_outcome` is required for any `role='review'` phase — missing it causes startup rejection via `validate_policy_completeness`.

**Terminal routing**: uses `policy.recovery.failed_route` for the failure path and `policy.terminal_phase` for the success path — never hardcoded strings.

## Events

Events are descriptive facts about what happened, not instructions about what to do next.

Current events handled by the reducer (see `ralph/pipeline/events/`):

| Event | Meaning |
|-------|---------|
| `AGENT_SUCCESS` | An agent completed its invocation successfully |
| `AGENT_FAILURE` | An agent invocation failed |
| `AGENT_RETRY` | An agent retry was requested |
| `ANALYSIS_SUCCESS` | Analysis decided to advance |
| `ANALYSIS_LOOPBACK` | Analysis decided to loop back |
| `REVIEW_CLEAN` | Review found no issues |
| `REVIEW_ISSUES_FOUND` | Review found issues |
| `FIX_SUCCESS` | Fix phase completed successfully |
| `FIX_FAILURE` | Fix phase failed |
| `COMMIT_SUCCESS` | Commit was created |
| `COMMIT_SKIPPED` | Commit was skipped (no diff) |
| `COMMIT_FAILURE` | Commit failed |
| `CHECKPOINT_SAVED` | Checkpoint was persisted |
| `INTERRUPTED` | User interrupted the run |
| `COMPLETE` | Pipeline reached terminal success |
| `FAILED` | Pipeline reached terminal failure |
| `PHASE_ADVANCE` | Explicit phase advance requested |
| `FAN_OUT_STARTED` | Parallel fan-out started |
| `ALL_WORKERS_COMPLETE` | All parallel workers finished |

`PhaseFailureEvent` and `WorkerFailedEvent` are handled before the main dispatch and may be delegated to `RecoveryController` for classification-aware retry/fallback decisions.

Event design rules:
- Good: `SomethingSucceeded`, `SomethingFailed { reason }`, `SomethingCompleted`
- Bad: `TryNextAgent`, `ShouldRetry`, `AdvanceToReview`

Routing decisions belong in the reducer, not in event shapes.

## Effects

Effects are granular intentions to perform one type of I/O. Each effect does one thing and reports an outcome event. The orchestrator derives the next effect from state only — it never performs I/O itself.

## Recovery

When a phase exhausts its agent chain, the reducer calls `_enter_failed_recovery()`, which transitions state to `policy.recovery.failed_route`. This is the policy-declared terminal failure path.

The `RecoveryController` in `ralph/recovery/controller.py` manages retry/fallback decisions within a single phase's agent chain. It is passed to the reducer as an optional collaborator; when present, `PhaseFailureEvent` is delegated to it for classification-aware handling.

## Parallel Execution

When the planning artifact declares multiple work units, the orchestrator dispatches parallel workers (see `ralph/pipeline/parallel/`). Worker events (`WorkerStartedEvent`, `WorkerCompletedEvent`, `WorkerFailedEvent`) are handled by the reducer and tracked in `state.worker_states`.

`ALL_WORKERS_COMPLETE` triggers routing via `resolve_next_phase(state.phase, 'success', policy)` — parallel fan-out routing comes from policy, not hardcoded control flow.

## Best Practices: Reducers

Reducers must be deterministic and side-effect free:

- No filesystem, git, network, environment, time, randomness, or logging
- No mutation of shared global state
- No hidden coupling to config: decisions driven by values in `PipelineState` or events

When adding or changing reducer behavior:
1. Write a unit test for `reduce(state, event) → new_state` capturing the decision
2. Confirm it fails for the right reason
3. Implement the minimal state transition
4. Add follow-up tests for edge cases (limits, phase boundaries, retries)

## Where to Look in Code

| Concern | Location |
|---------|----------|
| Reducer (state + event → state) | `ralph/pipeline/reducer.py` |
| Orchestrator (state → effect) | `ralph/pipeline/orchestrator.py` |
| Pipeline state | `ralph/pipeline/state.py` |
| Events | `ralph/pipeline/events/` |
| Effects | `ralph/pipeline/effects/` |
| Runner / event loop | `ralph/pipeline/runner.py` |
| Routing helpers | `ralph/pipeline/handoffs.py` |
| Recovery controller | `ralph/recovery/controller.py` |
| Policy models | `ralph/policy/models/` |
| Policy validation | `ralph/policy/validation/` |

## See Also

- `pipeline-lifecycle.md` — end-to-end phase lifecycle
- [`../legacy-rust/architecture/checkpoint-and-resume.md`](../legacy-rust/architecture/checkpoint-and-resume.md) — checkpoint semantics and resume flow (legacy Rust-era reference)
- [`../legacy-rust/architecture/git-and-rebase.md`](../legacy-rust/architecture/git-and-rebase.md) — git operations and baseline diff tracking (legacy Rust-era reference)

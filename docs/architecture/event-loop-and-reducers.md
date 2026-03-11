# Event Loop and Reducer Architecture

This document describes Ralph's pipeline event loop and reducer architecture: how `PipelineState`, `PipelineEvent`, and `Effect` work together, and the best practices that keep reducers pure and effects isolated.

If you are looking for the end-to-end lifecycle (Planning -> Development -> result verification -> Commit -> Review/Fix loops), see `pipeline-lifecycle.md`.
If you are looking specifically for effect-handler layering and filesystem rules (`AppEffect` vs pipeline `Effect`, `Workspace` requirements, `std::fs` exceptions), see `effect-system.md`.
If you are looking for per-run logging, event loop observability, and log directory structure, see `logging-and-observability.md`.
If you are looking for a codebase-level module map and entrypoints, see `codebase-tour.md`.

## Core Contract

Ralph's pipeline is driven by an explicit event loop with a strict separation of concerns:

- `PipelineState`: immutable snapshot of "where we are" (this is the pipeline's app-state).
- `Effect`: an intention to perform I/O (run an agent, write a checkpoint, run git, etc.).
- `PipelineEvent`: a fact about something that happened (success, failure, data produced). In code this is an umbrella enum that wraps category events.
- `Reducer`: pure function that applies an event to the current state.
- `Orchestrator`: pure function that derives the next effect from the current state.
- `EffectHandler`: impure executor that performs the effect and reports the outcome as `EffectResult` (primary reducer event + optional additional events + optional UI events).

The pipeline must make progress by cycling through these steps:

```
state --orchestrate--> effect --handle--> event --reduce--> next_state
```

### Why this is non-negotiable

- Predictable execution: the same state + the same sequence of events produces the same result.
- Testability: reducers and orchestrators test without filesystem, network, or git.
- Debuggability: the event log explains what happened without reverse-engineering control flow.
- Resume/checkpoint: state is the checkpoint; resume is just load state + continue.

## The Event Loop

At a high level (pipeline layer):

1. Orchestrate: inspect the current `PipelineState` and choose the next `Effect`.
2. Handle: execute the effect in the `EffectHandler` (I/O happens here).
3. Emit: return an `EffectResult` (primary `PipelineEvent`, optional `additional_events`, optional `ui_events`).
4. Reduce: compute the next state by applying the primary event and then any additional events, in order.
5. Repeat until the state reaches a terminal condition (`PipelineState::is_complete()`).

Two rules prevent subtle bugs:

- The event loop must not "fake" events for certain effects (no synthetic substitutions).
- The handler is responsible for the side effect itself; the reducer is responsible for decisions.

This includes checkpointing: `Effect::SaveCheckpoint` must still execute through the handler even when persistence is disabled. (If persistence is disabled, the handler can skip writing files, but the effect still runs.)

## PipelineState (The App State)

`PipelineState` is the canonical application state for the pipeline reducer architecture.

- It is the single source of truth for pipeline progress.
- It is the checkpoint payload: serialize to JSON to resume later.
- It is reducer-owned: state transitions only happen by applying reducer events.

### What belongs in state

State should include the minimum information needed to:

- deterministically derive the next `Effect`
- explain why the pipeline is in its current phase
- safely resume after interruption

In practice this means a lot of "single-task sequencing" fields (for example: "prompt prepared for iteration N", "xml extracted for pass P", "validated outcome stored", etc.). These flags keep orchestration deterministic and prevent handlers from bundling policy decisions into I/O.

### What must not be state

- mutable caches of external reality (filesystem, git status, network)
- hidden control flags that are not driven by events
- anything that would make `reduce(state, event)` depend on time, environment, or I/O

### Prompt replay history is reducer-owned state

`PipelineState` includes `prompt_history: HashMap<String, PromptHistoryEntry>` which stores generated prompts keyed by a typed `PromptScopeKey` Display string. This is canonical state — it is serialized in checkpoints and participates in recovery semantics.

- Prompts are written to `prompt_history` via `PromptInputEvent::PromptCaptured { key, content, content_id }` reducer events (not by direct mutation from handlers).
- All prompt-preparation handlers use `get_stored_or_generate_prompt()` to replay from history or generate fresh, then emit `PromptCaptured` if a fresh prompt was generated.
- `UIEvent::PromptReplayHit { key, was_replayed }` is emitted alongside every prompt generation for observability; it does not affect state.
- Level-3 and level-4 recovery clear `prompt_history` atomically with the `recovery_epoch` increment so stale prompts cannot survive an epoch boundary.
- All prompt keys are constructed via typed `PromptScopeKey` constructors (never ad-hoc `format!()` strings), with phase-specific constructors enforcing required identity dimensions at compile time.

## Terminal State Semantics

The event loop terminates based on `PipelineState::is_complete()`, not on orchestration returning "no effect".

Current terminal semantics (see `ralph-workflow/src/reducer/state/pipeline.rs`):

- `PipelinePhase::Complete` is always terminal.
- `PipelinePhase::Interrupted` is terminal when either:
  - at least one checkpoint has been saved (`checkpoint_saved_count > 0`), or
  - we transitioned from `PipelinePhase::AwaitingDevFix` (completion marker already emitted).

The `AwaitingDevFix -> Interrupted` path is intentionally considered terminal even before the checkpoint write, because external orchestration observes termination via the completion marker. The event loop still ensures `Effect::SaveCheckpoint` runs next for persistence.

## Event and Effect Shapes (Current)

Ralph uses a few structural patterns that are important when you add new behavior.

### PipelineEvent is category-based

`PipelineEvent` wraps category-specific enums so the reducer can do type-safe routing:

- `LifecycleEvent` (frozen)
- `PlanningEvent`
- `DevelopmentEvent`
- `ReviewEvent`
- `CommitEvent`
- `AgentEvent`
- `RebaseEvent`
- `PromptInputEvent`
- `AwaitingDevFixEvent`

Category routing keeps each reducer module exhaustively matched within its own domain.

### UIEvent is separate from PipelineEvent

Effect handlers may emit UI-only events for rendering/logging. These do not affect state, do not go into checkpoints, and must not be required for correctness.

### Effects are intentionally "single-task"

Pipeline effects are granular (prepare prompt, invoke agent, extract XML, validate XML, write markdown, archive, apply outcome, etc.).

An effect should do one type of I/O and then report an outcome event. Avoid effects that mix multiple responsibilities (for example: invoke agent + parse output + transition phase).

## Non-Terminating Failure Handling: Escalating Recovery Hierarchy

The pipeline is designed to keep running and route internal failures through an escalating recovery hierarchy instead of exiting early. This ensures true non-terminating operation for unattended pipelines.

### Recovery Flow

When terminal internal failures occur:

1. **Transition to AwaitingDevFix**: Pipeline phase transitions to `PipelinePhase::AwaitingDevFix`
2. **Dev-fix invocation**: Orchestration derives `TriggerDevFixFlow` effect to diagnose and fix the issue
3. **Recovery attempt**: After dev-fix completes, the reducer determines the appropriate recovery level based on attempt count
4. **Escalating resets**: If recovery fails repeatedly, the system escalates through progressively more aggressive reset strategies
5. **No termination for internal failures**: Internal failures do not cause early termination; completion markers are reserved for explicit external/catastrophic termination paths

### Escalation Levels

The recovery hierarchy implements escalating reset strategies:

- **Level 1 - Retry same operation** (attempts 1-3): Dev-fix agent runs, reset error state, retry the failed effect from the same point
- **Level 2 - Reset to phase start** (attempts 4-6): Clear phase-specific progress flags and restart the entire phase from the beginning
- **Level 3 - Reset iteration** (attempts 7-9): Decrement iteration counter and redo Planning → Development → Commit sequence; increments `recovery_epoch` and clears `prompt_history` atomically to prevent stale prompt replay after scope rotation
- **Level 4 - Reset everything** (attempts 10+): Reset to iteration 0 and start completely fresh from the beginning (and keep trying); increments `recovery_epoch` and clears `prompt_history` atomically for the same reason

### Recovery State Tracking

`PipelineState` includes fields to track recovery progress:

- `dev_fix_attempt_count: u32` - Number of recovery attempts for the current failure
- `recovery_escalation_level: u32` - Current recovery strategy (0-4)
- `failed_phase_for_recovery: Option<PipelinePhase>` - Snapshot of the phase where failure occurred
- `recovery_epoch: u32` - Epoch counter incremented on level-3/4 recovery; carried in `PromptScopeKey` for audit and future isolation

These fields enable deterministic escalation decisions and are preserved in checkpoints to maintain recovery context across resumption.

### Recovery Events

The `AwaitingDevFixEvent` category includes events for recovery progression:

- `RecoveryAttempted { level, attempt_count, target_phase }` - Recovery initiated at a specific escalation level
- `RecoveryEscalated { from_level, to_level, reason }` - Recovery escalated to more aggressive strategy
- `RecoverySucceeded { level, total_attempts }` - Recovery succeeded, clear recovery state and resume normal operation

`target_phase` is carried from the recovery effect parameters so the reducer does not need to
trust `failed_phase_for_recovery`, which can be stale or overwritten by subsequent errors while
the pipeline is in the recovery loop.

### Why This Architecture

This escalating recovery design ensures:

- **Unattended operation**: Pipeline never gives up early, always tries progressively more aggressive recovery
- **Safety valve**: The event loop's max-iteration cap prevents tight infinite loops even if the environment never converges
- **Minimal disruption**: Start with least disruptive recovery (retry) before escalating to more expensive resets
- **Deterministic behavior**: Recovery decisions are pure functions of attempt count and escalation level
- **Observable progress**: Recovery events provide visibility into escalation decisions

## Timeout Handling

Timeouts are classified into two sub-types based on whether the agent produced meaningful output:

- **NoOutput**: Agent produced no meaningful content (empty, whitespace-only, or fewer than ~10 non-whitespace characters). Triggers immediate agent switch without consuming same-agent retry budget.
- **PartialOutput**: Agent produced meaningful partial content before being cut off. Retries the same agent with context preservation (session reuse for session-capable agents, context file extraction for session-less agents).

Context preservation for session-less agents uses the `WriteTimeoutContext` effect, which extracts the logfile content and writes it to a temp file before the retry prompt is prepared. This ensures no context is lost regardless of agent capabilities.

## Orchestration: Priority Order

Orchestration is a pure function from state to the next effect (`determine_next_effect(&PipelineState) -> Effect`). It intentionally encodes a priority order so that recovery/cleanup always preempts phase work.

The current priority ordering is documented in code (see `ralph-workflow/src/reducer/orchestration/xsd_retry.rs`) and includes, roughly:

1. Continuation context cleanup
2. Timeout context write pending (session-less agent retry context extraction)
3. Same-agent retry pending (transient invocation failures)
4. XSD retry pending (invalid XML output)
5. Continuation pending (valid output but incomplete work)
6. Rebase in progress
7. Agent-chain exhaustion / backoff waiting
8. Phase-specific effects (the normal single-task sequence)

Do not implement hidden retries or fallback loops inside handlers; retries/fallback must be reducer-visible state so the orchestrator can remain pure and deterministic.

## Handler Error Recovery (Downcasting)

Handlers normally return `Ok(EffectResult { .. })`. When a handler needs to surface a failure that should still be handled by the state machine, it returns `Err(ErrorEvent::... .into())`.

The event loop attempts to downcast typed `ErrorEvent` values out of `anyhow::Error` and re-emit them as a reducer event (`PipelineEvent::PromptInput(PromptInputEvent::HandlerError { .. })`). This keeps recovery logic in reducers without forcing new top-level `PipelineEvent` variants.

Handler panics are also treated as internal failures: the event loop catches them and routes through the same non-terminating failure flow.

## Debuggability: Event Loop Trace

The event loop keeps a bounded in-memory ring buffer of recent (effect, event, phase, retry counters) entries. When the loop encounters an internal failure or hits its iteration cap, it writes a trace to:

- `.agent/tmp/event_loop_trace.jsonl`

This is designed to make "stuck" pipelines diagnosable without reconstructing control flow.

## Where This Lives in Code

The exact file layout can evolve, but conceptually Ralph keeps these concerns separate:

- Event loop driver: `ralph-workflow/src/app/event_loop.rs`
- Orchestration (state -> next effect): `ralph-workflow/src/reducer/orchestration/`
- Reduction (state + event -> next state): `ralph-workflow/src/reducer/state_reduction/`
- Effects (intent enum) and handler trait: `ralph-workflow/src/reducer/effect*`
- Effect handler implementations (I/O): `ralph-workflow/src/reducer/handler/`

## See Also

- `README.md` (topic index)
- `agents-and-prompts.md` (agent registry, prompt generation, provider selection)
- `checkpoint-and-resume.md` (checkpoint semantics and resume flow)
- `git-and-rebase.md` (libgit2 operations and baseline diff tracking)

## Best Practices: Events vs Decisions

### Never add decision-events to lifecycle

`LifecycleEvent` is intentionally frozen so effect handlers cannot introduce new "control" events.

If you need to represent a new observation or failure, add it to the appropriate phase/category event and let the reducer decide what to do.

### Events must be descriptive facts

An emitted event should answer: "what happened" (and with what observable data), not "what should we do next".

Good events are:

- Past-tense, factual, and specific
- Stable over time (their meaning does not depend on hidden context)
- Carry the data needed for future decisions

Bad events are:

- Imperative or policy-shaped ("advance", "retry", "should")
- Encoding decisions that belong in the reducer

### Decisions belong in reducers (pure functions)

Reducers should encode the decision logic that turns facts into state transitions:

- retry vs fallback vs abort
- phase transitions
- iteration bookkeeping
- counters/limits
- which "next step" is enabled by state

The orchestrator then translates the resulting state into the next effect.

### Concrete naming guidance

Prefer events shaped like:

- `SomethingStarted { .. }`
- `SomethingSucceeded { .. }`
- `SomethingFailed { reason, .. }`
- `SomethingDetected { .. }`
- `SomethingCompleted { .. }`

Avoid events shaped like:

- `TryNextX`
- `ShouldRetry`
- `AdvanceToNextX`
- `DecideX`

#### Migration examples

Before (decision-shaped):

- `AdvanceToNextAgent`
- `RetryCommitGeneration`
- `SkipCheckpointWrite`

After (fact-shaped + reducer decision):

- `AgentEvent::InvocationFailed { role, agent, error_kind, retriable, .. }`
- `CommitEvent::MessageValidationFailed { reason, attempt }`
- `CheckpointPersisted` / `CheckpointWriteSkipped { reason }` (when you need observability)

In the "after" model:

- The handler reports the outcome (including classification data like `retriable`).
- The reducer updates state (advance chain, increment attempt, transition phase).
- The orchestrator sees the updated state and chooses the next effect.

## Best Practices: Reducers

Reducers must be deterministic and side-effect free.

- No filesystem, git, network, environment, time, randomness, or logging
- No mutation of shared global state
- No hidden coupling to config: decisions should be driven by values already present in `PipelineState` or carried in events

### Agent-chain architecture must keep chain definitions, drains, and modes separate

For agent execution architecture, Ralph should treat these as distinct concepts:

- **chain definition**: reusable configured ordered list of concrete agents
- **drain**: runtime consumer attached to a chain definition (for example planning, development, review, fix, commit, analysis)
- **drain mode**: retry/continuation sub-state inside a drain (for example continuation, same-agent retry, XSD retry)

This separation is required for reducer clarity and consistency.

Rules:

- Runtime state should not collapse reusable chain definition and active consumer into the same concept.
- Retry modes that continue the same logical work and session, such as XSD retry, should remain drain-local mode rather than becoming separate drains.
- Reducer state and pipeline events must stay concrete; they must not carry unresolved config aliases or config-only indirection.
- Effect handlers should consume already-resolved concrete drain-to-chain mappings, not re-derive chain semantics from scattered defaults.

When changing agent architecture, apply this model consistently across config, runtime state, effects, handlers, and documentation. Do not introduce a new abstraction in one layer while leaving the other layers role-shaped and ambiguous.

### Prefer typed error-events over `Err` when recoverable

When a failure should be handled by the state machine, represent it as a typed reducer event (often via `ErrorEvent` or a category event like `PlanningEvent::OutputValidationFailed`).

Reserve returning `Err` for truly unrecoverable failures. The event loop has a recovery path where certain handler errors are downcast back into typed error events and reduced.

### Reducer-friendly event design

If the reducer needs to decide something, the event should include the inputs to that decision.

Example: if fallback policy depends on whether an agent failure is retriable, the event should carry `retriable: bool` (or a structured `error_kind`) rather than forcing the reducer to re-derive it.

### TDD for reducers

When adding or changing reducer behavior:

1. Write a unit test for `reduce(state, event) -> new_state` capturing the decision.
2. Run the test and confirm it fails for the right reason.
3. Implement the minimal state transition in the reducer.
4. Add follow-up tests for edge cases (limits, phase boundaries, retries).

## Best Practices: Effects and Handlers

Effects are intentions; handlers are execution.

- Effects should be named as verbs/intents: `RunRebase`, `CreateCommit`, `SaveCheckpoint`, `InvokeAgent`.
- Handlers should not contain high-level pipeline policy (like "how many times to retry").
- Handlers should translate outcomes into events, not mutate pipeline state directly.

When a handler must implement a local safety rule (for example, "checkpointing disabled so do not write files"), it should still execute through the handler and return an event; the event loop must not bypass the effect.

## Migration Guide: From Decision-Actions to Descriptive Events

If you see control flow that looks like "if X then emit ActionY", it is often a sign the event model is too decision-shaped.

Recommended migration approach:

1. Identify the decision in the handler/orchestrator (retry, fallback, phase change).
2. Replace any decision-shaped event with a fact-shaped outcome event.
3. Move the decision into the reducer by updating `PipelineState` fields (attempt counters, chain position, phase).
4. Ensure the orchestrator derives the next effect only from state.
5. Add reducer unit tests that cover the policy explicitly.

## Loop Detection and Mandatory Recovery

The pipeline includes a loop detection mechanism to prevent infinite tight loops, particularly for XSD retry scenarios where the system cannot converge due to external mismatches (e.g., workspace vs CWD path issues).

### Loop Detection

The orchestrator tracks effect execution patterns in `ContinuationState`:
- `last_effect_kind`: fingerprint of the last executed effect
- `consecutive_same_effect_count`: counter for repeated identical effects
- `max_consecutive_same_effect`: threshold before triggering recovery (default: 20)

The effect fingerprint includes: phase, role, iteration, pass, and XSD retry state.

### Mandatory Recovery

When `consecutive_same_effect_count` exceeds the threshold and the phase is not `Complete` or `Interrupted`, the orchestrator emits `Effect::TriggerLoopRecovery`.

The loop recovery handler:
1. Resets XSD retry state (`xsd_retry_pending = false`, `xsd_retry_count = 0`)
2. Clears agent session ID to force fresh invocation
3. Resets loop detection counters
4. Emits `LoopRecoveryTriggered` event

After recovery, the orchestrator derives the next effect from the cleaned state, allowing the pipeline to resume with a fresh attempt.

### Why This Is Required

Without loop detection, the orchestrator's priority system (e.g., `xsd_retry_pending` always winning) can keep the system stuck in the same effect indefinitely. Loop recovery provides a deterministic escape path that preserves checkpoint/resume safety: the same pre-recovery state will always trigger recovery at the same threshold.

## See Also

- `effect-system.md`

## Historical Notes

The RFCs in `docs/RFC/` are kept for historical interest only. Do not treat them as canonical.

- `../RFC/RFC-004-reducer-based-pipeline-architecture.md` (historical design)
- `../RFC/RFC-005-event-loop-savecheckpoint-bypass.md` (historical incident writeup)



# RFC-005: Event Loop Must Not Bypass SaveCheckpoint Effects

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


**RFC Number**: RFC-005
**Title**: Event Loop Must Not Bypass SaveCheckpoint Effects
**Status**: Implemented
**Author**: OpenCode
**Created**: 2026-01-30
**Completed**: 2026-01-30

> NOTE: RFCs are historical documents. This RFC may not reflect current behavior.
> For up-to-date architecture docs, see `../architecture/event-loop-and-reducers.md` and `../architecture/effect-system.md`.

---

## Abstract

Ralph's reducer-based event loop previously short-circuited `Effect::SaveCheckpoint` into a
synthetic `PipelineEvent::CheckpointSaved` when checkpointing was disabled. This violated the
reducer/effect separation and could cause tight infinite loops at phase boundaries.

This RFC documents the failure mode, the root cause, and the implemented fix: always execute
`Effect::SaveCheckpoint` through the effect handler, even when persistence is disabled.

---

## Motivation

### Symptom

When running with checkpointing disabled, the pipeline could print:

- "Event loop reached max iterations (1000) without completion"
- "Pipeline exited without completion marker"

...after only a few seconds, often right after entering a phase boundary.

### Why This Matters

The reducer architecture is intentionally Redux/Elm-like:

- Reducers are pure: `reduce(state, event) -> state`
- Side effects are isolated: `effect -> handler.execute(effect) -> event (+ additional events)`

If the event loop bypasses the handler for a particular effect, it is equivalent to skipping
middleware/sagas in Redux for a subset of dispatched actions. That breaks the contract that
"effects are executed" and makes progress dependent on incidental state changes.

---

## Root Cause

In `ralph-workflow/src/app/event_loop.rs`, the event loop had special-case logic:

- When checkpoint persistence was disabled, and the derived effect was `Effect::SaveCheckpoint`,
  it would apply `PipelineEvent::CheckpointSaved` directly via the reducer and skip calling the
  handler.

At phase boundaries, orchestration derives `Effect::SaveCheckpoint` repeatedly until the state
machine advances.

When the handler is bypassed, no handler-level progress can happen, including:

- emitting any additional reducer events required to advance the state machine past the boundary
- performing any side effects that other code assumes occurred when the checkpoint effect runs

The result is a tight loop: derive `SaveCheckpoint` -> reduce `CheckpointSaved` -> derive
`SaveCheckpoint` again.

---

## Implemented Changes

1. Removed the event-loop bypass for `Effect::SaveCheckpoint`.
2. Kept the handler responsible for deciding whether to write checkpoint files based on config.
3. Added regression tests to prevent reintroducing the bypass and to ensure phase boundary
   progress does not rely on checkpoint persistence.

---

## Success Criteria

- With checkpointing disabled, the pipeline does not spin at phase boundaries.
- The event loop always executes effects via the handler (no synthetic event substitutions).
- Regression tests fail if `Effect::SaveCheckpoint` is bypassed.

---

## Risks & Mitigations

### Risk: "Checkpointing disabled" still executes a checkpoint effect

Mitigation: the handler can still skip writing any files. The effect is about the transition and
its associated bookkeeping; persistence is an implementation detail.

### Risk: Hidden coupling between phase transitions and checkpoint effects

Mitigation: document the coupling (this RFC) and keep tests that enforce progress even with
checkpoint persistence disabled.

---

## Alternatives Considered

1. Keep the bypass but make reducers advance phases on `CheckpointSaved`.
   - Rejected: this makes the event loop's behavior depend on a config flag and continues to
     violate the "effects run through the handler" contract.

2. Introduce an explicit `PhaseTransitionCompleted` event and make checkpoint persistence an
   observer of that event.
   - Not implemented here: this is a larger refactor, but is the most "pure Redux" direction.
     It would decouple progress from checkpoint concerns entirely.

---

## References

- Code fix: `ralph-workflow/src/app/event_loop.rs`
- Regression tests: `ralph-workflow/src/app/event_loop.rs`, `ralph-workflow/src/reducer/handler.rs`
- Related background: `docs/RFC/RFC-004-reducer-based-pipeline-architecture.md`

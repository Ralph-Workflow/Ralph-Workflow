# RFC-007: Post-Mortem - Stale Prompt Replay Across Iterations

**RFC Number**: RFC-007
**Title**: Post-Mortem - Stale Prompt Replay Across Iterations
**Status**: Implemented
**Author**: Mistlight
**Created**: 2026-03-08

---

## Abstract

A long-running bug caused later commit cycles to reuse stale context from earlier cycles, producing commit outputs that included already-committed changes. Multiple fixes targeted reducer-visible state freshness but did not address the actual replay path. The final fix worked by making commit prompt replay keys cycle-unique, and this incident revealed broader architectural debt around prompt identity, replay ownership, and cross-phase reset semantics.

---

## Incident Summary

### User-visible symptom

In multi-iteration runs, a later commit could include changes that should have belonged only to an earlier committed iteration.

### Impact

- High debugging churn and repeated "fix" commits.
- Reduced confidence in test coverage despite passing suites.
- Delayed delivery while root cause remained active.

### Resolution status

The immediate commit-phase bug was fixed by `65fc3991` (iteration-scoped commit prompt keys), with additional test hardening and mock-path cleanup in follow-up commits.

---

## Timeline

- `9776c48f`: reset `commit_diff_prepared` and related commit diff state after commit outcomes.
- `a95ebada`: added HEAD-based diff regression test.
- `6f0009fc` and `ca8d2f39`: cleared `prompt_inputs.commit` after commit outcomes and expanded tests.
- Production behavior still failed in realistic multi-iteration flow.
- `65fc3991`: root-cause fix - include iteration in commit prompt replay keys.
- `60f6bc1b`, `f3465451`, `596ecd8f`, `9ba6a57b`: cleanup/refactor around mock/materialization pathways.

---

## Root Cause

### Proximate functional cause

Commit prompt replay used non-cycle-unique keys. Attempt numbers reset to `1` on new commit cycles, so prompt history could replay an earlier cycle's prompt text under the same key.

Relevant path:

- Replay function: `ralph-workflow/src/prompts/prompt_dispatch.rs`
- Historical (pre-RFC-007): run-scoped prompt storage lived outside the reducer in `PhaseContext` (`ralph-workflow/src/phases/context.rs`).
- Current: reducer-owned prompt storage is `PipelineState.prompt_history` (`ralph-workflow/src/reducer/state/pipeline/core_state.rs`).
- Commit prompt keying (fixed): `ralph-workflow/src/reducer/handler/commit/prompts.rs`

### Why state-reset fixes failed

Earlier fixes correctly reset reducer fields (`commit_diff_prepared`, `commit_diff_empty`, `prompt_inputs.commit`) but stale context source lived in `prompt_history` replay identity, not only in reducer-owned commit input state.

---

## Architecture Findings (Cross-Cutting)

This incident is not only a commit bug. It exposed a broader architecture mismatch.

### 1) Split-brain state between reducer and prompt replay

Historical issue: the reducer architecture defines `PipelineState` as canonical application state, but prompt replay state (`prompt_history`) lived in `PhaseContext` outside reducer control.

Current state: `prompt_history` is reducer-owned (`PipelineState.prompt_history`) and updated only via `PromptInputEvent::PromptCaptured` so replay eligibility is tied to reducer state transitions.

Consequence: reducer-level recovery/reset can be correct while replay still reintroduces stale text.

### 2) Ad-hoc string keys as distributed identity system

Prompt identity is encoded via local `format!` strings in multiple handlers (`planning_{iteration}`, `development_{iteration}`, `review_{pass}`, `fix_{pass}`, and retry variants).

Consequence: identity policy is duplicated, informal, and easy to collide under re-entry/reset semantics.

### 3) Recovery asymmetry

Escalation-level resets (phase start / iteration rewind / full reset) clear reducer flags and materialized prompt inputs, but replay cache semantics are not intrinsically tied to those resets.

Consequence: old prompt text may remain addressable unless keys happen to encode equivalent reset dimensions.

### 4) Dual execution surfaces increase debugging entropy

Both reducer-driven and legacy phase-runner pathways contain prompt-generation/replay logic with different constraints and keying behavior.

Consequence: more places to drift, harder incident triage, weaker global invariants.

---

## External Pattern Review (What Industry Guidance Adds)

We reviewed external Rust and architecture guidance to test whether this incident is a local bug or a known class of design failure. The external guidance supports our architecture diagnosis and adds specific implementation patterns.

### A) Rust API Guidelines: static distinctions over ambiguous primitives

Rust API Guidelines explicitly recommend newtypes/custom types over ambiguous primitives and stringly-typed arguments (C-NEWTYPE, C-CUSTOM-TYPE), and static enforcement of invariants where practical (C-VALIDATE).

Applied to this incident:

- Prompt keys should not be plain `String` values assembled in multiple handlers.
- Use typed identity objects (`PromptScopeKey`) with required fields enforced at construction.
- Keep unchecked/raw key paths confined to a narrow internal module if needed for migration.

### B) Parse, don't validate: enforce invariants at the boundary once

Type-driven design guidance ("parse, don't validate") argues for converting unstructured data into refined types as early as possible, so downstream code cannot accidentally bypass invariants.

Applied to this incident:

- Parse raw phase/iteration/pass/attempt/retry metadata into one canonical replay-scope type before prompt generation.
- Remove downstream ad-hoc key synthesis; handlers consume typed scope rather than rebuilding identity logic.
- This converts replay safety from "remember to format the right key" into a compile-time API constraint.

### C) Event sourcing lessons: replay requires canonical identity + deterministic boundaries

Event sourcing literature emphasizes that replay correctness depends on canonical event identity and strict control of what can be replayed. If replay side data is outside the canonical state model, replay correctness becomes accidental.

Applied to this incident:

- Treat prompt replay metadata as part of deterministic state semantics, not incidental context cache.
- Introduce explicit epoch/generation semantics so recovery resets and replay identity move together.
- Keep replay side effects observable (hit/miss events and key dimensions) for audit and incident debugging.

### D) Serde/versioned-state guidance: encode evolution explicitly

Serde guidance around tagged representations and strict field handling (`deny_unknown_fields`, tagged enums) reinforces explicit versioning when serialized state evolves.

Applied to this incident:

- If replay metadata moves into checkpointed/reducer-owned structures, include explicit version/tag fields.
- Add backward-compat decoding policy for old checkpoints to prevent silent behavior drift on resume.

### E) Redux/Elm patterns we are partially applying, but not fully

Redux and Elm guidance maps directly to this incident class.

1. **Single store / single source of truth (Redux)**
   - Redux guidance favors one canonical store and reducer ownership of state shape.
   - Gap: replay-critical `prompt_history` is outside reducer-owned state, creating split-brain semantics.

2. **Reducers should own state shape (Redux)**
   - Redux explicitly warns against reducers giving up ownership to caller-shaped payloads.
   - Gap analogue: replay identity is assembled ad-hoc in handlers (`format!`), so state-shape constraints are effectively delegated to call sites.

3. **Treat reducers as state machines (Redux)**
   - Current reducer already models phases/passes, but replay eligibility is not modeled as a first-class transition guard.
   - Missing: explicit replay-state transitions keyed by `epoch` + scope identity, so invalid replay transitions are unrepresentable.

4. **Model actions as events, avoid multi-step transactional drift (Redux)**
   - Redux recommends event-style actions and avoiding sequential dispatches for one conceptual transaction.
   - Gap analogue: our orchestration sometimes requires multiple granular events to establish "fresh prompt context"; intermediate states can still permit stale replay.
   - Improvement: add a single domain event that atomically advances "context refreshed + replay scope rotated".

5. **Effects separated from pure update loop (Elm Architecture)**
   - Elm keeps `update` pure and pushes effects through `Cmd`/runtime.
   - We largely follow this pattern, but replay cache semantics currently straddle runtime context and reducer semantics.
   - Improvement: keep side effects external, but move replay *identity/lifecycle* decisions into pure state transitions.

---

## Why This Took So Long

1. Investigations focused on reducer-visible freshness indicators, while failure source was replay cache identity.
2. Early tests validated internals (flags/effects/transitions) more than end-to-end consumed prompt content.
3. Mock integrations could prove staged diff handling without proving replay key uniqueness under realistic cycle re-entry.
4. Concurrent branch churn and unrelated refactors increased signal-to-noise during debugging.

---

## Corrective Actions

### Immediate (done)

- Commit prompt keys now include iteration dimension to prevent cross-cycle collisions.
- Added behavior-first regression tests for multi-iteration freshness and size invariants.

### Short term (done)

1. Introduced typed `PromptScopeKey` (`ralph-workflow/src/prompts/prompt_scope_key.rs`) replacing all
   hand-rolled `format!()` key strings in handlers (planning, development, commit, review, fix phases).
2. Added `recovery_epoch` field to `PipelineState` and `PipelineCheckpoint` (incremented on level-3/4
   recovery); carried in `PromptScopeKey` for auditing and future isolation.
3. Added `UIEvent::PromptReplayHit { key, was_replayed }` and rendering in all prompt-preparation
   handlers; cloud progress handler ignores it (informational only).
4. Compile-time phase-specific constructors on `PromptScopeKey` (`for_planning`, `for_development`,
   `for_commit`, `for_review`, `for_fix`) enforce required identity dimensions at call sites.
5. `Display` output of `PromptScopeKey` is byte-identical to the old `format!()` strings, preserving
   checkpoint backward-compatibility for existing `prompt_history` maps.

### Medium term (done)

1. `prompt_history` moved into reducer-owned `PipelineState` (`HashMap<String, PromptHistoryEntry>`);
   checkpoint handler uses `state.prompt_history` rather than `ctx.clone_prompt_history()`. The
   final completion checkpoint uses `loop_result.final_state.prompt_history` for the same reason.
2. `get_stored_or_generate_prompt` validates `content_id` when both stored and current are `Some`;
   mismatch is treated as a cache miss and a fresh prompt is generated. `PromptHistoryEntry` carries
   an optional SHA-256 hex digest of materialized inputs so replay/miss is auditable.
3. Type migration complete: all prompt-history maps changed from `HashMap<String, String>` to
   `HashMap<String, PromptHistoryEntry>` across both reducer and legacy pipeline paths. Full
   event-capture migration complete: all paths (including rebase conflict resolution via
   `app/rebase/conflicts.rs`) use `PromptScopeKey` for key generation and
   `get_stored_or_generate_prompt` for replay. No remaining ad-hoc `format!()` prompt keys.
4. `PromptHistoryEntry` uses backward-compatible serde (bare string → v0; object with `content_id`
   → v1). `PipelineCheckpoint` carries `replay_metadata_version` for explicit migration signaling.
   The custom `Deserialize` impl handles old checkpoints transparently.
5. `PromptInputEvent::PromptCaptured { key, content, content_id }` added; all reducer-path prompt
   handlers (planning, development, commit, review, fix) now emit this event instead of calling
   `ctx.capture_prompt()`. The reducer handles the event and writes to `state.prompt_history`
   atomically. Level-3 and level-4 recovery in `awaiting_dev_fix.rs` now clears `prompt_history`
   alongside the `recovery_epoch` increment so stale replay candidates cannot survive an epoch
   boundary.

---

## Success Criteria

- No cross-cycle prompt replay in multi-iteration commit flows.
- Equivalent freshness guarantees demonstrated for planning/development/review/fix re-entry paths.
- Replay behavior is observable, auditable, and tied to explicit scoped identity.
- Recovery level changes cannot silently preserve stale replay candidates.

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Scope creep from architecture refactor | Phase rollout: typed keys first, state ownership second |
| False confidence from unit-only tests | Require behavior-first integration invariants |
| Regressions during replay contract changes | Add compatibility tests for checkpoint/resume + replay |
| Continued dual-path drift | Document one authoritative pathway and deprecate duplicate logic |

---

## Alternatives Considered

1. Keep ad-hoc keys and patch each phase incrementally.
   - Rejected: high chance of repeated collisions in future paths.
2. Disable replay broadly.
   - Rejected: harms deterministic resume behavior and loses valuable invariants.
3. Commit-only fix (current state forever).
   - Rejected: leaves same architectural debt in other phases.

---

## References

- `PROMPT.md` (bug context and failed-attempt history)
- `docs/architecture/event-loop-and-reducers.md`
- `docs/architecture/effect-system.md`
- `ralph-workflow/src/prompts/prompt_dispatch.rs`
- `ralph-workflow/src/phases/context.rs`
- `ralph-workflow/src/reducer/handler/commit/prompts.rs`
- `ralph-workflow/src/reducer/state/pipeline/helpers.rs`
- `ralph-workflow/src/reducer/state_reduction/awaiting_dev_fix.rs`
- `ralph-workflow/src/prompts/prompt_history_entry.rs`
- `ralph-workflow/src/reducer/event/prompt_input.rs`
- Rust API Guidelines (Type Safety, Dependability, Predictability): `https://rust-lang.github.io/api-guidelines/`
- Rust API Guidelines (Type Safety chapter): `https://rust-lang.github.io/api-guidelines/type-safety.html`
- Rust API Guidelines (Dependability chapter): `https://rust-lang.github.io/api-guidelines/dependability.html`
- Rust API Guidelines (Predictability chapter): `https://rust-lang.github.io/api-guidelines/predictability.html`
- Parse, don't validate (Alexis King): `https://lexi-lambda.github.io/blog/2019/11/05/parse-don-t-validate/`
- Event Sourcing (Martin Fowler): `https://martinfowler.com/eaaDev/EventSourcing.html`
- Serde enum representations and attributes: `https://serde.rs/enum-representations` and `https://serde.rs/attributes`
- Redux Style Guide: `https://redux.js.org/style-guide/`
- Redux reducers as state machines: `https://redux.js.org/style-guide/#treat-reducers-as-state-machines`
- Redux reducer prerequisites (purity/immutability/replay): `https://redux.js.org/usage/structuring-reducers/prerequisite-concepts`
- Redux normalizing state shape: `https://redux.js.org/usage/structuring-reducers/normalizing-state-shape`
- Elm Architecture overview: `https://guide.elm-lang.org/architecture/`
- Elm commands and subscriptions (effects boundary): `https://guide.elm-lang.org/effects/`

---

## Open Questions

1. ~~Should replay cache be reducer-owned state, or remain external but strictly reducer-versioned?~~
   **Resolved**: replay cache (`prompt_history`) is now reducer-owned state in `PipelineState`.
2. ~~Which identity dimensions are mandatory globally (`phase`, `iteration`, `pass`, `attempt`, `retry_mode`, `recovery_epoch`, `content_id`)?~~
   **Resolved**: `PromptScopeKey` encodes all dimensions except `content_id`. Content-id is
   computed from materialized prompt inputs and stored in `PromptHistoryEntry`. Mandatory
   dimensions per phase are enforced at compile time by the phase-specific constructors:
   Planning and Development require `iteration`; Commit requires `iteration` + `attempt`;
   Review and Fix require `pass`; all phases carry `recovery_epoch` for auditing. No ad-hoc
   `format!()` keys remain.
3. ~~What is the deprecation plan for duplicate legacy prompt execution pathways?~~
   **Resolved**: Full migration complete. `for_conflict_resolution` constructor added to
   `PromptScopeKey`; `app/rebase/conflicts.rs` migrated to typed keys via
   `get_stored_or_generate_prompt`. All prompt-history keys across the codebase now
   use typed `PromptScopeKey` constructors. No remaining ad-hoc `format!()` keys.

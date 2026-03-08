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
- Run-scoped prompt storage: `ralph-workflow/src/phases/context.rs`
- Commit prompt keying (fixed): `ralph-workflow/src/reducer/handler/commit/prompts.rs`

### Why state-reset fixes failed

Earlier fixes correctly reset reducer fields (`commit_diff_prepared`, `commit_diff_empty`, `prompt_inputs.commit`) but stale context source lived in `prompt_history` replay identity, not only in reducer-owned commit input state.

---

## Architecture Findings (Cross-Cutting)

This incident is not only a commit bug. It exposed a broader architecture mismatch.

### 1) Split-brain state between reducer and prompt replay

The reducer architecture defines `PipelineState` as canonical application state, but prompt replay state (`prompt_history`) lives in `PhaseContext` outside reducer control.

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

### Short term (recommended)

1. Introduce typed `PromptScopeKey` and remove hand-rolled key strings in handlers.
2. Include recovery/reset epoch in replay identity (not just iteration/pass/attempt).
3. Add replay observability: explicit event/metric for replay hit with key dimensions.
4. Add invariants that replayed prompt content must match current materialized input IDs.

### Medium term (recommended)

1. Move replay metadata ownership into reducer-owned state (or reducer-versioned cache contract).
2. Unify prompt keying/replay policy behind one API used by all phases.
3. Reduce dual-path drift between legacy phase-runner and reducer pathways.

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

---

## Open Questions

1. Should replay cache be reducer-owned state, or remain external but strictly reducer-versioned?
2. Which identity dimensions are mandatory globally (`phase`, `iteration`, `pass`, `attempt`, `retry_mode`, `recovery_epoch`, `content_id`)?
3. What is the deprecation plan for duplicate legacy prompt execution pathways?

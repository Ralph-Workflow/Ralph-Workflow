# RFC-010: Post-Mortem - Agent Success Ordering Regression Across Phases

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


**RFC Number**: RFC-010
**Title**: Post-Mortem - Agent Success Ordering Regression Across Phases
**Status**: Implemented
**Author**: Mistlight
**Created**: 2026-03-24

---

## Abstract

Ralph regressed to waiting for idle timeout even after agents had already exited and produced usable outputs. The root cause was an event-ordering gap: orchestration relied on phase-specific `*_agent_invoked_*` markers that can lag `AgentEvent::InvocationSucceeded`. Development and fix phases had an effective-invocation guard, but planning, review, and commit did not. This RFC documents the incident, why it was architectural, and the cross-phase remediation.

---

## Incident Summary

### User-visible symptom

- Agent process stops responding or exits.
- XML/summary output is already present.
- Pipeline does not advance to extraction/validation/apply.
- Workflow waits for timeout and may emit kill/retry behavior instead of progressing.

### Scope

- Reproducible in planning, review, and commit orchestration.
- Development and fix already had a fallback guard for success ordering and were not the primary regression surface.

### Impact

- Unnecessary timeout waits and kill-path activity.
- Incorrect fallback behavior risk (re-invocation instead of phase progression).
- User-visible loss of confidence in unattended orchestration.

---

## Timeline and Regression Point

The development/fix orchestration paths were previously hardened with an effective-invocation pattern that checks both explicit invocation markers and recent `Invoke*Agent` effect context. A later architecture change left planning/review/commit on strict marker checks only. This created inconsistent semantics across phases.

When agent lifecycle behavior was tightened by recent kill infrastructure work, the timing window became easier to hit: `InvocationSucceeded` could be processed while the phase-specific marker had not yet been reduced, and strict checks interpreted the phase as "not yet invoked".

---

## Root Cause (Architectural)

The pipeline currently models invocation completion via two related but asynchronously observed signals:

1. Generic agent success event (`AgentEvent::InvocationSucceeded`)
2. Phase-specific invocation marker (`planning_agent_invoked_iteration`, `review_agent_invoked_pass`, `commit_agent_invoked`, etc.)

Some orchestration paths treated (2) as the only source of truth. Because reducer/event-loop ordering allows a moment where (1) is true but (2) is not yet set, those phases could derive `Invoke*Agent` again instead of advancing to extraction.

This is architectural because the failure is about cross-phase state-machine consistency under valid event ordering, not a single parser or boundary bug.

---

## What Changed

### Orchestration fixes

Applied effective-invocation guards to the missing phases:

- `ralph-workflow/src/reducer/orchestration/phase_effects/planning.rs`
- `ralph-workflow/src/reducer/orchestration/phase_effects/review.rs`
- `ralph-workflow/src/reducer/orchestration/phase_effects/commit.rs`

Each guard now accepts either:

- explicit `*_agent_invoked_*` marker, or
- recent matching `last_effect_kind == Invoke*Agent` plus required pre-invocation state and expected drain/mode.

### Regression test coverage

Added cross-phase behavioral regression coverage:

- `tests/integration_tests/behavioral_pipeline_tests.rs`
  - `test_successful_invocation_is_treated_as_complete_across_planning_review_and_commit`

This test reproduces the ordering window and asserts progression to XML extraction (not re-invocation).

### Surfaced verification issue fixed during incident response

`cargo xtask verify` exposed a flaky timeout-child-activity failure while validating this work:

- Failing test: `timeout_file_activity::active_subprocess_prevents_idle_kill`
- Root cause: race-sensitive single-sample classification for active child processes
- Fix: one-cycle grace for active descendants before classifying as idle in
  `ralph-workflow/src/pipeline/idle_timeout/runtime.rs`

This aligns idle enforcement with observed subprocess jitter while preserving stalled-child timeout behavior.

---

## Why This Was Not Caught Earlier

1. Existing cross-phase tests emphasized development/fix ordering and did not include the same ordering scenario for planning/review/commit.
2. Phase implementations diverged over time; the effective-invocation safety pattern was not uniformly enforced as a shared invariant.
3. Kill/timeout path hardening increased sensitivity to ordering windows that were previously lower-frequency.

---

## Success Criteria

- Planning/review/commit treat post-success states as completed invocation and continue to extraction without waiting for timeout.
- No re-invocation derived in the success-ordering window.
- Full verification remains green after the fix set.

---

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| False positive “effective invocation” allows advancement when invocation truly failed | Guard requires matching recent `Invoke*Agent` and phase-specific preconditions (prompt/context/cleanup + drain/mode) |
| Future phase drift reintroduces inconsistent ordering semantics | Keep cross-phase behavioral test and treat effective-invocation pattern as an architecture invariant |
| Child-activity grace hides real stalls | Grace is single-cycle only; repeated stale active snapshots still time out and are covered by unit/integration tests |

---

## References

- `docs/architecture/event-loop-and-reducers.md`
- `ralph-workflow/src/reducer/orchestration/phase_effects/development.rs`
- `ralph-workflow/src/reducer/orchestration/phase_effects/review.rs`
- `ralph-workflow/src/reducer/orchestration/phase_effects/planning.rs`
- `ralph-workflow/src/reducer/orchestration/phase_effects/commit.rs`
- `tests/integration_tests/behavioral_pipeline_tests.rs`
- `tests/integration_tests/timeout_file_activity.rs`
- `ralph-workflow/src/pipeline/idle_timeout/runtime.rs`

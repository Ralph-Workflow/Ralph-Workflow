# ADR-0010: Post-fix source-of-truth closure (residual prose-comparison fixes and defensive collector guard)

* Status: Accepted
* Date: 2026-10-17
* Deciders: Ralph Workflow Team
* Supersedes: none (additive to ADR-0009)
* Related: [ADR-0009](adr-0009-pipeline-source-of-truth-ownership.md)

## Context

[ADR-0009](adr-0009-pipeline-source-of-truth-ownership.md) established the
typed-authority model that closed the original linked-worktree
retained-integration reproduction and the broader architectural class of
drifting sources of truth. Fresh inspection on the maintained tree
confirms that every cited site already delegates to the typed authorities
(`next_integration_action`, `decide_phase_transition`,
`is_terminal_success_eligible`, `classify_git_operation`,
`fan_out_decision`, `PublicationDecision`). The architecture is in place.

Two residual prose-comparison sites remained, plus a missing defensive
guard on the operation-scoped evidence collector. This ADR records the
post-fix state, the residual fixes that close invariant 7 / 13, and the
defensive `RuntimeError` that fails closed if a future contributor
reintroduces a repository-global evidence key.

## Decision

The architecture is **not extended** by this ADR. The decision is
**closure**: the two residual prose-comparison sites are replaced with
typed enum-identity checks, the operation-scoped evidence collector
gains a fail-closed `RuntimeError` boundary, and the audit tables
returned by the PARALLEL-1 / PARALLEL-2 / PARALLEL-3 fixtures are pinned
as regression coverage. No new authority, no new enum, no new layer.

### 1. Replace residual prose-comparison at `run_loop._integration_alert_for_state`

**Before.** `ralph/pipeline/run_loop.py:609` compared the persisted
`rebase.last_decision` against the literal string
`"report_intervention_required"`. The literal is the value of
`PhaseDecision.REPORT_INTERVENTION_REQUIRED.value`, but a future change
to the enum could rename the value while leaving the literal in place,
silently disagreeing with the reducer.

**After.** The literal is replaced with
`PhaseDecision.REPORT_INTERVENTION_REQUIRED.value`. The static
`test_run_loop_no_prose_compare_on_last_decision` and behaviour
`test_integration_alert_for_state_uses_typed_phase_decision` pin the
contract. The StrEnum round-trip continues to work: a
`last_decision` value written by the reducer always equals
`PhaseDecision.REPORT_INTERVENTION_REQUIRED.value`.

### 2. Replace residual prose-substring matching at `_is_diagnosis_state` / `integration_action_to_operation_display_state`

**Before.** `ralph/display/auto_integrate_message.py:_is_diagnosis_state`
and the adjacent
`integration_action_to_operation_display_state` performed prose substring
matching (`"diagnos" in norm or "evidence" in norm`) to flip the
operator surface to the `EVIDENCE_DIAGNOSIS` label. A free-form string
that happened to contain those substrings could flip the label without
a typed authority decision. This violated invariant 13
("UI and logs describe the actual authoritative state rather than the
helper currently executing").

**After.** A new closed vocabulary
`_DIAGNOSIS_ACTION_VERBS: frozenset[str] = {"diagnosing", "evidence_diagnosis"}`
plus the typed `is_diagnosis: bool` flag are the **only** paths that
flip the projection to `OperationDisplayState.EVIDENCE_DIAGNOSIS`.
Substring matching on arbitrary prose is gone. The
`test_integration_action_display_state_uses_closed_vocabulary` fixture
pins both halves: the closed tokens still flip the label, and
prose-only inputs that previously matched by substring no longer do.

### 3. Defensive `RuntimeError` at the `SubprocessOperationEvidenceCollector.snapshot()` boundary

**Before.** The collector already emits only the closed `_KIND_*` set
and the test `test_subprocess_collector_never_emits_for_each_ref`
pins the absence of `for-each-ref` / `ls-files` in the value domain.
A future contributor could still slip a repository-global key past
the value-domain test if a new helper accidentally introduced one.

**After.** The collector runs a fail-closed `_is_closed_kind` check
over every emitted fact at the end of `snapshot()`. A forbidden kind
raises `RuntimeError(f"OperationEvidenceCollector emitted forbidden
kind {fact.kind!r}; only the closed _KIND_* set is allowed.")`
before the tuple is returned. The companion loader already refuses
records carrying `_LEGACY_GLOBAL_EVIDENCE_KEYS` (for-each-ref /
ls-files), so the same surface cannot be reintroduced on either side.
The `test_subprocess_collector_guard_rejects_forbidden_kind` and
`test_subprocess_collector_guard_accepts_only_closed_vocabulary`
fixtures pin both the helper and the boundary raise.

### 4. Audit-and-pin existing delegation

The PARALLEL-1 / PARALLEL-2 / PARALLEL-3 fixtures extend the existing
test files to pin the already-cited delegation sites:

* `ralph/pipeline/runner.py:972, :1881-1882, :1935-1936, :2516`
  delegate to `is_terminal_success_eligible`, `next_integration_action`,
  `decide_phase_transition`. Regression coverage extended in
  `tests/test_runner_decision_delegation.py`.
* `ralph/pipeline/run_loop.py:1422, :2071, :2149, :2549,
  :3138-3140, :3149` delegate to `classify_git_operation`,
  `next_integration_action`, `decide_phase_transition`,
  `is_terminal_success_eligible`. Regression coverage extended in
  `tests/test_run_loop_decision_delegation.py`,
  `tests/test_run_loop_setters_only.py`,
  `tests/test_run_loop_pro_integration.py`.
* `ralph/pipeline/auto_integrate_recovery.py:142, :777, :802,
  :897-900` delegate to `busy_refusal_outcome`,
  `next_integration_action`, `classify_git_operation`. Regression
  coverage extended in `tests/test_auto_integrate_recovery.py`,
  `tests/test_recovery_release_uses_record_store.py`.

### 5. Public-boundary matrix test extension (PARALLEL-7)

`tests/test_public_workflow_entry_point_matrix.py` is extended with
`test_entry_point_matrix_six_seams_agree` (parameterised over the
audit table). The fixture proves that for the
active-integration (BLOCK_FOR_RECOVERY) and the recovered-shallow
scenarios, **all six** public entry points — startup inspection,
phase-boundary run-loop, commit handling, cooldown recovery,
parallel join, terminal handling — produce the **same**
`PhaseDecision` value. A divergence surfaces as a single test
failure; no per-seam patched fixture is needed.

### 6. Kill-at-cap restart fixture (PARALLEL-6)

`tests/test_authority_audit_scenario_3_4_9_restart_retry.py::test_kill_at_cap_does_not_replenish_after_restart`
proves that a deliberate SIGKILL at
`merge_commit_repair_attempts == DIAGNOSTIC_REPAIR_ATTEMPT_CAP`
does not replenish the budget on restart: the first post-restart
handoff refuses, no diagnostic agent is invoked, and the
`decide_phase_transition` answer remains
`PhaseDecision.REPORT_INTERVENTION_REQUIRED` across multiple
reloads. The setter boundary
(`RebaseState.record_consecutive_conflicts`,
`RebaseState.advance_conflict_strategy`) is the only writer; the
restart-replenishment path is forbidden by invariant 11.

## Source-of-Truth Domain Ownership Matrix (post-fix)

The 8-field matrix from ADR-0009 holds; the new row is the
**display-substring** owner, which is now the closed vocabulary
`_DIAGNOSIS_ACTION_VERBS` plus the typed `is_diagnosis` flag:

| Field (post-fix delta)            | Authority                                                                              | Writer                                                | Reader                                                  | Projection                                    | Restart    | UNKNOWN                       | Blocks dispatch / terminal?                              |
|-----------------------------------|----------------------------------------------------------------------------------------|-------------------------------------------------------|---------------------------------------------------------|-----------------------------------------------|------------|-------------------------------|----------------------------------------------------------|
| `operation_display_state` (DELTA) | Closed vocab + typed `is_diagnosis` flag (was: substring heuristic)                     | `RebaseState._resolve_operation_display_state`        | `display/auto_integrate_message.py`                    | Eight-category enum                           | Re-derived | Off-vocab → no projection    | Yes — projection is read by every display surface        |
| `OperationFact.kind` (NEW)        | `SubprocessOperationEvidenceCollector.snapshot()`                                      | Same (closed `_KIND_*` set)                           | `IntegrationRecord.diagnostic_evidence`                 | Tuple of facts (boundary-checked)             | Re-snapshotted | Closed vocab only          | Yes — RuntimeError boundary fails closed                 |
| `last_decision` (DELTA)           | `ralph/pipeline/reducer.py`                                                            | Same (sole writer)                                    | `display/snapshot.py`, `_integration_alert_for_state`  | `PhaseDecision.value` (typed)                 | Re-derived | Off-vocab → reducer refuses  | Yes — comparison now via `PhaseDecision.value`          |

## Consequences

### Positive

* Invariant 7 ("A projection cannot overrule or mutate its source")
  is now mechanical: the two display-side residual projections read
  the typed value, not a string.
* Invariant 11 ("Retries and strategy progression survive restart
  and terminate in a visible state") is mechanically pinned: the
  kill-at-cap fixture is a regression test for the cap, not for
  the helper that sets it.
* Invariant 13 ("UI and logs describe the actual authoritative
  state rather than the helper currently executing") is closed:
  prose substring matching is gone from the display path.
* The defensive guard makes a future reintroduction of a
  repository-global evidence key fail closed at the collector
  boundary, not at the loader's `RecordUnreadableError`.

### Negative

* The `_DIAGNOSIS_ACTION_VERBS` vocabulary is duplicated against
  `_ACTION_TO_DISPLAY_STATE` and the `OperationDisplayState` enum.
  The duplication is intentional: each table is a closed
  vocabulary pinned by its own test, and the projection layer
  re-derives the label from the closed enum rather than from a
  shared mutable dict. The cost of the duplication is one
  five-line constant; the alternative is a single mutable dict
  that future contributors could break without a test failing.
* The defensive `RuntimeError` may surface in tests that
  monkey-patch the collector's helpers to inject a forbidden
  kind. The companion test
  `test_subprocess_collector_guard_rejects_forbidden_kind`
  documents the contract; future tests that want to bypass the
  guard must add a closed kind to `_CLOSED_OPERATION_FACT_KINDS`.

### Neutral

* `IntegrationAction`, `PhaseDecision`, `RebaseOutcomeKind`,
  `OperationDisplayState`, `FanOutDecision`, `PublicationDecision`
  are unchanged. The closed enums do not grow.
* `make verify` continues to enforce the 60-second combined test
  budget. The new fixtures add <2s to the per-suite timing on
  the maintained tree.

## Migration / Compatibility

* `last_decision` continues to persist as a `str | None` field
  carrying the typed `PhaseDecision.value`. Legacy checkpoints
  with the existing string value load unchanged; the
  `_populate_operation_display_state` validator already coerces
  the string into the typed enum.
* `IntegrationDisplayLabel` and `OperationDisplayState` enums are
  unchanged; the `_ACTION_TO_DISPLAY_STATE` map keeps the same
  keys. A new closed-vocab path (`_DIAGNOSIS_ACTION_VERBS`) is
  added next to it; existing call sites that pass the typed
  `is_diagnosis=True` flag continue to work.
* The defensive guard only raises on a **forbidden** kind. The
  current `_KIND_*` set is unchanged; the closed vocabulary is a
  strict superset of `_KIND_*`.

## Verification

| Required outcome | Concrete edits                                                       | Runnable proof                                                                              |
|------------------|----------------------------------------------------------------------|---------------------------------------------------------------------------------------------|
| (1) Prose-compare at run_loop:609 closed | `run_loop.py:609` (1 line)                                          | `uv run pytest tests/test_run_loop_decision_delegation.py -q`                                |
| (2) Prose-substring at display closed    | `auto_integrate_message.py:_DIAGNOSIS_ACTION_VERBS` + lookup        | `uv run pytest tests/test_display_lifecycle_label_taxonomy.py -q`                            |
| (3) Defensive guard on collector         | `operation_evidence.py:_is_closed_kind` + boundary raise            | `uv run pytest tests/test_operation_evidence.py -q`                                          |
| (4) Audit-and-pin runner / run_loop / recovery | `tests/test_runner_decision_delegation.py`, `test_run_loop_*` | `uv run pytest tests/test_runner_decision_delegation.py tests/test_run_loop_*.py tests/test_auto_integrate_recovery.py tests/test_recovery_release_uses_record_store.py -q` |
| (5) Public-boundary matrix extension     | `tests/test_public_workflow_entry_point_matrix.py:test_entry_point_matrix_six_seams_agree` | `uv run pytest tests/test_public_workflow_entry_point_matrix.py -q`                          |
| (6) Kill-at-cap restart fixture          | `tests/test_authority_audit_scenario_3_4_9_restart_retry.py:test_kill_at_cap_does_not_replenish_after_restart` | `uv run pytest tests/test_authority_audit_scenario_3_4_9_restart_retry.py -q`                |
| (7) Full gate                            | none                                                                 | `cd ralph-workflow && make verify` (combined 60s budget, see `docs/agents/verification.md`)  |

## References

* [ADR-0006: Owned landing recovery](adr-0006-owned-landing-recovery.md)
* [ADR-0007: Integration source of truth](adr-0007-integration-source-of-truth.md)
* [ADR-0008: Integration lifecycle consolidation](adr-0008-integration-lifecycle-consolidation.md)
* [ADR-0009: Pipeline source-of-truth ownership, observation boundaries, and durable state authorities](adr-0009-pipeline-source-of-truth-ownership.md)
* Canonical verification policy: `docs/ralph-workflow-policy/verification-policy.md`
* Operator surface: `docs/sphinx/operator-states.md`
* Recovery surface: `docs/sphinx/recovery.md`

# ADR-0010: Source-of-truth closure (typed display projection and defensive collector guard)

* Related: [ADR-0009](adr-0009-pipeline-source-of-truth-ownership.md)

## Architecture

Display projections use typed enum-identity checks. The operation-scoped
evidence collector enforces a fail-closed `RuntimeError` boundary, and the
delegation fixtures pin the authority call sites.

### 1. Typed comparison at `run_loop.integration_alert_for_state`

`ralph/pipeline/run_loop.py:685` compares the persisted
`rebase.last_decision` against `PhaseDecision.REPORT_INTERVENTION_REQUIRED.value`,
the typed enum value rather than a bare string literal, so the comparison
cannot disagree with the reducer. The static
`test_run_loop_no_prose_compare_on_last_decision` and behaviour
`test_integration_alert_for_state_uses_typed_phase_decision` pin the
contract. The StrEnum round-trip works: a
`last_decision` value written by the reducer always equals
`PhaseDecision.REPORT_INTERVENTION_REQUIRED.value`.

### 2. Closed vocabulary at `_is_diagnosis_state` / `integration_action_to_operation_display_state`

`ralph/display/auto_integrate_message.py:_is_diagnosis_state`
and the adjacent
`integration_action_to_operation_display_state` use the closed vocabulary
`_DIAGNOSIS_ACTION_VERBS: frozenset[str] = {"diagnosing", "evidence_diagnosis"}`
plus the typed `is_diagnosis: bool` flag, and those are the **only** paths that
flip the projection to `OperationDisplayState.EVIDENCE_DIAGNOSIS`.
Substring matching on arbitrary prose is absent from the display path, which
satisfies invariant 13
("UI and logs describe the actual authoritative state rather than the
helper currently executing"). The
`test_integration_action_display_state_uses_closed_vocabulary` fixture
pins both halves: the closed tokens still flip the label, and
prose-only inputs do not.

### 3. Defensive `RuntimeError` at the `SubprocessOperationEvidenceCollector.snapshot()` boundary

The collector emits only the closed `_KIND_*` set, and the test
`test_subprocess_collector_never_emits_for_each_ref`
pins the absence of `for-each-ref` / `ls-files` in the value domain.
`snapshot()` runs a fail-closed `_is_closed_kind` check
over every emitted fact at the end of the call, so a repository-global key
cannot enter the value domain through a new helper. A forbidden kind
raises `RuntimeError(f"OperationEvidenceCollector emitted forbidden
kind {fact.kind!r}; only the closed _KIND_* set is allowed.")`
before the tuple is returned. The companion loader already refuses
records carrying `_LEGACY_GLOBAL_EVIDENCE_KEYS` (for-each-ref /
ls-files), so the same surface cannot be reintroduced on either side.
The `test_subprocess_collector_guard_rejects_forbidden_kind` and
`test_subprocess_collector_guard_accepts_only_closed_vocabulary`
fixtures pin both the helper and the boundary raise.

### 4. Delegation sites

The delegation fixtures pin these call sites:

* `ralph/pipeline/runner.py:913` and `:2382` delegate to
  `is_terminal_success_eligible`; `runner.py:1679` and `:1734` delegate to
  `decide_seam_action` (the integration owner seam); `runner.py:1686` and
  `:1741` delegate to `decide_phase_transition`. Regression coverage is in
  `tests/test_runner_decision_delegation.py`.
* `ralph/pipeline/run_loop.py:1518` and `:2489` delegate to
  `classify_git_operation`; `run_loop.py:2648` delegates to
  `decide_seam_action` (the integration owner seam); `run_loop.py:3873`
  delegates to `decide_phase_transition`; `run_loop.py:3377`, `:3397`, and
  `:3883` delegate to `is_terminal_success_eligible`. Regression coverage is in
  `tests/test_run_loop_decision_delegation.py`,
  `tests/test_run_loop_setters_only.py`,
  `tests/test_run_loop_pro_integration.py`.
* `ralph/pipeline/auto_integrate_recovery.py:92-97` delegates to
  `busy_refusal_outcome`; `auto_integrate_recovery.py:112` delegates to
  `classify_git_operation`; `auto_integrate_recovery.py:303` delegates to
  `decide_seam_action` (the integration owner seam). Regression coverage is in
  `tests/test_auto_integrate_recovery.py`,
  `tests/test_recovery_release_uses_record_store.py`.

### 5. Public-boundary matrix test

`tests/test_public_workflow_entry_point_matrix.py` contains
`test_entry_point_matrix_six_seams_agree` (parameterised over the
audit table). The fixture proves that for the
active-integration (BLOCK_FOR_RECOVERY) and the recovered-shallow
scenarios, **all six** public entry points — startup inspection,
phase-boundary run-loop, commit handling, cooldown recovery,
parallel join, terminal handling — produce the **same**
`PhaseDecision` value. A divergence surfaces as a single test
failure; no per-seam patched fixture is needed.

### 6. Kill-at-cap restart fixture

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

## Source-of-Truth Domain Ownership Matrix

The 8-field matrix from ADR-0009 holds. The rows below name the three fields
whose authority this document pins; the **display-substring** owner is the
closed vocabulary `_DIAGNOSIS_ACTION_VERBS` plus the typed `is_diagnosis` flag:

| Field                              | Authority                                                                              | Writer                                                | Reader                                                  | Projection                                    | Restart    | UNKNOWN                       | Blocks dispatch / terminal?                              |
|-----------------------------------|----------------------------------------------------------------------------------------|-------------------------------------------------------|---------------------------------------------------------|-----------------------------------------------|------------|-------------------------------|----------------------------------------------------------|
| `operation_display_state`          | Closed vocab + typed `is_diagnosis` flag                                             | `RebaseState._resolve_operation_display_state`        | `display/auto_integrate_message.py`                    | Eight-category enum                           | Re-derived | Off-vocab → no projection    | Yes — projection is read by every display surface        |
| `OperationFact.kind`               | `SubprocessOperationEvidenceCollector.snapshot()`                                      | Same (closed `_KIND_*` set)                           | `IntegrationRecord.diagnostic_evidence`                 | Tuple of facts (boundary-checked)             | Re-snapshotted | Closed vocab only          | Yes — RuntimeError boundary fails closed                 |
| `last_decision`                    | `ralph/pipeline/reducer.py`                                                            | Same (sole writer)                                    | `display/snapshot.py`, `_integration_alert_for_state`  | `PhaseDecision.value` (typed)                 | Re-derived | Off-vocab → reducer refuses  | Yes — comparison via `PhaseDecision.value`               |

## Compatibility

* `last_decision` persists as a `str | None` field
  carrying the typed `PhaseDecision.value`. Checkpoints written with
  the plain string value load unchanged; the
  `_populate_operation_display_state` validator coerces
  the string into the typed enum.
* The `IntegrationDisplayLabel` and `OperationDisplayState` enums keep
  their members and the `_ACTION_TO_DISPLAY_STATE` map keeps its keys.
  The closed-vocab path (`_DIAGNOSIS_ACTION_VERBS`) sits next to it, and
  call sites that pass the typed `is_diagnosis=True` flag work with it.
* The defensive guard raises only on a **forbidden** kind. The
  `_KIND_*` set is a strict subset of the closed vocabulary.

## Verification

| Required outcome | Implementation                                                        | Runnable proof                                                                              |
|------------------|-----------------------------------------------------------------------|---------------------------------------------------------------------------------------------|
| (1) No prose compare at run_loop:685 | `run_loop.py:685` (1 line)                                          | `uv run pytest tests/test_run_loop_decision_delegation.py -q`                                |
| (2) No prose substring at the display seam | `auto_integrate_message.py:_DIAGNOSIS_ACTION_VERBS` + lookup        | `uv run pytest tests/test_display_lifecycle_label_taxonomy.py -q`                            |
| (3) Defensive guard on collector         | `operation_evidence.py:_is_closed_kind` + boundary raise            | `uv run pytest tests/test_operation_evidence.py -q`                                          |
| (4) Audit-and-pin runner / run_loop / recovery | `tests/test_runner_decision_delegation.py`, `test_run_loop_*` | `uv run pytest tests/test_runner_decision_delegation.py tests/test_run_loop_*.py tests/test_auto_integrate_recovery.py tests/test_recovery_release_uses_record_store.py -q` |
| (5) Public-boundary matrix              | `tests/test_public_workflow_entry_point_matrix.py:test_entry_point_matrix_six_seams_agree` | `uv run pytest tests/test_public_workflow_entry_point_matrix.py -q`                          |
| (6) Kill-at-cap restart fixture          | `tests/test_authority_audit_scenario_3_4_9_restart_retry.py:test_kill_at_cap_does_not_replenish_after_restart` | `uv run pytest tests/test_authority_audit_scenario_3_4_9_restart_retry.py -q`                |
| (7) Full gate                            | none                                                                 | `cd ralph-workflow && make verify` (combined 60s budget, see `docs/agents/verification.md`)  |

## References

* [ADR-0006: Owned landing recovery](adr-0006-owned-landing-recovery.md)
* [ADR-0007: Integration source of truth](adr-0007-integration-source-of-truth.md)
* [ADR-0008: Integration lifecycle consolidation](adr-0008-integration-lifecycle-consolidation.md)
* [ADR-0009: Pipeline source-of-truth ownership, observation boundaries, and durable state authorities](adr-0009-pipeline-source-of-truth-ownership.md)
* Canonical verification policy: `docs/ralph-workflow-policy/verification-policy.md`
* Operator surface: `ralph-workflow/docs/sphinx/operator-states.md`
* Recovery surface: `ralph-workflow/docs/sphinx/recovery.md`

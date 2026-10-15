# ADR-0008: Integration lifecycle consolidation: typed IntegrationAction, RebaseOutcomeKind, and the operation-scoped object-reachability check

* Status: Accepted
* Date: 2026-10-15
* Deciders: Ralph Workflow Team
* Consulted: ADR-0004, ADR-0005, ADR-0006, ADR-0007
* Extended by: [ADR-0009](adr-0009-pipeline-source-of-truth-ownership.md)

## Context

[ADR-0007](adr-0007-integration-source-of-truth.md) established the single-source-of-truth
architecture for Ralph Workflow, introducing operation-scoped evidence collection via
`OperationEvidenceCollector`, explicit state transitions on `IntegrationRecord`, and the
`OperationDisplayState` taxonomy. However, ADR-0007 explicitly deferred several areas in
its "Out of Scope" clause:

1. **Full reducer consolidation**: `RebaseState` retained legacy compatibility fields
   (`last_action`, `last_reason`) alongside `operation_display_state` without a single
   typed outcome authority.
2. **Conflict-resolution typing**: Round progression in `ralph/pipeline/conflict_resolution/driver.py`
   relied on unstructured boolean checks and prose inspection (`if evidence`, `if evidence is None`).
3. **Deterministic completion proof & non-agent diagnostic repair**: Diagnostic remediation
   continued to invoke diagnostic agents without verifying whether the underlying Git operation
   had already completed cleanly or whether marker objects (`MERGE_HEAD`, `REBASE_HEAD`) were
   reachable in the object store.

As a result, several drift sites remained where code compared prose strings such as
`record.phase == "integrating"` / `"integrated"` or `rebase.last_action == "conflict"`, or wrote
untyped outcome strings (e.g. `"remote_failed"`). Furthermore, when marker SHAs were invalid or
unreachable, diagnostic routines could enter repeated agent rotation loops or risk destructive
metadata mutations.

## Decision

This ADR supersedes the "Out of Scope" clause of ADR-0007 by consolidating the integration
lifecycle under typed authorities, single-writer state invariants, fail-closed reachability
verification, and a four-condition positive operation-completion proof.

### 1. Typed `IntegrationAction` authority

We introduce `ralph.pipeline._integration_action.IntegrationAction`, a closed enumeration of
8 discrete actions governing the entire integration lifecycle:

| Value | Name | Description |
|---|---|---|
| `resolve_active_operation` | `RESOLVE_ACTIVE_OPERATION` | Active merge or rebase currently uncompleted in worktree (`resolving_rebase` or `resolving_merge`). |
| `continue_git_operation` | `CONTINUE_GIT_OPERATION` | Resolved Git operation awaiting continuation or merge commit (`rebase_continue_pending` or `merge_commit_pending`). |
| `reintegrate_against_target` | `REINTEGRATE_AGAINST_TARGET` | Target branch moved or reintegration pending (`reintegrate_pending` or phase `integrating`). |
| `land_target` | `LAND_TARGET` | Integration complete (`phase="integrated"` or resolved rebase/merge); local target fast-forward pending. |
| `publish_after_landing` | `PUBLISH_AFTER_LANDING` | Local landing completed; configured remote push in flight or deferred. |
| `wait_for_bounded_retry` | `WAIT_FOR_BOUNDED_RETRY` | Conflict backoff timer active or retry delay required before next attempt. |
| `require_intervention` | `REQUIRE_INTERVENTION` | Diagnostic repair attempts exhausted, resolution exhausted, or unresolvable failure requiring human operator. |
| `complete_with_proof` | `COMPLETE_WITH_PROOF` | Integration lifecycle verified and terminal; no pending obligations. |

The function `ralph.pipeline._integration_action.next_integration_action(record, policy=None, clock=None) -> IntegrationAction`
is the sole authority for deciding what action the pipeline must take for an integration obligation.
All prose checks (`phase == "integrating"`, `phase == "integrated"`, `last_action == ...`) across
the run loop, reducer, orchestrator, effect router, and recovery boundaries are eliminated and replaced
by queries against `next_integration_action`.

### 2. Typed `RebaseOutcomeKind` and single-writer `RebaseState.last_outcome`

We introduce `ralph.pipeline._integration_action.RebaseOutcomeKind`, an enumeration of 19 closed outcome
verbs representing the lossless union of all integration outcome categories across the pipeline:

- Core rebase/merge outcomes: `REBASED`, `MERGED`, `FAST_FORWARDED`, `SKIPPED`, `CONFLICT`, `RECOVERED`
- Diagnostic and intervention outcomes: `DIAGNOSING`, `EVIDENCE_DIAGNOSIS`, `INTERVENTION_REQUIRED`, `DIAGNOSTIC_INTERVENTION_REQUIRED`
- Execution and lifecycle states: `INTEGRATING`, `LANDING`, `PUBLISHING`, `CONFLICT_RESOLUTION`, `WAITING`
- Remote publication outcomes: `REMOTE_PUSHED`, `REMOTE_FAILED`
- Terminal outcomes: `TERMINAL`, `COMPLETED`

`RebaseState.last_outcome: RebaseOutcomeKind | None` is the single authoritative writer field on `RebaseState`.
To guarantee backward compatibility with existing checkpoints and external callers:
- `RebaseState.last_action` is a read-only `@property` projection returning `self.last_outcome.value` (or `None`).
- `RebaseState.from_legacy_payload(payload: dict) -> RebaseState` provides a one-way migration constructor:
  it parses `last_action`, maps it via `RebaseOutcomeKind.from_legacy(action)`, logs a structured
  `loguru.warning("MIGRATED_LEGACY: last_action {} -> last_outcome {}", ...)`, and constructs the model.
  Direct modification of `IntegrationRecord.with_transition` for `last_outcome` is prohibited; the
  migrator lives exclusively on `RebaseState`.
- `last_outcome_reason(record) -> str | None` provides typed access to human-readable outcome reasons,
  reading `last_reason` or `repair_cap_reason` without ad-hoc string inspections.

### 3. Operation-scoped object reachability and the four-condition completion proof

To eliminate unnecessary diagnostic agent invocations and prevent corruption when marker files point
to unreachable objects, `ralph.pipeline.operation_evidence` and `_integration_diagnostic` enforce
a fail-closed pre-pass before any diagnostic agent is invoked:

#### Object reachability check
The evidence collector captures worktree-scoped reachability facts:
- `_KIND_MERGE_OBJECT_RESOLVABLE`: checks if the SHA in `MERGE_HEAD` exists in the local Git object database (`git cat-file -e <sha>`).
- `_KIND_REBASE_OBJECT_RESOLVABLE`: checks if the SHA in `REBASE_HEAD` exists in the local Git object database.

If either marker is present but its object is unresolvable (`False`), `_run_completion_pre_pass` immediately
halts forward execution and routes to `RAISE_DIAGNOSTIC_INTERVENTION` with `diagnostic_intervention_required=True`.
It does **not** consume a repair attempt (`merge_commit_repair_attempts` is unchanged) and does **not** delete
or mutate `MERGE_HEAD` or `REBASE_HEAD`.

#### Four-condition positive operation-completion proof
When marker objects are resolvable and `next_integration_action(record)` returns
`IntegrationAction.CONTINUE_GIT_OPERATION` or `IntegrationAction.LAND_TARGET`,
Ralph evaluates whether the Git operation has in fact completed cleanly,
satisfying all four conditions:

1. **Commit ancestry proof**: HEAD matches `record.integrated_feature_sha` or `record.pre_target_sha`,
   and parents of HEAD match expected parent SHAs (`pre_target_sha` or `merge_commit_parent`).
2. **Operation ownership proof**: `record.owning_worktree` matches the inspected root path, and
   `record.diagnostic_ownership` matches `ownership_fingerprint(record)`.
3. **Clean index and tree proof**: `MERGE_HEAD` and `REBASE_HEAD` are absent from the worktree, no
   unmerged paths exist in the index (`unmerged_paths()` is empty), and working tree diff against HEAD
   is clean (`git diff --quiet HEAD` returns 0).
4. **Durable atomic transition proof**: `record.phase == "integrated"` (or phase is `"integrating"` with
   a valid `integrated_feature_sha`) and `record.diagnostic_evidence` is empty.

**Pre-pass execution rules**:
- If all four conditions pass: Ralph verifies positive completion, transitions the record via
  `RELEASE_DIAGNOSTIC_EVIDENCE`, clears the diagnostic lock, and skips agent rotation entirely.
- If any condition fails: Ralph immediately transitions the record to `RAISE_DIAGNOSTIC_INTERVENTION`
  without consuming a repair attempt, preserving all marker files and metadata for operator review.
- Under no circumstances does the pre-pass or diagnostic recovery delete or mutate `MERGE_HEAD`,
  `REBASE_HEAD`, `ORIG_HEAD`, the Git index, or worktree files. The diagnostic path is strictly read-only
  on Git operation metadata.

### 4. Typed conflict-resolution action and attempt fault classification

We type the conflict-resolution driver and fault classification:
- `ralph.pipeline.conflict_resolution.driver.ResolutionAction`: enum with values
  `CONTINUE_AGENT_ROUND`, `ESCALATE_TO_INTERVENTION`, `ACCEPT_WORKTREE_REPAIR`, `PROCEED_TO_FAST_FORWARD`.
- `select_resolution_action(session, round_index, cap) -> ResolutionAction`: single authority for round
  decision making.
- `ralph.pipeline.conflict_resolution.attempt_fault.AttemptFault`: enum with values
  `AGENT_HEALTHY`, `AGENT_EXITED`, `AGENT_INFRASTRUCTURE_FAILURE`, `AGENT_TOOL_DEAD`, `UNKNOWN`.
- `classify_attempt_fault(evidence) -> AttemptFault`: typed classifier replacing unstructured `if evidence` checks.

## Consequences

- **Single authority for progression**: `next_integration_action` prevents divergent lifecycle decisions
  across the six public entry points (`inspect_integration_resolution`, `_run_inner_loop`, commit handler,
  cooldown handler, parallel-join handler, terminal handler).
- **Lossless outcome projection**: `RebaseState.last_outcome` provides full typed fidelity across 19 verbs
  while maintaining 100% backward compatibility for legacy checkpoints.
- **Fail-closed diagnostic safety**: Broken or unreachable Git marker objects no longer trigger infinite
  agent loops or unsafe marker deletions.
- **Fast-path evidence release**: Completed Git operations with positive proof skip agent invocation,
  reducing latency and agent spend while maintaining strict cryptographic and structural guarantees.

## Verification

The consolidated lifecycle is verified by deterministic tests in `ralph-workflow/tests/`:
- `tests/test_integration_action.py`: Tests each `IntegrationAction` value, drift replacement, startup, and budget cases.
- `tests/test_rebase_outcome_kind.py`: Round-trip migration across all 19 legacy verbs plus `None`, and display projection.
- `tests/test_integration_record_legacy_migration.py`: Public checkpoint reload with one-way `last_action` migration.
- `tests/test_public_workflow_entry_point_matrix.py`: Verifies parity across all six public entry points.
- `tests/test_public_workflow_crash_restart_matrix.py`: Verifies idempotency across durable transition crash points.
- `tests/test_integration_diagnostic.py`: Positive completion proof releases evidence, unreachable marker triggers intervention, and unreadable index preserves metadata.
- `tests/test_resolution_action.py`: Round selection action routing.
- `tests/test_attempt_fault.py`: Attempt fault classification.

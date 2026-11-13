# ADR-0008: Integration lifecycle consolidation: typed IntegrationAction, RebaseOutcomeKind, and the operation-scoped object-reachability check

* Related: [ADR-0004](adr-0004-conflict-resolution-liveness.md), [ADR-0005](adr-0005-conflict-resolution-pipeline-parity.md), [ADR-0006](adr-0006-owned-landing-recovery.md), [ADR-0007](adr-0007-integration-source-of-truth.md)

## Architecture

The integration lifecycle runs under typed authorities, single-writer state invariants,
fail-closed reachability verification, and a four-condition positive operation-completion
proof.

### 1. Typed `IntegrationAction` authority

`ralph.pipeline._integration_action.IntegrationAction` is a closed enumeration of
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

The integration owner seam (`ralph.pipeline.integration_owner`: `decide_integration` /
`decide_integration_verdict`, with the durable attempt ledger written through
`charge_integration_attempt`, reached from the pipeline boundaries through
`ralph.pipeline._integration_owner_seam.decide_seam_action`) is the sole authority for deciding
what action the pipeline must take for an integration obligation.
All prose checks (`phase == "integrating"`, `phase == "integrated"`, `last_action == ...`) are
absent from the run loop, reducer, orchestrator, effect router, and recovery boundaries;
each of them consults typed queries against that seam.

The helper `ralph.pipeline._integration_action.next_integration_action(record, policy=None, clock=None) -> IntegrationAction`
is NOT that authority: it is the typed projection of durable state onto the closed
`IntegrationAction` vocabulary, and it fails closed to `REQUIRE_INTERVENTION` when the durable
facts cannot prove a next step.

### 2. Typed `RebaseOutcomeKind` and single-writer `RebaseState.last_outcome`

`ralph.pipeline._integration_action.RebaseOutcomeKind` is an enumeration of 19 closed outcome
verbs representing the lossless union of all integration outcome categories across the pipeline:

- Core rebase/merge outcomes: `REBASED`, `MERGED`, `FAST_FORWARDED`, `SKIPPED`, `CONFLICT`, `RECOVERED`
- Diagnostic and intervention outcomes: `DIAGNOSING`, `EVIDENCE_DIAGNOSIS`, `INTERVENTION_REQUIRED`, `DIAGNOSTIC_INTERVENTION_REQUIRED`
- Execution and lifecycle states: `INTEGRATING`, `LANDING`, `PUBLISHING`, `CONFLICT_RESOLUTION`, `WAITING`
- Remote publication outcomes: `REMOTE_PUSHED`, `REMOTE_FAILED`
- Terminal outcomes: `TERMINAL`, `COMPLETED`

`RebaseState.last_outcome: RebaseOutcomeKind | None` is the single authoritative writer field on `RebaseState`.
Checkpoint and caller compatibility use these surfaces:
- `RebaseState.last_action` is a read-only `@property` projection returning `self.last_outcome.value` (or `None`).
- `RebaseState.from_legacy_payload(payload: dict) -> RebaseState` provides a one-way migration constructor:
  it parses `last_action`, maps it via `RebaseOutcomeKind.from_legacy(action)`, logs a structured
  `loguru.warning("MIGRATED_LEGACY: last_action {} -> last_outcome {}", ...)`, and constructs the model.
  Direct modification of `IntegrationRecord.with_transition` for `last_outcome` is prohibited; the
  migrator lives exclusively on `RebaseState`.
- `last_outcome_reason(record) -> str | None` provides typed access to human-readable outcome reasons,
  reading `last_reason` or `repair_cap_reason` without ad-hoc string inspections.

### 3. Operation-scoped object reachability and the four-condition completion proof

Before any diagnostic agent is invoked,
`ralph.pipeline.operation_evidence` and `_integration_diagnostic` enforce
a fail-closed pre-pass:

#### Object reachability check
The evidence collector captures worktree-scoped reachability facts:
- `_KIND_MERGE_OBJECT_RESOLVABLE`: checks if the SHA in `MERGE_HEAD` exists in the local Git object database (`git cat-file -e <sha>`).
- `_KIND_REBASE_OBJECT_RESOLVABLE`: checks if the SHA in `REBASE_HEAD` exists in the local Git object database.

If either marker is present but its object is unresolvable (`False`), `_run_completion_pre_pass` immediately
halts forward execution and routes to `RAISE_DIAGNOSTIC_INTERVENTION` with `diagnostic_intervention_required=True`.
It does **not** consume a repair attempt (`merge_commit_repair_attempts` is unchanged) and does **not** delete
or mutate `MERGE_HEAD` or `REBASE_HEAD`.

#### Four-condition positive operation-completion proof
When marker objects are resolvable and the typed projection
`next_integration_action(record)` is `IntegrationAction.CONTINUE_GIT_OPERATION`,
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

The conflict-resolution driver and the fault classification are typed:
- `ralph.pipeline.conflict_resolution.driver.ResolutionAction`: enum with values
  `CONTINUE_AGENT_ROUND`, `ESCALATE_TO_INTERVENTION`, `ACCEPT_WORKTREE_REPAIR`, `PROCEED_TO_FAST_FORWARD`.
- `select_resolution_action(session, round_index, cap) -> ResolutionAction`: single authority for round
  choice making.
- `ralph.pipeline.conflict_resolution.attempt_fault.AttemptFault`: enum with values
  `AGENT_HEALTHY`, `AGENT_EXITED`, `AGENT_INFRASTRUCTURE_FAILURE`, `AGENT_TOOL_DEAD`, `UNKNOWN`.
- `classify_attempt_fault(evidence) -> AttemptFault`: typed classifier; the unstructured `if evidence` checks are absent.

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

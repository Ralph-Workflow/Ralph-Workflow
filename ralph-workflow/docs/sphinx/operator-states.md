# Pipeline states

This page is the **operator reference** for the closed vocabulary of
states Ralph Workflow reports while a workflow is running. When you
watch the live activity line or read a run transcript, what you see is
projected from these typed states — never from parsing prose — so what
the display says always matches what the state machine is doing.

## OperationDisplayState — the eight pipeline states

`OperationDisplayState` (defined in
`ralph.display.auto_integrate_message`, ADR-0007) is the typed
eight-category state stored on `RebaseState`. One of these is active at
any moment:

| State | Meaning |
|-------|---------|
| `INTEGRATING` | A Git rebase or merge is actively in flight. |
| `LANDING` | Integration succeeded; the local target ref is being fast-forwarded. |
| `PUBLISHING` | The local landing is complete and the result is being pushed to the configured remote (or deferred if offline). |
| `CONFLICT_RESOLUTION` | A Git conflict was detected and candidate agents are being driven to resolve the conflicted paths. |
| `EVIDENCE_DIAGNOSIS` | The diagnostic lock is held while retained integration evidence is inspected — this is deliberately distinct from conflict resolution. |
| `WAITING` | A bounded retry timer or cooldown delay is running before the next attempt. |
| `INTERVENTION_REQUIRED` | Automatic recovery is exhausted (diagnostic repair or conflict budget); a human operator must act. |
| `TERMINAL` | The operation-level final state. Maps integration outcomes such as `skipped`, `recovered`, and `remote_pushed` onto `OperationDisplayState.TERMINAL`. This is distinct from whole-pipeline terminal-success eligibility, which is governed exclusively by `ralph.pipeline._terminal_authority.is_terminal_success_eligible`. |

### Closed ownership model

The lifecycle states above are the **typed projection** of the closed
ownership model: every transition between states is the result of
exactly one authoritative owner applying a closed transition table or
calling a typed decision function. There is no fallback authority:

- The durable integration record (`ralph.pipeline.auto_integrate_record.IntegrationRecord`)
  is the single source of truth for the integration lifecycle. All
  mutations flow through
  `IntegrationRecord.with_transition(...)`; the closed
  `Transition` table is the only path that flips
  `diagnostic_intervention_required`. Ad-hoc
  `model_copy(update=...)` is forbidden.
- The recovery release sites route every `RELEASE_DIAGNOSTIC_EVIDENCE`
  through `IntegrationRecordStore.transition` (via
  `apply_record_transition`). BUSY refusals are mapped to typed retry
  outcomes; UNREADABLE refusals are mapped to intervention with
  bytes preserved on disk. The release path NEVER erases
  ownership evidence.
- Decision-field writers on `RebaseState` are exclusively the typed
  setters: `set_publication_decision` (single writer of
  `last_publication_decision`), `set_fan_out_decision` (single writer of
  `last_fan_out_decision`), and `set_operation_display_state` (single
  writer of `operation_display_state`). Grepping the codebase for these
  setters confirms there are no raw `model_copy` writers of these
  fields left after the audit. The migration covered 10
  operation-display sites and 1 fan-out site (see
  [ADR-0009](../architecture/adr-0009-pipeline-source-of-truth-ownership.md)).
- Crash recovery uses two reflog boundaries with different contracts.
  One reads the shared branch reflog as one row of a legacy rebase
  completion proof. The other, `detached_branch_name`, reads the
  worktree-private git-dir `logs/HEAD` to reconstruct the branch from
  detached-HEAD residue. Neither is diagnostic evidence. The contracts
  are in [Recovery](recovery.md#recovery-boundaries).
- The run loop, the diagnostic path, and the publication state
  machine all consult the same authority — they never infer
  progression from prose, from worktree cleanliness, or from
  record absence.

## IntegrationDisplayLabel — the seven display labels

`IntegrationDisplayLabel` (defined in
`ralph.display.integration_display_label`) is the closed vocabulary
shown to you in status output. Producer action/state strings are mapped
onto exactly one of these seven labels:

| Label | Meaning |
|-------|---------|
| `integration` | Git integration work (rebase/merge/continuation) is being performed. |
| `landing` | The integration result is being landed on the local target. |
| `publication` | The landed result is being published to the remote. |
| `conflict resolution` | Agents are resolving an active Git conflict. |
| `evidence diagnosis` | Retained evidence is being diagnosed to classify the failure. |
| `waiting` | A bounded retry or cooldown is in progress. |
| `intervention` | Operator intervention is required before the workflow can continue. |

## Where to read more

The full lifecycle — failure categorization, checkpoints, evidence
retention, and the recovery loop these states project — is documented in
[Recovery](recovery.md).

### Post-fix closure

The two residual prose-comparison sites (the `last_decision` string
compare in `run_loop._integration_alert_for_state` and the
`"diagnos" in norm or "evidence" in norm` substring heuristic in
`display/auto_integrate_message`) are closed in
[ADR-0010](../architecture/adr-0010-post-fix-source-of-truth.md).
The same ADR records the defensive `RuntimeError` boundary on
`SubprocessOperationEvidenceCollector.snapshot()` and the public
matrix-extension regression coverage. The operator surface is now
strictly typed: every label you see in the activity line comes
from a closed enum and an `is_diagnosis` flag, never from prose.

### Documentation review note

- **What changed:** The closed-ownership section names all three setters, the 10 display plus 1 fan-out migration, and the two distinct crash-recovery reflog contracts.
- **Why here:** This is the operator reference for what the activity line means and which writer owns each displayed decision.
- **What stayed elsewhere:** The domain matrix and setter signatures stay in ADR-0009. Seam locations stay on the recovery page.
- **Duplication:** This page keeps one-line state notes and points at Recovery for the reflog contracts.
- **Clearer route:** An operator can match a displayed state to its setter, then open Recovery for how a crash is reconstructed.

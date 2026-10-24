# auto_integration_and_retained_integration_recovery

## Authoritative owner
`ralph.pipeline.auto_integrate_recovery.recover_incomplete_integration`
is the single authority for the recovery boundary; the durable
`IntegrationRecord` is the single owner of the integration obligation
(`auto_integrate_recovery.py:702`).

## Writers
- `ralph.pipeline._integration_diagnostic` writes the diagnostic
  evidence and the `RAISE_DIAGNOSTIC_INTERVENTION` transition
  (`_integration_diagnostic.py:106`).
- `ralph.pipeline.auto_integrate_recovery_terminal` writes the
  cleared record after a successful recovery
  (`auto_integrate_recovery_terminal.py:29`).
- `ralph.pipeline.run_loop` and the worker runtime write the
  reconciled `RebaseState` via
  `reconcile_stale_unresolved_state`
  (`auto_integrate_resolution_state.py:49`).

## Readers
- The integration owner seam (`ralph.pipeline.integration_owner`:
  `decide_integration` / `decide_integration_verdict`, reached from the
  pipeline boundaries through
  `ralph.pipeline._integration_owner_seam.decide_seam_action`) reads the
  `IntegrationRecord` to compute the next `IntegrationAction`
  (`integration_owner.py:245`).
  `ralph.pipeline._integration_action.next_integration_action`
  (`_integration_action.py:258`) is the retired typed projection onto the
  same closed vocabulary — it fails closed to `REQUIRE_INTERVENTION` and is
  not an authority.
- `ralph.pipeline._pipeline_state_machine._check_blocking_decision`
  reads the `IntegrationRecord.diagnostic_intervention_required`
  flag and the owner seam's verdict to block dispatch
  (`_pipeline_state_machine.py:159`).

## Derived projections
- `IntegrationRecord` is the durable source; `RebaseState` and the
  `OperationDisplayState` enum are derived projections of the
  integration lifecycle outcome (`rebase_state.py`).

## Valid transitions
- `START_INTEGRATING`, `PROMOTE_TO_INTEGRATED`, `MARK_RESOLVING_REBASE`,
  `MARK_PENDING_REBASE_CONTINUE`, `MARK_RESOLVING_MERGE`,
  `MARK_PENDING_MERGE_COMMIT`, `ACCEPT_REPAIR_EDITS`, `RESUME_REPAIR`,
  `HOLD_DIAGNOSTIC_EVIDENCE`, `RELEASE_DIAGNOSTIC_EVIDENCE`,
  `BUMP_DIAGNOSTIC_ATTEMPT`, `RAISE_DIAGNOSTIC_INTERVENTION`,
  `MARK_REINTEGRATE_PENDING`, `MARK_LEGACY_FIELDS_PRESERVED`, `CLEAR`
  (`auto_integrate_transition.py:261`).

## Restart behaviour
A crash before a transition leaves the prior recoverable state; a
crash after leaves the new recoverable state. The
`IntegrationRecordStore.transition` PROMOTE_TO_INTEGRATED idempotency
short-circuit closes the recovery path on both sides
(`auto_integrate_record_store.py:75`).

## UNKNOWN representation
- `IntegrationRecord` with no `phase` or unknown phase ->
  `parse_record_payload` returns `None` and `read_record_outcome`
  returns `UNREADABLE` with reason `unsupported_schema`
  (`auto_integrate_record.py:648`).
- A diagnostic evidence verdict of `UNKNOWN` is mapped to
  `EvidenceVerdict.UNKNOWN` which the recovery boundary treats as
  "do not release" (preserves evidence for inspection).

## What blocks dispatch / terminal success
- A present `IntegrationRecord` blocks dispatch
  (`_pipeline_state_machine.py:56-99`).
- `IntegrationRecord.diagnostic_intervention_required` blocks dispatch
  and surfaces `REPORT_INTERVENTION_REQUIRED`.
- `IntegrationAction.REQUIRE_INTERVENTION` blocks dispatch.

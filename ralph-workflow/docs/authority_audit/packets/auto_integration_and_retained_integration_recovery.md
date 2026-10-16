# auto_integration_and_retained_integration_recovery

## Authoritative owner
`ralph.pipeline.auto_integrate_recovery.recover_incomplete_integration`
is the single authority for the recovery boundary; the durable
`IntegrationRecord` is the single owner of the integration obligation
(`auto_integrate_recovery.py:576-682`).

## Writers
- `ralph.pipeline._integration_diagnostic` writes the diagnostic
  evidence and the `RAISE_DIAGNOSTIC_INTERVENTION` transition
  (`_integration_diagnostic.py:_raise_intervention`).
- `ralph.pipeline.auto_integrate_recovery_terminal` writes the
  cleared record after a successful recovery
  (`auto_integrate_recovery_terminal.py:post_attempt_verify`).
- `ralph.pipeline.run_loop` writes the reconciled `last_decision`
  via `reconcile_stale_unresolved_state` for the next phase.

## Readers
- `ralph.pipeline._integration_action.next_integration_action` reads
  the `IntegrationRecord` to compute the next `IntegrationAction`
  (`_integration_action.py:next_integration_action`).
- `ralph.pipeline._pipeline_state_machine.decide_phase_transition`
  reads the `IntegrationRecord.diagnostic_intervention_required` flag
  to block dispatch (`_pipeline_state_machine.py:74-89`).

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
  (`auto_integrate_transition.py:_register_all_transitions`).

## Restart behaviour
A crash before a transition leaves the prior recoverable state; a
crash after leaves the new recoverable state. The
`IntegrationRecordStore.transition` PROMOTE_TO_INTEGRATED idempotency
short-circuit closes the recovery path on both sides
(`auto_integrate_record_store.py:IntegrationRecordStore.transition`).

## UNKNOWN representation
- `IntegrationRecord` with no `phase` or unknown phase ->
  `parse_record_payload` returns `None` and `read_record_outcome`
  returns `UNREADABLE` with reason `unsupported_schema`
  (`auto_integrate_record.py:_parse_record_payload`).
- A diagnostic evidence verdict of `UNKNOWN` is mapped to
  `EvidenceVerdict.UNKNOWN` which the recovery boundary treats as
  "do not release" (preserves evidence for inspection).

## What blocks dispatch / terminal success
- A present `IntegrationRecord` blocks dispatch
  (`_pipeline_state_machine.py:53-89`).
- `IntegrationRecord.diagnostic_intervention_required` blocks dispatch
  and surfaces `REPORT_INTERVENTION_REQUIRED`.
- `IntegrationAction.REQUIRE_INTERVENTION` blocks dispatch.

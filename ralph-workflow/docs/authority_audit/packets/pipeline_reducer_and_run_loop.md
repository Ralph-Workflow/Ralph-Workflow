# pipeline_reducer_and_run_loop

## Authoritative owner
`ralph.pipeline.run_loop.run` (the run loop), consuming
`ralph.pipeline._pipeline_state_machine.decide_phase_transition` as the
single authority for "what may the run loop do next?"
(`_pipeline_state_machine.py:118-172`).

## Writers
- `ralph.pipeline.run_loop` writes the latest `PipelineState` via
  `reconcile_stale_unresolved_state` and the recovery checkpoint
  (`run_loop.py:checkpoint reconciliation block`).
- `ralph.pipeline.handoffs.resolve_next_phase` is consulted for the
  next-phase signal but is not the progression authority; it only
  picks a candidate from the current state and a signal
  (`handoffs.py:resolve_next_phase`).

## Readers
- `ralph.pipeline._pipeline_state_machine.decide_phase_transition`
  reads the integration record + `RebaseState` to derive the typed
  `PhaseDecision` (`_pipeline_state_machine.py:118-172`).
- `ralph.display.auto_integrate_message` reads the `PhaseDecision` /
  `RebaseState` to derive the seven `IntegrationDisplayLabel` strings.

## Derived projections
- `OperationDisplayState` (8-state enum) and `IntegrationDisplayLabel`
  (7-string enum) are pure display projections of the run-loop
  authority output; they never gate dispatch or terminal success
  (`display/auto_integrate_message.py`, `display/integration_display_label.py`).

## Valid transitions
- `PhaseDecision` is a closed set: `CONTINUE_PHASE`, `ADVANCE_PHASE`,
  `BLOCK_FOR_RECOVERY`, `REPORT_TERMINAL_SUCCESS`,
  `REPORT_TERMINAL_FAILURE`, `REPORT_INTERVENTION_REQUIRED`
  (`_pipeline_state_machine.py:36-50`).

## Restart behaviour
A crash leaves the latest `PipelineState` in the durable checkpoint;
on restart, the run loop rebuilds state from the checkpoint and the
integration record before consulting `decide_phase_transition`. No
business work is replayed because the integration record is the
authoritative ownership surface.

## UNKNOWN representation
- Unknown phase or non-str phase value in the integration record ->
  `read_record` returns `None` and the recovery boundary treats the
  record as corrupt (`auto_integrate_record.py:read_record`).
- Unknown `last_outcome` in legacy checkpoint -> `RebaseState` migrates
  to `RebaseOutcomeKind.UNKNOWN` (`rebase_state.py:_migrate_legacy`).

## What blocks dispatch / terminal success
- A present `IntegrationRecord` blocks dispatch and terminal success
  via `_pipeline_state_machine._check_blocking_decision`
  (`_pipeline_state_machine.py:53-89`).
- A `rebase_state.integration_unresolved is True` or
  `rebase_state.recovery_record_retained is True` blocks dispatch.
- A non-terminal publication decision (not `PUBLICATION_TERMINAL`)
  blocks terminal success (`_terminal_authority.py:43-46`).
- A non-complete fan-out decision (not `JOIN_COMPLETE`) blocks
  terminal success (`_terminal_authority.py:48`).

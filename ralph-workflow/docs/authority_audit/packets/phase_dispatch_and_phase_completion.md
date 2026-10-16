# phase_dispatch_and_phase_completion

## Authoritative owner
`ralph.pipeline._pipeline_state_machine.decide_phase_transition` is the
single authority for what the run loop may do next
(`_pipeline_state_machine.py:118-170`).

## Writers
- `ralph.pipeline.handoffs.resolve_next_phase` returns a candidate
  phase string from the current state + signal; it does not decide
  progression itself (`handoffs.py:63`).
- `ralph.pipeline.reducer` routes the typed `last_outcome` and
  `last_decision` projections onto the durable `RebaseState`
  (`reducer.py:360-426`, `_terminal_failure_route` /
  `_terminal_success_route` at `reducer.py:561,575`).

## Readers
- `ralph.pipeline.run_loop` reads `decide_phase_transition` at every
  entry point (startup, phase-boundary, commit, cooldown, parallel-
  join, terminal) and routes the typed `PhaseDecision` accordingly
  (`run_loop.py:2121`,
  `run_loop.py:2075`).
- `ralph.pipeline._terminal_authority.is_terminal_success_eligible`
  reads the `last_outcome`, `last_publication_decision`, and
  `last_fan_out_decision` projections to gate terminal success
  (`_terminal_authority.py:26-46`).

## Derived projections
- `PhaseDecision` is the closed vocabulary the run loop reads for
  progression; it is a derived projection of the integration record
  + `RebaseState` (`_pipeline_state_machine.py:33-48`).

## Valid transitions
- `PhaseDecision` is a closed set: `CONTINUE_PHASE`, `ADVANCE_PHASE`,
  `BLOCK_FOR_RECOVERY`, `REPORT_TERMINAL_SUCCESS`,
  `REPORT_TERMINAL_FAILURE`, `REPORT_INTERVENTION_REQUIRED`
  (`_pipeline_state_machine.py:33-48`).

## Restart behaviour
On restart, the run loop reads the durable `PipelineState` checkpoint
and the integration record, then calls `decide_phase_transition` from
those two sources only. The previous turn's `last_decision` is NOT
consulted (avoiding feedback-loop regression where a settled obligation
remains blocked because the previous turn decided to block).

## UNKNOWN representation
- `rebase_state.last_outcome` may be `None` (no outcome yet) or
  `RebaseOutcomeKind.UNKNOWN` (legacy migration couldn't map a verb).
  Either way, `_check_blocking_decision` treats it as "no
  unlanded-rebase" but still blocks on `integration_record is not None`
  or `recovery_record_retained is True`.

## What blocks dispatch / terminal success
- Present `IntegrationRecord` (the integration lifecycle authority)
  blocks dispatch until cleared (`_pipeline_state_machine.py:72-84`).
- `rebase_state.is_intervention_required` blocks dispatch
  (`_pipeline_state_machine.py:86-87`).
- `rebase_state.integration_unresolved` and
  `rebase_state.recovery_record_retained` block dispatch and
  terminal success (`_pipeline_state_machine.py:93-99`).
- Non-terminal `last_publication_decision` blocks terminal success
  (`_terminal_authority.py:44`); non-complete
  `last_fan_out_decision` likewise (`_terminal_authority.py:45`).

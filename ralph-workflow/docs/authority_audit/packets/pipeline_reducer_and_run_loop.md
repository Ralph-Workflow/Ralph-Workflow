# pipeline_reducer_and_run_loop

## Authoritative owner
`ralph.pipeline.run_loop.run` (the run loop), consuming
`ralph.pipeline._pipeline_state_machine.decide_phase_transition` as the
single authority for "what may the run loop do next?"
(`_pipeline_state_machine.py:118-170`).

## Writers
- `ralph.pipeline.run_loop` writes the latest `PipelineState` via
  `reconcile_stale_unresolved_state` and the recovery checkpoint
  (`run_loop.py:845-865`, `run_loop.py:1780-1830`).
- `ralph.pipeline.handoffs.resolve_next_phase` is consulted for the
  next-phase signal but is not the progression authority; it only
  picks a candidate from the current state and a signal
  (`handoffs.py:63`).

## Readers
- `ralph.pipeline._pipeline_state_machine.decide_phase_transition`
  reads the integration record + `RebaseState` to derive the typed
  `PhaseDecision` (`_pipeline_state_machine.py:118-170`).
- `ralph.display.auto_integrate_message` reads the `PhaseDecision`
  / `RebaseState` display projection (including
  `rebase_state.py:535`); the
  `IntegrationDisplayLabel` strings are derived in
  `display/integration_display_label.py`.

## Derived projections
- `OperationDisplayState` (8-state enum) and `IntegrationDisplayLabel`
  (7-string enum) are pure display projections of the run-loop
  authority output; they never gate dispatch or terminal success
  (`display/auto_integrate_message.py`, `display/integration_display_label.py`).

## Valid transitions
- `PhaseDecision` is a closed set: `CONTINUE_PHASE`, `ADVANCE_PHASE`,
  `BLOCK_FOR_RECOVERY`, `REPORT_TERMINAL_SUCCESS`,
  `REPORT_TERMINAL_FAILURE`, `REPORT_INTERVENTION_REQUIRED`
  (`_pipeline_state_machine.py:33-48`).

## Restart behaviour
A crash leaves the latest `PipelineState` in the durable checkpoint;
on restart, the run loop rebuilds state from the checkpoint and the
integration record before consulting `decide_phase_transition`. No
business work is replayed because the integration record is the
authoritative ownership surface.

## UNKNOWN representation
- Unknown phase or non-str phase value in the integration record ->
  `read_record` returns `None` and the recovery boundary treats the
  record as corrupt (`auto_integrate_record.py:532,648`).
- Unknown `last_outcome` in legacy checkpoint -> `RebaseState`
  migrates to `RebaseOutcomeKind.UNKNOWN`
  (`rebase_state.py:94-181`).

## What blocks dispatch / terminal success
- A present `IntegrationRecord` blocks dispatch and terminal success
  via `_pipeline_state_machine._check_blocking_decision`
  (`_pipeline_state_machine.py:56-99`).
- A `rebase_state.integration_unresolved is True` or
  `rebase_state.recovery_record_retained is True` blocks dispatch.
- A non-terminal publication decision (not `PUBLICATION_TERMINAL`)
  blocks terminal success (`_terminal_authority.py:44`).
- A non-complete fan-out decision (not `JOIN_COMPLETE`) blocks
  terminal success (`_terminal_authority.py:45`).

## Typed `FailureCategory` seam (2026)

The reducer's `_enter_failed_recovery` and `_handle_agent_failure`
(`reducer.py:598, 641`) accept an optional keyword-only
`failure_category: FailureCategory | None = None` parameter and
branch on `FailureCategory.INTEGRATION` (`reducer.py:609, 619`)
instead of the prose prefix
`"integration conflict requires resolution:"`. The producer
`runner._integration_conflict_failure`
(`runner.py:1064-1074`) already passes the typed
`FailureCategory.INTEGRATION` on `PhaseFailureEvent`; the reducer
now consumes it through the typed seam. No string fallback is
permitted: when `failure_category is None` the reducer does NOT
advance the conflict-strategy ladder. The bounded-advancement
writer of the strategy block is
`RebaseState.advance_conflict_strategy(reason: str) -> Self`
(`rebase_state.py:559`).

## Planning-obstruction typed seam (2026)

The run loop's `_route_planning_commit` (`run_loop.py:1790-1805`)
reads the closed `PlanningObstructionKind` member from
`state.rebase.last_planning_obstruction` (`rebase_state.py:351`)
instead of `any(text in reason for text in (...5 strings...))`.
The closed vocabulary lives in
`ralph/pipeline/_planning_obstruction.py`; the single producer
`identify_planning_obstruction(reason)` is the only allowed
prose-substring scan on the planning-seam reason. Downstream
callers read only the typed kind.

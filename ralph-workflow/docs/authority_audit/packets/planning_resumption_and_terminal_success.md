# planning_resumption_and_terminal_success

## Authoritative owner
`ralph.pipeline._terminal_authority.is_terminal_success_eligible` is
the single authority for terminal-success eligibility
(`_terminal_authority.py:26-50`).

## Writers
- `ralph.pipeline._terminal_authority` is the only writer of the
  terminal-success decision; the run loop consults it once per
  cycle and routes the result to the typed
  `PhaseDecision.REPORT_TERMINAL_SUCCESS` or
  `PhaseDecision.REPORT_TERMINAL_FAILURE` outcomes.
- `ralph.pipeline._pipeline_state_machine.decide_phase_transition`
  writes the typed `PhaseDecision` to the durable `last_decision`
  projection on `RebaseState`
  (`_pipeline_state_machine.py:118-172`).

## Readers
- `ralph.pipeline.run_loop` reads `is_terminal_success_eligible` at
  the terminal entry point and routes the verdict to the run-loop
  boundary.
- `ralph.display.auto_integrate_message` reads the
  `last_publication_decision` and `last_fan_out_decision` for the
  operator display.

## Derived projections
- `last_publication_decision` (typed `PublicationDecision`) and
  `last_fan_out_decision` (typed `FanOutDecision`) are the typed
  projections of the publication and fan-out authorities.

## Valid transitions
- Terminal-success is an outcome, not a transition; the
  `is_terminal_success_eligible` function is the single
  authoritative gate.

## Restart behaviour
On restart, the run loop re-reads the integration record, the
`RebaseState`, and the publication/fan-out decisions; the terminal
authority re-derives eligibility from those sources. The previous
turn's `last_decision` is NOT consulted (avoiding feedback-loop
regression where a settled obligation remains blocked because the
previous turn decided to block).

## UNKNOWN representation
- `last_publication_decision is None` or
  `last_fan_out_decision is None` means the corresponding authority
  has not yet decided; the terminal authority treats
  `None` as "not yet proven terminal" and blocks terminal success.

## What blocks dispatch / terminal success
- `integration_record is not None` blocks terminal success
  (`_terminal_authority.py:40`).
- `rebase_state.integration_unresolved is True` or
  `rebase_state.recovery_record_retained is True` blocks
  terminal success.
- `rebase_state.is_intervention_required` blocks terminal
  success.
- A non-terminal `last_publication_decision` (anything other than
  `PUBLICATION_TERMINAL`) blocks terminal success.
- A non-complete `last_fan_out_decision` (anything other than
  `JOIN_COMPLETE`) blocks terminal success.

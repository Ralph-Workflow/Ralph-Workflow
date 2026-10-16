# retry_controllers_counters_budgets_and_strategy_selection

## Authoritative owner
`ralph.pipeline._integration_action._conflict_action` is the single
authority for conflict-retry decisions and the bounded-retry deadline
check (`_integration_action.py:111-141`); for diagnostic-repair
attempt caps, the authority is
`ralph.pipeline._integration_diagnostic._bump_attempt`
(`_integration_diagnostic.py:_bump_attempt`).

## Writers
- `ralph.pipeline._integration_diagnostic._bump_attempt` writes the
  typed `BUMP_DIAGNOSTIC_ATTEMPT` transition and enforces
  `DIAGNOSTIC_REPAIR_ATTEMPT_CAP` (default 5); over-cap, emits the
  `RAISE_DIAGNOSTIC_INTERVENTION` transition
  (`_integration_diagnostic.py:_bump_attempt`).
- `ralph.pipeline.auto_integrate_recovery` writes the bounded-retry
  `retry_deadline` field on `RebaseState` (via the
  `integration_transaction` lease and the typed refusal path).
- `ralph.pipeline._integration_action._conflict_action` writes the
  typed `IntegrationAction.WAIT_FOR_BOUNDED_RETRY` or
  `IntegrationAction.REQUIRE_INTERVENTION` decision based on the
  `retry_deadline` and the injected clock.

## Readers
- `ralph.pipeline._pipeline_state_machine.decide_phase_transition`
  reads the `IntegrationAction` and the
  `RebaseState.is_intervention_required` flag to block dispatch
  (`_pipeline_state_machine.py:53-89`).
- `ralph.pipeline._terminal_authority.is_terminal_success_eligible`
  reads `is_intervention_required` to gate terminal success
  (`_terminal_authority.py:42`).

## Derived projections
- `IntegrationAction.WAIT_FOR_BOUNDED_RETRY` and
  `IntegrationAction.REQUIRE_INTERVENTION` are the typed projections
  of the retry decision (`_integration_action.py:17-26`).

## Valid transitions
- `BUMP_DIAGNOSTIC_ATTEMPT` and `RAISE_DIAGNOSTIC_INTERVENTION` are
  the diagnostic-repair transitions
  (`auto_integrate_transition.py:_register_all_transitions`).

## Restart behaviour
The bounded-retry `retry_deadline` is persisted on the
`RebaseState`; a crash before the wait expires replays the
bounded wait (no replenishment). A crash after the wait expires
maps to `REQUIRE_INTERVENTION` on the next read.

## UNKNOWN representation
- A missing or invalid `retry_deadline` (not a float, or
  unparseable) maps to `REQUIRE_INTERVENTION` — the architecture
  forbids an unbounded wait
  (`_integration_action.py:128-141`).
- A `retry_deadline` that has already passed (clock >= deadline)
  maps to `REQUIRE_INTERVENTION`.

## What blocks dispatch / terminal success
- `IntegrationAction.WAIT_FOR_BOUNDED_RETRY` blocks dispatch
  until the deadline expires; the recovery boundary keeps the
  record retained for the bounded wait
  (`auto_integrate_recovery.py:_record_skip`).
- `IntegrationAction.REQUIRE_INTERVENTION` and
  `RebaseState.is_intervention_required` block dispatch and
  surface `REPORT_INTERVENTION_REQUIRED`.
- A `merge_commit_repair_attempts` count at the cap emits
  `RAISE_DIAGNOSTIC_INTERVENTION` and stops automatic rotation.

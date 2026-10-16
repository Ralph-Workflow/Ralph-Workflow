# startup_and_cooldown_recovery

## Authoritative owner
`ralph.pipeline.auto_integrate_recovery.recover_incomplete_integration`
is the single authority for the startup recovery decision
(`auto_integrate_recovery.py:576-682`).

## Writers
- `ralph.pipeline.auto_integrate_recovery.recover_incomplete_integration`
  writes a typed `RebaseState` with `last_outcome` and
  `recovery_record_retained` flags for downstream consumers.
- The run loop's `reconcile_stale_unresolved_state` writer surfaces the
  recovery outcome to `ctx.latest_state` (`run_loop.py`).
- Cooldown is implemented in
  `ralph.pipeline._integration_action._conflict_action` via the
  `retry_deadline` field on `RebaseState`
  (`_integration_action.py:111-141`).

## Readers
- `ralph.pipeline.run_loop` reads the recovery outcome at every
  entry point via `ctx.latest_state` and the run-loop boundary
  (`run_loop.py:_block_unresolved_integration`,
  `run_loop.py:_handle_retained_integration`).
- `ralph.pipeline._pipeline_state_machine.decide_phase_transition`
  reads `rebase_state.recovery_record_retained` to block dispatch
  (`_pipeline_state_machine.py:53-89`).

## Derived projections
- `RebaseState.recovery_record_retained` is a typed boolean with a
  typed `recovery_record_retained_reason` companion
  (`rebase_state.py:309-326`).

## Valid transitions
- The recovery boundary only ever sets the retention flag through
  `_record_skip(reason=..., record_retained=True,
  retention_reason=...)` or the typed intervention outcome; the
  run loop's boundary consults the same authority outputs
  (`auto_integrate_recovery.py:600-682`).

## Restart behaviour
A crash before the recovery decision leaves the durable
`IntegrationRecord` intact; on restart, `recover_incomplete_integration`
re-reads the record via the typed `read_record_outcome` and re-derives
the recovery decision. No replay of completed business work.

## UNKNOWN representation
- `read_record_outcome` returns `UNREADABLE` for corrupt bytes, which
  the recovery boundary maps to
  `DIAGNOSTIC_INTERVENTION_REQUIRED` with `recovery_record_retained=True`
  (`auto_integrate_recovery.py:600-617`).
- `BUSY` is reserved for demonstrable transient lock contention; the
  recovery boundary routes it through the `integration_transaction`
  lease and the typed refusal path.

## What blocks dispatch / terminal success
- A `rebase_state.recovery_record_retained is True` blocks dispatch
  and terminal success.
- A `rebase_state.is_intervention_required is True` blocks dispatch
  and surfaces `REPORT_INTERVENTION_REQUIRED` to the run loop.

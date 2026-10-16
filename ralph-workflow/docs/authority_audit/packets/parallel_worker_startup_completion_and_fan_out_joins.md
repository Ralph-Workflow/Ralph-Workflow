# parallel_worker_startup_completion_and_fan_out_joins

## Authoritative owner
`ralph.pipeline._fan_out_authority` is the single authority for
parallel-worker fan-out join decisions
(`_fan_out_authority.py:next_fan_out_action`).

## Writers
- `ralph.pipeline._fan_out_authority.next_fan_out_action` writes
  the typed `FanOutDecision` to `rebase_state.last_fan_out_decision`.
- `ralph.parallel.parallel_coordinator` writes the per-worker
  completion receipts that feed the fan-out authority.
- `ralph.pipeline.run_loop` reconciles the fan-out decision into
  the durable `RebaseState` via `reconcile_stale_unresolved_state`.

## Readers
- `ralph.pipeline._terminal_authority.is_terminal_success_eligible`
  reads `last_fan_out_decision` to gate terminal success
  (`_terminal_authority.py:48`).
- `ralph.display.auto_integrate_message` reads the
  `last_fan_out_decision` for the operator display.

## Derived projections
- `FanOutDecision` is a closed enum: `JOIN_PENDING`, `JOIN_COMPLETE`,
  `JOIN_BLOCKED` (`_fan_out_authority.py:FanOutDecision`).

## Valid transitions
- Parallel-worker completion is a derived projection; the
  fan-out decision is the typed outcome of the
  `next_fan_out_action` call.

## Restart behaviour
A crash before a fan-out decision leaves the prior
`last_fan_out_decision` intact; on restart, the fan-out authority
re-derives the decision from the per-worker receipts. No
completed fan-out is replayed.

## UNKNOWN representation
- `last_fan_out_decision is None` means the fan-out authority has
  not yet decided; the terminal authority treats
  `None` as "not yet proven complete" and blocks terminal success.

## What blocks dispatch / terminal success
- `last_fan_out_decision != JOIN_COMPLETE` blocks terminal success
  (`_terminal_authority.py:48`).
- A parallel-worker timeout that returns `JOIN_BLOCKED` blocks
  terminal success; a `JOIN_PENDING` decision means the
  coordinator is still waiting.

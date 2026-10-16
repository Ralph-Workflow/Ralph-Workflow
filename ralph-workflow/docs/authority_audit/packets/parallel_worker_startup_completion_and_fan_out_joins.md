# parallel_worker_startup_completion_and_fan_out_joins

## Authoritative owner
`ralph.pipeline._fan_out_authority.fan_out_decision` is the single
authority for parallel-worker fan-out join decisions
(`_fan_out_authority.py:108-127`).

## Writers
- `ralph.pipeline._fan_out_authority` writes the typed
  `FanOutDecision` to `rebase_state.last_fan_out_decision` via
  `_with_fan_out_decision`, invoked from the `handle_worker_*` /
  `handle_fan_out_started` / `handle_workers_resumed` event
  handlers (`_fan_out_authority.py:32-105`).
- `ralph.pipeline.reducer` applies the worker completed/failed
  events through those handlers (`reducer.py:146`,
  `reducer.py:184`).
- `ralph.pipeline.parallel.ParallelCoordinator` drives the
  per-worker lifecycle whose events feed the fan-out authority.

## Readers
- `ralph.pipeline._terminal_authority.is_terminal_success_eligible`
  reads `last_fan_out_decision` to gate terminal success
  (`_terminal_authority.py:45`).
- `ralph.pipeline._pipeline_state_machine.decide_phase_transition`
  consults the same authority for the terminal branch.

## Derived projections
- `FanOutDecision` is a closed enum: `JOIN_PENDING`,
  `JOIN_COMPLETE`, `JOIN_PARTIAL_FAILURE`, `JOIN_BLOCKED`
  (`_fan_out_authority.py:23-29`).

## Valid transitions
- Parallel-worker completion is a derived projection; the
  fan-out decision is the typed outcome of the
  `fan_out_decision(state)` call
  (`_fan_out_authority.py:108-127`).

## Restart behaviour
A crash before a fan-out decision leaves the prior
`last_fan_out_decision` intact; on restart, the fan-out decision is
re-derived from the persisted `worker_states` in the checkpoint
(`_fan_out_authority.py:108-127`). No completed fan-out is
replayed.

## UNKNOWN representation
- `last_fan_out_decision is None` means the fan-out authority has
  not yet decided; the terminal authority treats `None` as
  eligible — an absent decision does NOT block terminal success
  (`_terminal_authority.py:45`).
- Worker statuses that map to no explicit decision fall through to
  `JOIN_BLOCKED` (`_fan_out_authority.py:127`).

## What blocks dispatch / terminal success
- `last_fan_out_decision` not in (`None`, `JOIN_COMPLETE`)
  blocks terminal success (`_terminal_authority.py:45`).
- `JOIN_PENDING` means workers are still running or pending
  (`_fan_out_authority.py:119-120`).
- `JOIN_PARTIAL_FAILURE` (a worker FAILED) blocks terminal
  success (`_fan_out_authority.py:121-122`).
- `JOIN_BLOCKED` (all workers CANCELLED or an unrecognized
  status set) blocks terminal success
  (`_fan_out_authority.py:125-127`).

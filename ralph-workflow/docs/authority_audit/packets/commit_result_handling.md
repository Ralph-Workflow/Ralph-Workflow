# commit_result_handling

## Authoritative owner
`ralph.pipeline._commit_completion_authority.complete_commit` is the
single authority for the commit-completion promotion
(`_commit_completion_authority.py:28`).

## Writers
- `ralph.pipeline.auto_integrate_recovery_terminal` writes the
  post-attempt verified state after `_commit_completion_authority` has
  promoted the record (`auto_integrate_recovery_terminal.py`).
- `ralph.pipeline.run_loop` writes the typed `last_decision` via
  `reconcile_stale_unresolved_state` for downstream readers.

## Readers
- `ralph.pipeline._pipeline_state_machine.decide_phase_transition`
  reads the `last_outcome` projection to block unlanded rebase
  (`_pipeline_state_machine.py:90-100`).
- `ralph.pipeline._terminal_authority.is_terminal_success_eligible`
  reads the typed `last_outcome` to gate terminal success.

## Derived projections
- `RebaseState.last_outcome` (typed `RebaseOutcomeKind`) is the
  derived projection; `last_action` is a read-only backward-
  compatible `@property` (`rebase_state.py:84-93`).

## Valid transitions
- `PROMOTE_TO_INTEGRATED` is the transition that records the
  integrated feature SHA; `START_INTEGRATING` and `CLEAR` are the
  boundary transitions; `MARK_RESOLVING_REBASE`,
  `MARK_PENDING_REBASE_CONTINUE`, `MARK_RESOLVING_MERGE`,
  `MARK_PENDING_MERGE_COMMIT` are the in-flight transitions
  (`auto_integrate_transition.py`).

## Restart behaviour
A crash before `complete_commit` returns leaves the integration record
in its prior phase; on restart, the recovery boundary re-derives the
next action. A crash after `complete_commit` returns but before the
record is written is closed by `IntegrationRecordStore.transition`'s
PROMOTE_TO_INTEGRATED idempotency short-circuit
(`auto_integrate_record_store.py:75`).

## UNKNOWN representation
- An empty `pre_target_sha` means the atomic compare-and-swap is
  unproven; the recovery boundary treats it as a clearable no-op
  (not as a proven landing) — explicit `unproven_landing` is
  surfaced through `rebase_state.unlanded_rebase` rather than as a
  successful outcome.

## What blocks dispatch / terminal success
- An unlanded rebase (`last_outcome in (REBASED, MERGED)` and
  `fast_forwarded is False`) blocks dispatch and terminal success
  via `_pipeline_state_machine._check_blocking_decision`
  (`_pipeline_state_machine.py:90-99`).

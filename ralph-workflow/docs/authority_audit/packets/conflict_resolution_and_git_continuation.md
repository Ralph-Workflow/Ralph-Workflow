# conflict_resolution_and_git_continuation

## Authoritative owner
`ralph.pipeline._integration_action.next_integration_action` is the
single authority for the integration lifecycle action
(`_integration_action.py:224-252`); for in-flight conflict and merge
state, the authority is the
`ralph.pipeline._git_operation_authority.classify_git_operation` enum.

## Writers
- `ralph.pipeline._git_operation_authority.classify_git_operation`
  classifies the current Git operation into a typed
  `GitOperationKind` (`_git_operation_authority.py:classify_git_operation`).
- `ralph.pipeline.auto_integrate_rebase_merge` writes the resolve
  and continuation transitions
  (`auto_integrate_rebase_merge.py:resolve_rebase`,
  `continue_rebase`).
- `ralph.pipeline._pending_merge_commit` writes the
  `MARK_PENDING_MERGE_COMMIT` transition on a verified commit
  (`_pending_merge_commit.py`).

## Readers
- `ralph.pipeline.run_loop` reads the integration action and the
  Git operation kind at the phase-boundary entry point.
- `ralph.pipeline._pipeline_state_machine.decide_phase_transition`
  reads the typed `IntegrationAction` to block dispatch.

## Derived projections
- `IntegrationAction` is a closed 8-value enum
  (`_integration_action.py:17-26`); `GitOperationKind` is the
  classification enum.

## Valid transitions
- `MARK_RESOLVING_REBASE`, `MARK_PENDING_REBASE_CONTINUE`,
  `MARK_RESOLVING_MERGE`, `MARK_PENDING_MERGE_COMMIT`,
  `ACCEPT_REPAIR_EDITS`, `RESUME_REPAIR` are the in-flight
  transitions; `START_INTEGRATING`, `PROMOTE_TO_INTEGRATED`, `CLEAR`
  are the boundary transitions
  (`auto_integrate_transition.py:_register_all_transitions`).

## Restart behaviour
A crash before a transition leaves the prior recoverable state; a
crash after leaves the new recoverable state. The pending rebase
metadata captured at `MARK_PENDING_REBASE_CONTINUE` is the durable
replay queue (`auto_integrate_transition.py:MARK_PENDING_REBASE_CONTINUE`)
and is consumed by `resume_pending_rebase` on the next turn
(`_pending_rebase_continue.py:resume_pending_rebase`).

## UNKNOWN representation
- `classify_git_operation` returns `GitOperationKind.NONE` when
  the operation state is unreadable or unowned; the recovery
  boundary treats NONE as a "no current operation" signal and
  inspects the durable record to derive the next action.

## What blocks dispatch / terminal success
- An in-flight Git operation (`resolving_rebase`,
  `resolving_merge`, `rebase_continue_pending`, `merge_commit_pending`)
  blocks dispatch and terminal success.
- `RebaseOutcomeKind.CONFLICT` blocks dispatch through the
  `_conflict_action` retry deadline check; an expired or missing
  `retry_deadline` maps to `REQUIRE_INTERVENTION`
  (`_integration_action.py:111-141`).

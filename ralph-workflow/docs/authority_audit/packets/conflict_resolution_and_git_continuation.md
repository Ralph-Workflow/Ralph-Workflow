# conflict_resolution_and_git_continuation

## Authoritative owner
The integration owner seam is the single authority for the integration
lifecycle action (`ralph.pipeline.integration_owner`: `decide_integration` /
`decide_integration_verdict`, reached from the pipeline boundaries through
`ralph.pipeline._integration_owner_seam.decide_seam_action`);
`ralph.pipeline._integration_action.next_integration_action`
(`_integration_action.py:258`) is the retired typed projection onto the
closed `IntegrationAction` vocabulary — it fails closed to
`REQUIRE_INTERVENTION` and is not an authority. For in-flight conflict and
merge state, the authority is the
`ralph.pipeline._git_operation_authority.classify_git_operation` enum
(`_git_operation_authority.py:30`).

## Writers
- `ralph.pipeline._git_operation_authority.classify_git_operation`
  classifies the current Git operation into a typed
  `GitOperationKind` (`_git_operation_authority.py:30-48`).
- `ralph.pipeline.auto_integrate_rebase_merge.run_rebase_or_merge`
  drives the resolve and continuation transitions
  (`auto_integrate_rebase_merge.py:123`);
  `git/rebase/rebase_continuation.continue_rebase_at` performs the
  rebase continuation (`rebase_continuation.py:153`).
- `ralph.pipeline._pending_merge_commit` writes the
  `MARK_PENDING_MERGE_COMMIT` transition on a verified commit
  (`_pending_merge_commit.py:54,77`).

## Readers
- `ralph.pipeline.run_loop` reads the integration action and the
  Git operation kind at the phase-boundary entry point.
- `ralph.pipeline._pipeline_state_machine.decide_phase_transition`
  reads the typed `IntegrationAction` to block dispatch.

## Derived projections
- `IntegrationAction` is a closed 8-value enum
  (`_integration_action.py:17-27`); `GitOperationKind` is the
  classification enum.

## Valid transitions
- `MARK_RESOLVING_REBASE`, `MARK_PENDING_REBASE_CONTINUE`,
  `MARK_RESOLVING_MERGE`, `MARK_PENDING_MERGE_COMMIT`,
  `ACCEPT_REPAIR_EDITS`, `RESUME_REPAIR` are the in-flight
  transitions; `START_INTEGRATING`, `PROMOTE_TO_INTEGRATED`, `CLEAR`
  are the boundary transitions
  (`auto_integrate_transition.py:261`).

## Restart behaviour
A crash before a transition leaves the prior recoverable state; a
crash after leaves the new recoverable state. The pending rebase
metadata captured at `MARK_PENDING_REBASE_CONTINUE` is the durable
replay queue (`auto_integrate_transition.py:152`) and is consumed by
`resume_pending_rebase` on the next turn
(`_pending_rebase_continue.py:131`).

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

## Conflict-strategy and intervention typed setters (2026)

The conflict-strategy / deadline / intervention decision fields on
`RebaseState` are owned by seven typed setters
(`rebase_state.py:491-622`):

- `set_retry_deadline(value: float | None) -> Self` -- single writer
  of `retry_deadline`; rejects non-finite values.
- `record_consecutive_conflicts(value: int) -> Self` -- single writer
  of `consecutive_conflicts`; clamped to
  `MAX_TRACKED_CONFLICT_ATTEMPTS` inside the setter. The counter
  describes the repository (conflict identity plus the operator
  escalation); it never selects, suppresses or demotes an agent.
- `advance_conflict_strategy(reason: str) -> Self` -- bounded
  advancement of `conflict_strategy_index`,
  `conflict_strategies_tried`, `resolution_exhausted`,
  `resolution_exhaustion_reason`. The strategy-cycling modulo
  `(index + 1) % len(CONFLICT_RESOLUTION_STRATEGIES)` is gone;
  `_wait_for_integration_retry` calls `set_intervention_required`
  on the exhaustion path instead.
- `project_conflict_strategy_from(source: RebaseState) -> Self` --
  exact one-way projection of the same four fields; used by
  `_merge_strategy_onto_outcome` (`runner.py:1002-1011`).
- `clear_conflict_strategy() -> Self` -- reset of all four
  strategy fields to defaults; used by
  `reconcile_stale_unresolved_state`
  (`auto_integrate_resolution_state.py`) when the strategy block
  is stale or never started.
- `clear_resolution_exhausted() -> Self` -- reset of only the
  exhaustion flag pair, preserving the strategy index and tried
  list.
- `set_intervention_required(reason: str, *, diagnostic: bool = False) -> Self` --
  single writer of the persistent
  `INTERVENTION_REQUIRED` / `DIAGNOSTIC_INTERVENTION_REQUIRED`
  transition (`last_outcome` + `last_reason`). The exhaustion
  path no longer calls `_run_auto_integrate_recovery_preamble`
  after `set_intervention_required`; the run-loop consumer
  (`_maybe_auto_integrate`) checks `rebase.is_intervention_required`
  and refuses to dispatch ordinary work.

Ad-hoc `model_copy(update=...)` on any of these decision fields
outside the seven setter bodies is forbidden
(`tests/test_no_superseded_paths_typed_setter_contract.py`).

## Attempt-fault trust boundary (2026)

`ralph/pipeline/conflict_resolution/attempt_fault.py::classify_attempt_fault`
authorizes `AttemptFault.AGENT_HEALTHY` only for an exact match
`text.lower() == "healthy"` (line 154). Arbitrary agent output
containing `"healthy"` as a substring (e.g. `"unhealthy
environment"`, `"agent is healthy enough"`) classifies to
`AttemptFault.AGENT_EXITED`, not `AGENT_HEALTHY`. The two
remaining closed-vocabulary scans at `attempt_fault.py:115` (the
named token `SUPERVISION_INFRASTRUCTURE_FAILURE`) and
`attempt_fault.py:154` (`any(k in lowered for k in (...))` against
`ResolutionTerminationReason` enum member identifiers) remain
allowed because they consult a closed vocabulary, not free-form
prose.
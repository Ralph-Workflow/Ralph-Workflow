# target_landing_and_remote_reconciliation

## Authoritative owner
`ralph.pipeline.auto_integrate_ff.maybe_push_target` is the local
landing + remote publication authority
(`auto_integrate_ff.py:maybe_push_target`); for target reconciliation
across linked worktrees, the authority is
`ralph.pipeline._remote_reconcile_recovery.recover_target_resolution`
(`_remote_reconcile_recovery.py:recover_target_resolution`).

## Writers
- `ralph.pipeline.auto_integrate_ff.maybe_push_target` writes the
  post-landing `RebaseState.fast_forwarded=True` and the publication
  decision projection.
- `ralph.pipeline._remote_reconcile_recovery.recover_target_resolution`
  writes the `RELEASE_DIAGNOSTIC_EVIDENCE` transition through the
  record store (via `apply_record_transition`); BUSY/UNREADABLE
  refusals are mapped to typed retry or intervention outcomes
  (`_remote_reconcile_recovery.py:60-95`).
- `ralph.pipeline._target_reconciliation_handoff.finish_target_substep`
  writes the target reconciliation completion
  (`_target_reconciliation_handoff.py:finish_target_substep`).

## Readers
- `ralph.pipeline._publication_state_machine.next_publication_action`
  reads the `RebaseState` and the integration record to derive the
  typed `PublicationDecision`
  (`_publication_state_machine.py:43-67`).
- `ralph.pipeline._terminal_authority.is_terminal_success_eligible`
  reads the `last_publication_decision` to gate terminal success
  (`_terminal_authority.py:43-46`).

## Derived projections
- `PublicationDecision` is a closed 6-value enum
  (`_publication_state_machine.py:18-25`); `rebase_state.fast_forwarded`
  is the typed projection of the landing.

## Valid transitions
- `PROMOTE_TO_INTEGRATED` (with `integrated_feature_sha`) is the
  transition that records a successful integration; landing is the
  ordinary fast-forward that follows; remote publication is the
  push to the configured remote.

## Restart behaviour
A crash before landing leaves the integration record in
`integrated` phase; on restart, `_continue_fast_forward_from_record`
resumes the landing. A crash during remote publication is closed by
`maybe_push_target`'s typed `PublicationDecision` outcome.

## UNKNOWN representation
- `next_publication_action` returns `PUBLISH_DEFERRED_FRESHNESS` for
  any non-`verified` freshness verdict; the lifecycle treats
  `deferred` and `unverified` as the same projection.
- `configured_remote is None` short-circuits to
  `PUBLICATION_TERMINAL` (no publication obligation).
- `record is not None and record.phase != "integrated"` short-
  circuits to `PUBLICATION_PENDING`.

## What blocks dispatch / terminal success
- `rebase_state.fast_forwarded is False` after a `REBASED` /
  `MERGED` outcome blocks dispatch
  (`_pipeline_state_machine.py:90-100`).
- A non-terminal `last_publication_decision` blocks terminal
  success.
- `record.phase != "integrated"` blocks publication (returns
  `PUBLICATION_PENDING`).

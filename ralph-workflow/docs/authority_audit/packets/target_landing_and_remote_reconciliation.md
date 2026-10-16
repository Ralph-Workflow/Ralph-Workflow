# target_landing_and_remote_reconciliation

## Authoritative owner
`ralph.pipeline.auto_integrate_ff.maybe_push_target` is the local
landing + remote publication authority
(`auto_integrate_ff.py:391`); the typed publication decision is
written by
`ralph.pipeline.auto_integrate_remote_sync.push_target_after_landing`
(`auto_integrate_remote_sync.py:609-649`) via the single-writer
`RebaseState.set_publication_decision` (`rebase_state.py:430-439`);
for target reconciliation across linked worktrees, the authority is
`ralph.pipeline._remote_reconcile_recovery.recover_target_resolution`
(`_remote_reconcile_recovery.py:48`).

## Writers
- `ralph.pipeline.auto_integrate_remote_sync.push_target_after_landing`
  writes the post-landing `last_publication_decision`,
  `last_push`, `last_remote_sync` and `last_outcome`
  (`auto_integrate_remote_sync.py:609-649`); the local landing
  itself sets `fast_forwarded=True` in `auto_integrate.py:793` and
  `_recovery_fast_forward_landing.py:166`.
- `ralph.pipeline._remote_reconcile_recovery.recover_target_resolution`
  writes the `RELEASE_DIAGNOSTIC_EVIDENCE` transition through the
  record store (via `apply_record_transition`,
  `_remote_reconcile_recovery.py:188`); BUSY/UNREADABLE
  refusals are mapped to typed retry or intervention outcomes
  (`_remote_reconcile_recovery.py:77-98,167+`).
- `ralph.pipeline._target_reconciliation_handoff.finish_target_substep`
  writes the target reconciliation completion
  (`_target_reconciliation_handoff.py:24`).

## Readers
- `ralph.pipeline._publication_state_machine.next_publication_action`
  reads the `RebaseState` and the integration record to derive the
  typed `PublicationDecision`
  (`_publication_state_machine.py:43-67`).
- `ralph.pipeline._terminal_authority.is_terminal_success_eligible`
  reads the `last_publication_decision` to gate terminal success
  (`_terminal_authority.py:37,44`).

## Derived projections
- `PublicationDecision` is a closed 6-value enum
  (`_publication_state_machine.py:22-30`);
  `rebase_state.fast_forwarded` is the typed projection of the
  landing.

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
- `next_publication_action` returns `PUBLISH_DEFERRED_FRESHNESS`
  for any non-`verified`, non-`unsafe` freshness verdict
  (including `degraded` and `unverified` and any unknown value)
  (`_publication_state_machine.py:33-40`).
- `configured_remote is None` short-circuits to
  `PUBLICATION_TERMINAL` (no publication obligation).
- `record is not None and record.phase != "integrated"` short-
  circuits to `PUBLICATION_PENDING`.

## What blocks dispatch / terminal success
- `rebase_state.fast_forwarded is False` after a `REBASED` /
  `MERGED` outcome blocks dispatch
  (`_pipeline_state_machine.py:90-99`).
- A non-terminal `last_publication_decision` blocks terminal
  success (`_terminal_authority.py:44`).
- `record.phase != "integrated"` blocks publication (returns
  `PUBLICATION_PENDING`).

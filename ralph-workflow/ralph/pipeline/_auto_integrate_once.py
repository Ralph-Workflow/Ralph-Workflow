"""Single-attempt integration machinery of :mod:`ralph.pipeline.auto_integrate`.

:func:`_integrate_once` / :func:`_integrate_once_owned` run one
rebase-or-merge attempt inside the integration transaction (durable
record transitions, backup ref, fast-forward, remote publication), and
:func:`_reintegrate_after_remote_reconcile` re-lands the feature after a
target-to-remote reconciliation. They live in this private companion
module so ``auto_integrate.py`` stays under the repo-structure file-size
cap; the orchestration (retry loop, recovery preamble, resolver
suppression) stays there and re-exports every name defined here
unchanged.

Names that tests monkeypatch on the ``ralph.pipeline.auto_integrate``
module (``_run_rebase_or_merge``, ``_fast_forward_target``,
``_clear_record``, ``_record_rebase_outcome``, ``post_attempt_verify``,
``contained_land``, ``read_record``, ``get_head_sha``, ``branch_sha``,
the backup-ref aliases, and the record I/O probes) are reached through a
deferred ``auto_integrate`` module attribute access, so the monkeypatch
surface is unchanged by the split.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.pipeline._commit_completion_authority import complete_commit
from ralph.pipeline._target_reconciliation_handoff import scheduled_landing
from ralph.pipeline.auto_integrate_context import record_refresh
from ralph.pipeline.auto_integrate_ff import is_retryable_fast_forward_failure
from ralph.pipeline.auto_integrate_outcome import record_skip as _record_skip
from ralph.pipeline.auto_integrate_rebase_merge import with_skip_reason
from ralph.pipeline.auto_integrate_record import (
    START_INTEGRATING,
    IntegrationRecord,
    RecordReadOutcomeDisposition,
)
from ralph.pipeline.auto_integrate_remote_sync import (
    reconcile_after_rejected_push,
)
from ralph.pipeline.auto_integrate_terminal import (
    verify_and_cleanup_backup as _verify_and_cleanup_backup,
)
from ralph.pipeline.integration_actions import (
    current_agent_runner,
    current_git_ops,
    default_git_ops,
    install_executors,
    reset_executors,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline._auto_integrate_reclaim import ReclaimResult
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.integration_actions import Clock
    from ralph.pipeline.rebase_state import RebaseState

__all__ = [
    "_integrate_once",
    "_integrate_once_owned",
    "_reintegrate_after_remote_reconcile",
]


def _reintegrate_after_remote_reconcile(
    config: UnifiedConfig,
    root: Path,
    target: str,
    conflict_resolver: ConflictResolver | None,
    *,
    refresh: str | None,
    rebase_stop_resolver: RebaseStopResolver | None,
    display: ParallelDisplay | None,
) -> bool:
    """Rebase and land the feature after a target-to-remote reconciliation."""
    record, retry = _integrate_once_owned(
        config,
        root,
        target,
        conflict_resolver,
        refresh=refresh,
        rebase_stop_resolver=rebase_stop_resolver,
        display=display,
        publish=False,
        owned_record=scheduled_landing(root, target),
    )
    return record is not None and record.fast_forwarded and not retry


def _integrate_once(
    config: UnifiedConfig,
    root: Path,
    target: str,
    conflict_resolver: ConflictResolver | None,
    *,
    prefer_merge: bool = False,
    refresh: str | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    display: ParallelDisplay | None = None,
    publish: bool = True,
    require_in_sync: bool = False,
    clock: Clock | None = None,
) -> tuple[RebaseState | None, bool]:
    from ralph.pipeline import auto_integrate as _ai

    clock_token = install_executors(clock=clock)
    try:
        with _ai.integration_transaction(root) as acquired:
            if not acquired:
                return _record_skip(
                    reason="integration already running in this worktree", target=target
                ), False
            return _integrate_once_owned(
                config,
                root,
                target,
                conflict_resolver,
                prefer_merge=prefer_merge,
                refresh=refresh,
                rebase_stop_resolver=rebase_stop_resolver,
                display=display,
                publish=publish,
                require_in_sync=require_in_sync,
                owned_record=scheduled_landing(root, target),
            )
    finally:
        reset_executors(clock_token)


def _integrate_once_owned(
    config: UnifiedConfig,
    root: Path,
    target: str,
    conflict_resolver: ConflictResolver | None,
    *,
    prefer_merge: bool = False,
    refresh: str | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    display: ParallelDisplay | None = None,
    publish: bool = True,
    require_in_sync: bool = False,
    owned_record: IntegrationRecord | None = None,
) -> tuple[RebaseState | None, bool]:
    """Run one rebase-or-merge integration and report whether a landing race merits retry."""
    # Late binding through the auto_integrate namespace: tests monkeypatch
    # these names on the ``ralph.pipeline.auto_integrate`` module.
    from ralph.pipeline import auto_integrate as _ai

    _ai.post_attempt_verify(root, expected_head_sha=None, owns_resolution=False)
    # Idempotent land: the target already holds every feature commit, so
    # nothing needs rebasing, merging or moving. Report the same success a
    # first landing reports and release every latch the record was holding.
    # A catch-up (``require_in_sync``) is the planning path: the branch must
    # also absorb the target tip, so "already inside target" is not enough.
    contained = _ai.contained_land(root, target, require_in_sync=require_in_sync)
    if contained is not None:
        return contained, False
    pre_feature_sha = _ai.get_head_sha(root)
    pre_target_sha = _ai.branch_sha(root, target)
    retained = _ai.read_record(root)
    if retained != owned_record or (
        _ai.read_record_outcome(root).disposition is RecordReadOutcomeDisposition.UNREADABLE
    ):
        return _record_skip(
            reason="unfinished integration retained for recovery", target=target
        ), False
    attempt_record = (
        owned_record.with_transition(
            START_INTEGRATING,
            phase="integrating",
            pre_feature_sha=pre_feature_sha,
            pre_target_sha=pre_target_sha,
            integrated_feature_sha=None,
        )
        if owned_record is not None
        else IntegrationRecord(
            phase="integrating",
            target=target,
            pre_feature_sha=pre_feature_sha,
            pre_target_sha=pre_target_sha,
        )
    )
    _ai._write_record_probe(root, attempt_record)
    # B11/E5: backup the original feature tip on a uniquely named
    # ``refs/rebase-backup/<id>`` ref BEFORE any mutation so a
    # concurrent ``git gc --prune`` cannot reclaim the in-flight commits.
    # The ref is retained through the attempt (the recovery preamble
    # reads it) and deleted after a successful land or a verified abort.
    backup_ref = _ai._create_rebase_backup_ref(
        root,
        pre_feature_sha,
        attempt_record.owning_worktree or str(root),
    )
    owns_resolution = False
    try:
        rebase_result = _ai._run_rebase_or_merge(
            root,
            target,
            conflict_resolver,
            prefer_merge=prefer_merge,
            rebase_stop_resolver=rebase_stop_resolver,
            display=display,
            conflict_resolution_config=config.conflict_resolution,
            git_ops=current_git_ops() if current_git_ops() is not default_git_ops() else None,
            agent_runner=current_agent_runner(),
        )
        if rebase_result.short_circuit is not None:
            # Resolved failures clear the record; an abort that leaves a rebase
            # in progress retains it for startup recovery.
            if not rebase_result.short_circuit.recovery_record_retained:
                _verify_and_cleanup_backup(root, backup_ref, pre_feature_sha, owns_resolution)
            return record_refresh(rebase_result.short_circuit, refresh), False

        feature_sha = _ai._read_post_integration_head_sha_probe(root, target)
        if feature_sha is None:
            _verify_and_cleanup_backup(root, backup_ref, pre_feature_sha, owns_resolution)
            return record_refresh(
                _record_skip(reason="post-integration HEAD read failed", target=target),
                refresh,
            ), False

        _ai.post_attempt_verify(root, expected_head_sha=None, owns_resolution=False)
        # Flip the durable record to 'integrated' BEFORE any ref move so
        # a crash between this point and the fast-forward will be
        # recoverable as a continue-fast-forward rather than a restore
        # (AC-11).
        _ai._write_record_probe(
            root,
            complete_commit(attempt_record, integrated_feature_sha=feature_sha).record,
        )

        refresh_outcome = refresh
        from ralph.pipeline.auto_integrate_remote_sync import reclaim_target_worktree_enabled

        reclaim_target_worktree = reclaim_target_worktree_enabled(config)
        reclamation: ReclaimResult | None = None

        def remember_reclamation(result: ReclaimResult) -> None:
            nonlocal reclamation
            reclamation = result

        ok, skip_reason = _ai._fast_forward_target(
            root,
            target,
            feature_sha,
            reclaim_target_worktree=reclaim_target_worktree,
            on_reclaimed=remember_reclamation,
        )

        # R6/AC-06: post-attempt terminal-state verification on EVERY
        # exit path. A completed rebase/merge leaves HEAD at feature_sha,
        # regardless of whether the target fast-forward wins its race.
        #
        # The record MUST stay on disk until this verification passes, so
        # the recovery preamble can reclaim the leaked state using the
        # recorded pre-attempt SHA. Clearing the record BEFORE
        # verification (the prior shape) discarded that metadata.
        _ai.post_attempt_verify(root, expected_head_sha=feature_sha, owns_resolution=False)
        if ok:
            _ai._delete_rebase_backup_ref(root, backup_ref)
        if ok:
            _ai._clear_record(root)

        record = _ai._record_rebase_outcome(
            rebase_outcome=rebase_result.rebase_outcome,
            merge_attempted=rebase_result.merge_attempted,
            merge_outcome=rebase_result.merge_outcome,
            target=target,
        ).model_copy(
            update={
                "last_refresh": refresh_outcome,
                "reclaimed_worktree_path": (
                    reclamation.worktree_path if reclamation is not None else None
                ),
                "reclaim_snapshot_ref": reclamation.snapshot_ref
                if reclamation is not None
                else None,
                "reclaim_discarded_path_count": (
                    reclamation.discarded_path_count if reclamation is not None else 0
                ),
            }
        )
        if not ok:
            logger.critical(
                "CRITICAL: feature {} did NOT land on '{}': {}; committed work remains on the feature",
                feature_sha,
                target,
                skip_reason,
            )
            # Fast-forward skipped: reason is appended but we keep
            # the rebase/merged action as the headline so the log line
            # reflects what actually happened to the feature. Only an
            # explicit concurrent target move merits a bounded retry.
            record = with_skip_reason(
                record.model_copy(update={"last_reason": skip_reason, "fast_forwarded": False}),
                rebase_result.skip_reason,
            )
            return record, is_retryable_fast_forward_failure(skip_reason)
        # A landed feature carries no rebase-phase reason; the registered
        # skip reason is appended below.
        record = record.model_copy(update={"fast_forwarded": True, "last_reason": None})
        if publish:
            # Single publication entry point: the reconciler compares
            # local and remote SHAs, pushes when local is ahead, catches
            # up when remote is ahead, and reintegrates on divergence.
            # A non-fast-forward inside the reconciler is handled by
            # ``reconcile_after_rejected_push``. The caller's
            # ``fast_forwarded`` is carried through ``source_state`` so
            # the headline "did the local land" signal survives.
            record = _ai.reconcile_remote_publication(
                config,
                root,
                target,
                rebase_stop_resolver=rebase_stop_resolver,
                source_state=record,
            )
            if record.last_push_status == "non_fast_forward":
                # A non-fast-forward push is a remote race, not a failed local
                # landing. Re-integrate the feature after reconciling the target
                # before retrying publication, without recursively publishing.
                record = reconcile_after_rejected_push(
                    config,
                    root,
                    target,
                    record,
                    rebase_stop_resolver=rebase_stop_resolver,
                    reintegrate=lambda: _reintegrate_after_remote_reconcile(
                        config,
                        root,
                        target,
                        conflict_resolver,
                        refresh=refresh_outcome,
                        rebase_stop_resolver=rebase_stop_resolver,
                        display=display,
                    ),
                )
        # A skipped rebase is RECORDED: the registered reason text rides on
        # the state, so a prescribed skip is visible next to the fallback.
        record = with_skip_reason(record, rebase_result.skip_reason)
        return record, False
    except BaseException:
        raise

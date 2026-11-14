from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.pipeline import _terminal_emitter as terminal_emitter
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
        with _ai.integration_transaction(root) as outcome:
            if not outcome.acquired:
                return _record_skip(
                    reason=outcome.describe("integration already running in this worktree"),
                    target=target,
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


@dataclass(frozen=True, slots=True)
class _FastForwardStatus:
    ok: bool
    skip_reason: str


def _parse_ff_status(raw_outcome: object) -> _FastForwardStatus:
    from ralph.pipeline.resolution_outcome import (
        ResolvedOutcome,
        RetryLaterOutcome,
        WaitingOutcome,
    )

    match raw_outcome:
        case ResolvedOutcome():
            return _FastForwardStatus(True, "")
        case RetryLaterOutcome(on_change=on_change):
            return _FastForwardStatus(False, on_change or "")
        case WaitingOutcome(external_condition=cond):
            return _FastForwardStatus(False, cond or "")
        case tuple():
            reason = str(raw_outcome[1]) if len(raw_outcome) > 1 else ""
            return _FastForwardStatus(bool(raw_outcome[0]), reason)
        case bool():
            return _FastForwardStatus(raw_outcome, "")
        case _:
            return _FastForwardStatus(False, str(raw_outcome))


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
    from ralph.pipeline import auto_integrate as _ai

    _ai.post_attempt_verify(root, expected_head_sha=None, owns_resolution=False)
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

        raw_ff_outcome: object = _ai._fast_forward_target(
            root,
            target,
            feature_sha,
            reclaim_target_worktree=reclaim_target_worktree,
            on_reclaimed=remember_reclamation,
        )
        ff_status = _parse_ff_status(raw_ff_outcome)
        ok, skip_reason = ff_status.ok, ff_status.skip_reason

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
            terminal_emitter.critical(
                "CRITICAL: feature {} did NOT land on '{}': {}; committed work remains on the feature",
                feature_sha,
                target,
                skip_reason,
            )
            record = with_skip_reason(
                record.model_copy(update={"last_reason": skip_reason, "fast_forwarded": False}),
                rebase_result.skip_reason,
            )
            return record, is_retryable_fast_forward_failure(raw_ff_outcome)
        record = record.model_copy(update={"fast_forwarded": True, "last_reason": None})
        if publish:
            record = _ai.reconcile_remote_publication(
                config,
                root,
                target,
                rebase_stop_resolver=rebase_stop_resolver,
                source_state=record,
            )
            if record.last_push_status == "non_fast_forward":
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
        record = with_skip_reason(record, rebase_result.skip_reason)
        return record, False
    except BaseException:
        raise

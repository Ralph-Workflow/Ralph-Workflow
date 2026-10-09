"""Safe target-branch reconciliation for configured remote sync.

A diverged local target is rebased only in the clean worktree that owns the
target branch. This keeps checked-out indexes coherent and never rewrites the
fetched remote commit. Agent-owned resolutions survive interruption for
continuation in the target worktree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.git.merge import WORKTREE_FOUND, branch_sha, worktree_lookup
from ralph.git.operations import find_main_worktree_root, get_head_sha, is_repo_clean
from ralph.git.rebase.rebase import (
    RebaseNoOp,
    RebaseSuccess,
    rebase_in_progress,
    rebase_onto,
)
from ralph.pipeline._auto_integrate_reclaim import reclaim_dirty_target_worktree
from ralph.pipeline._target_reconciliation_handoff import finish_target_substep
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    bind_integration_record_root,
    clear_record,
    read_record,
    write_record,
)
from ralph.pipeline.auto_integrate_transaction import integration_transaction
from ralph.pipeline.conflict_resolution.abort import abort_rebase_discarding_progress
from ralph.pipeline.conflict_resolution.rebase_loop import (
    RebaseStopResolver,
    current_rebase_identity,
    resolution_session_from_config,
    resolve_rebase_in_progress,
)
from ralph.pipeline.integration_resolution import retained_integration_reason

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.conflict_resolution.session import ResolutionSession


CLEAN_ABORTED_RECONCILIATION_CONFLICT = "cleanly aborted conflict:"


@dataclass(frozen=True)
class ReconciliationOutcome:
    """Structured result of a target reconciliation attempt.

    ``cleanly_aborted`` is true only after the abort, target-SHA verification,
    and durable-record cleanup all succeeded.  It is intentionally separate
    from ``reason``, which is operator-facing presentation text.
    """

    reconciled: bool
    reason: str
    cleanly_aborted: bool = False

    def __iter__(self) -> Iterator[bool | str]:
        """Preserve the two-value compatibility contract for direct callers."""
        yield self.reconciled
        yield self.reason


def reconcile_target_onto_remote(
    repo_root: Path,
    target: str,
    remote: str,
    *,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    conflict_resolution_config: UnifiedConfig | None = None,
    reclaim_target_worktree: bool = True,
) -> ReconciliationOutcome:
    """Rebase unpublished local target commits onto ``remote/target`` safely.

    The target must already be checked out in one clean worktree. Refusing
    every other shape is intentional: checking out or moving the feature
    worktree would make a remote retry alter unrelated local work.
    """
    try:
        owner = _reconciliation_owner(repo_root, target)
    except Exception:
        owner = None
    if owner is None:
        return ReconciliationOutcome(False, f"target '{target}' is unavailable for reconciliation")
    with integration_transaction(owner) as acquired:
        if not acquired or (owner != repo_root and retained_integration_reason(owner) is not None):
            return ReconciliationOutcome(False, "target worktree busy; reconciliation will retry")
        checked_owner, pre_target_sha, reason = _reconciliation_preconditions(
            repo_root,
            target,
            remote,
            reclaim_target_worktree=reclaim_target_worktree,
            expected_owner=owner,
        )
        if reason is not None or checked_owner != owner or pre_target_sha is None:
            return ReconciliationOutcome(
                False,
                reason or f"target '{target}' changed owner before reconciliation",
            )
        return _reconcile_owned_target(
            repo_root,
            owner,
            target,
            remote,
            pre_target_sha,
            rebase_stop_resolver,
            resolution_session_from_config(conflict_resolution_config)
            if conflict_resolution_config is not None
            else None,
        )


def _reconcile_owned_target(
    repo_root: Path,
    owner: Path,
    target: str,
    remote: str,
    pre_target_sha: str,
    rebase_stop_resolver: RebaseStopResolver | None,
    resolution_session: ResolutionSession | None,
) -> ReconciliationOutcome:
    """Reconcile an owned target while retaining recovery ownership on every failure."""
    record = IntegrationRecord(
        phase="integrating",
        target=target,
        pre_feature_sha=pre_target_sha,
        pre_target_sha=branch_sha(owner, f"{remote}/{target}"),
        operation_kind="target_reconcile",
        owning_worktree=str(owner),
        initiating_feature_sha=get_head_sha(repo_root)
        if owner.resolve() != repo_root.resolve()
        else None,
    )
    try:
        write_record(repo_root, record)
    except Exception as exc:
        return ReconciliationOutcome(
            False, f"reconciliation of {target} deferred: recovery record unavailable: {exc}"
        )
    try:
        outcome = rebase_onto(f"{remote}/{target}", repo_root=owner)
        if isinstance(outcome, (RebaseSuccess, RebaseNoOp)) and not rebase_in_progress(owner):
            return _clear_successful_reconciliation_record(repo_root, target, remote)
        if rebase_in_progress(owner) and rebase_stop_resolver is not None:
            return _resolve_owned_reconciliation(
                repo_root,
                owner,
                target,
                remote,
                record,
                rebase_stop_resolver,
                resolution_session,
            )
        if rebase_in_progress(owner):
            abort_rebase_discarding_progress(owner)
    except Exception as exc:
        return _abort_restore_or_retain_record(
            repo_root, owner, target, remote, pre_target_sha, str(exc)
        )
    return _abort_restore_or_retain_record(
        repo_root, owner, target, remote, pre_target_sha, "conflicted"
    )


def _resolve_owned_reconciliation(
    repo_root: Path,
    owner: Path,
    target: str,
    remote: str,
    record: IntegrationRecord,
    resolver: RebaseStopResolver,
    session: ResolutionSession | None,
) -> ReconciliationOutcome:
    """Retain every failure once a durable receipt grants the agent ownership."""
    _original, onto = current_rebase_identity(owner)
    write_record(
        repo_root,
        record.model_copy(update={"resolving_rebase": True, "pre_target_sha": onto}),
    )
    detail = "resolution interrupted"
    try:
        with bind_integration_record_root(owner, repo_root):
            resolved = _resolve_rebase_with_session(owner, f"{remote}/{target}", resolver, session)
        if resolved and not rebase_in_progress(owner):
            return _clear_successful_reconciliation_record(repo_root, target, remote)
    except Exception as exc:
        detail = str(exc)
    return ReconciliationOutcome(
        False,
        f"reconciliation of {target} with {remote}/{target} retained for recovery: {detail}",
    )


def _resolve_rebase_with_session(
    owner: Path,
    target: str,
    resolver: RebaseStopResolver,
    session: ResolutionSession | None,
) -> bool:
    """Preserve the legacy resolver seam when no typed config reached this caller."""
    if session is None:
        return resolve_rebase_in_progress(owner, target, resolver)
    return resolve_rebase_in_progress(owner, target, resolver, session=session)


def _clear_successful_reconciliation_record(
    repo_root: Path, target: str, remote: str
) -> ReconciliationOutcome:
    """Clear durable ownership only after reconciliation finished cleanly."""
    try:
        retained = read_record(repo_root)
        if retained is not None:
            finish_target_substep(repo_root, retained)
        else:
            clear_record(repo_root)
    except Exception as exc:
        return ReconciliationOutcome(
            False,
            f"reconciliation of {target} with {remote}/{target} retained for recovery: "
            f"record cleanup failed: {exc}",
        )
    return ReconciliationOutcome(True, "")


def _reconciliation_owner(repo_root: Path, target: str) -> Path | None:
    """Locate the worktree to lease before observing or reclaiming its contents."""
    verdict, owner = worktree_lookup(find_main_worktree_root(repo_root), target)
    return owner if verdict == WORKTREE_FOUND else None


def _reconciliation_preconditions(
    repo_root: Path,
    target: str,
    remote: str,
    *,
    reclaim_target_worktree: bool,
    expected_owner: Path,
) -> tuple[Path | None, str | None, str | None]:
    """Return a clean owning target worktree and its pre-rebase SHA."""
    try:
        verdict, owner = worktree_lookup(find_main_worktree_root(repo_root), target)
        if verdict != WORKTREE_FOUND or owner is None:
            return None, None, f"target '{target}' has no owning worktree for reconciliation"
        if owner != expected_owner:
            return None, None, f"target '{target}' changed owner before reconciliation"
        if not is_repo_clean(owner) and (
            not reclaim_target_worktree or reclaim_dirty_target_worktree(owner, target) is None
        ):
            return (
                None,
                None,
                f"target worktree is dirty; skipped reconciliation of {remote}/{target}",
            )
        pre_target_sha = branch_sha(owner, target)
    except Exception:
        return None, None, f"target '{target}' is unavailable for reconciliation"
    if pre_target_sha is None:
        return None, None, f"target '{target}' disappeared before reconciliation"
    return owner, pre_target_sha, None


def _abort_restore_or_retain_record(
    repo_root: Path,
    owner: Path,
    target: str,
    remote: str,
    pre_target_sha: str,
    detail: str,
) -> ReconciliationOutcome:
    """Abort a failed reconciliation and restore the target before clearing ownership."""
    try:
        if rebase_in_progress(owner):
            abort_rebase_discarding_progress(owner)
        if rebase_in_progress(owner):
            return ReconciliationOutcome(
                False,
                f"reconciliation of {target} with {remote}/{target} retained for recovery: {detail}",
            )
        if branch_sha(owner, target) != pre_target_sha:
            return ReconciliationOutcome(
                False,
                f"reconciliation of {target} with {remote}/{target} retained for recovery: {detail}; "
                "target moved during reconciliation",
            )
    except Exception as exc:
        return ReconciliationOutcome(
            False,
            f"reconciliation of {target} with {remote}/{target} retained for recovery: {detail}; "
            f"restore failed: {exc}",
        )
    try:
        retained = read_record(repo_root)
        if retained is not None:
            finish_target_substep(repo_root, retained)
        else:
            clear_record(repo_root)
    except Exception as exc:
        return ReconciliationOutcome(
            False,
            f"reconciliation of {target} with {remote}/{target} retained for recovery: {detail}; "
            f"record cleanup failed: {exc}",
        )
    return ReconciliationOutcome(
        False,
        f"reconciliation of {target} with {remote}/{target} "
        f"{CLEAN_ABORTED_RECONCILIATION_CONFLICT} {detail}",
        cleanly_aborted=True,
    )


__all__ = [
    "CLEAN_ABORTED_RECONCILIATION_CONFLICT",
    "ReconciliationOutcome",
    "reconcile_target_onto_remote",
]

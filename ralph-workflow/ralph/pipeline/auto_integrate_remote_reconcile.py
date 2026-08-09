"""Safe target-branch reconciliation for configured remote sync.

A diverged local target is rebased only in the clean worktree that owns the
target branch. This keeps checked-out indexes coherent and never rewrites the
fetched remote commit; a failed or conflicted rebase is aborted before the
normal local integration continues.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.merge import WORKTREE_FOUND, branch_sha, reset_hard, worktree_lookup
from ralph.git.operations import find_main_worktree_root, is_repo_clean
from ralph.git.rebase.rebase import (
    RebaseNoOp,
    RebaseSuccess,
    abort_rebase,
    rebase_in_progress,
    rebase_onto,
)
from ralph.pipeline._auto_integrate_reclaim import reclaim_dirty_target_worktree
from ralph.pipeline.auto_integrate_record import IntegrationRecord, clear_record, write_record
from ralph.pipeline.conflict_resolution.rebase_loop import (
    RebaseStopResolver,
    resolve_rebase_in_progress,
)

if TYPE_CHECKING:
    from pathlib import Path


def reconcile_target_onto_remote(
    repo_root: Path,
    target: str,
    remote: str,
    *,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    reclaim_target_worktree: bool = True,
) -> tuple[bool, str]:
    """Rebase unpublished local target commits onto ``remote/target`` safely.

    The target must already be checked out in one clean worktree.  Refusing
    every other shape is intentional: checking out or moving the feature
    worktree would make a remote retry alter unrelated local work.
    """
    owner, pre_target_sha, reason = _reconciliation_preconditions(
        repo_root, target, remote, reclaim_target_worktree=reclaim_target_worktree
    )
    if reason is not None or owner is None or pre_target_sha is None:
        return False, reason or f"target '{target}' is unavailable for reconciliation"
    write_record(
        repo_root,
        IntegrationRecord(
            phase="integrating",
            target=target,
            pre_feature_sha=pre_target_sha,
            pre_target_sha=pre_target_sha,
            operation_kind="target_reconcile",
            owning_worktree=str(owner),
        ),
    )
    try:
        outcome = rebase_onto(f"{remote}/{target}", repo_root=owner)
        if isinstance(outcome, (RebaseSuccess, RebaseNoOp)):
            clear_record(repo_root)
            return True, ""
        if (
            rebase_in_progress(owner)
            and rebase_stop_resolver is not None
            and resolve_rebase_in_progress(owner, f"{remote}/{target}", rebase_stop_resolver)
        ):
            clear_record(repo_root)
            return True, ""
        if rebase_in_progress(owner):
            abort_rebase(repo_root=owner)
    except Exception as exc:
        return _abort_restore_or_retain_record(
            repo_root, owner, target, remote, pre_target_sha, str(exc)
        )
    return _abort_restore_or_retain_record(
        repo_root, owner, target, remote, pre_target_sha, "conflicted"
    )


def _reconciliation_preconditions(
    repo_root: Path,
    target: str,
    remote: str,
    *,
    reclaim_target_worktree: bool,
) -> tuple[Path | None, str | None, str | None]:
    """Return a clean owning target worktree and its pre-rebase SHA."""
    try:
        verdict, owner = worktree_lookup(find_main_worktree_root(repo_root), target)
    except Exception:
        return None, None, f"target '{target}' is unavailable for reconciliation"
    if verdict != WORKTREE_FOUND or owner is None:
        return None, None, f"target '{target}' has no owning worktree for reconciliation"
    if not is_repo_clean(owner) and (
        not reclaim_target_worktree or reclaim_dirty_target_worktree(owner, target) is None
    ):
        return None, None, f"target worktree is dirty; skipped reconciliation of {remote}/{target}"
    pre_target_sha = branch_sha(owner, target)
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
) -> tuple[bool, str]:
    """Abort a failed reconciliation and restore the target before clearing ownership."""
    try:
        if rebase_in_progress(owner):
            abort_rebase(repo_root=owner)
        if rebase_in_progress(owner):
            return (
                False,
                f"reconciliation of {target} with {remote}/{target} retained for recovery: {detail}",
            )
        current_target_sha = branch_sha(owner, target)
        if current_target_sha != pre_target_sha:
            return (
                False,
                f"reconciliation of {target} with {remote}/{target} retained for recovery: {detail}; "
                "target moved during reconciliation",
            )
        reset_hard(owner, pre_target_sha)
        if branch_sha(owner, target) != pre_target_sha:
            return (
                False,
                f"reconciliation of {target} with {remote}/{target} retained for recovery: {detail}; "
                "target restore could not be verified",
            )
    except Exception as exc:
        return (
            False,
            f"reconciliation of {target} with {remote}/{target} retained for recovery: {detail}; "
            f"restore failed: {exc}",
        )
    clear_record(repo_root)
    return False, f"reconciliation of {target} with {remote}/{target} conflicted: {detail}"


__all__ = ["reconcile_target_onto_remote"]

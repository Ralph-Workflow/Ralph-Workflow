"""Safe target-branch reconciliation for opt-in remote sync.

A diverged local target is rebased only in the clean worktree that owns the
target branch.  This keeps checked-out indexes coherent and never rewrites the
fetched remote commit; a failed or conflicted rebase is aborted before the
normal local integration continues.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.merge import WORKTREE_FOUND, worktree_lookup
from ralph.git.operations import find_main_worktree_root, is_repo_clean
from ralph.git.rebase.rebase import (
    RebaseNoOp,
    RebaseSuccess,
    abort_rebase,
    rebase_in_progress,
    rebase_onto,
)

if TYPE_CHECKING:
    from pathlib import Path


def reconcile_target_onto_remote(repo_root: Path, target: str, remote: str) -> tuple[bool, str]:
    """Rebase unpublished local target commits onto ``remote/target`` safely.

    The target must already be checked out in one clean worktree.  Refusing
    every other shape is intentional: checking out or moving the feature
    worktree would make a remote retry alter unrelated local work.
    """
    verdict, owner = worktree_lookup(find_main_worktree_root(repo_root), target)
    if verdict != WORKTREE_FOUND or owner is None:
        return False, f"target '{target}' has no owning worktree for reconciliation"
    if not is_repo_clean(owner):
        return False, f"target worktree is dirty; skipped reconciliation of {remote}/{target}"
    try:
        outcome = rebase_onto(f"{remote}/{target}", repo_root=owner)
        if isinstance(outcome, (RebaseSuccess, RebaseNoOp)):
            return True, ""
        if rebase_in_progress(owner):
            abort_rebase(repo_root=owner)
    except Exception as exc:
        return False, f"reconciliation of {target} with {remote}/{target} failed: {exc}"
    return False, f"reconciliation of {target} with {remote}/{target} conflicted"


__all__ = ["reconcile_target_onto_remote"]

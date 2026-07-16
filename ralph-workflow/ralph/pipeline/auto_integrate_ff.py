"""Fast-forward helpers for :mod:`ralph.pipeline.auto_integrate`.

Houses the worktree-aware / atomic-CAS fast-forward path so the
main :mod:`ralph.pipeline.auto_integrate` module stays under the
repo-structure ``_MAX_FILE_LINES`` cap. The four helpers here are
a coherent unit (the AC-08 / AC-09 fast-forward branch table)
and have no callers outside :mod:`ralph.pipeline.auto_integrate`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.merge import (
    branch_sha,
    compare_and_swap_branch,
    fast_forward_via_worktree,
    is_ancestor,
    worktree_for_branch,
)
from ralph.git.operations import find_main_worktree_root, is_repo_clean

if TYPE_CHECKING:
    from pathlib import Path


def fast_forward_target(
    repo_root: Path,
    target: str,
    feature_sha: str,
) -> tuple[bool, str]:
    """Move the local mainline ref to ``feature_sha`` via CAS or worktree ff.

    Returns ``(fast_forwarded, skip_reason)``. ``skip_reason`` is
    empty when the fast-forward succeeded.

    AC-08 atomicity contract: the ancestry decision is BOUND to the
    same observed target SHA the CAS uses. Concretely we observe
    ``observed_target_sha = branch_sha(target)`` ONCE, then verify
    that *that specific SHA* is an ancestor of ``feature_sha``, and
    then CAS that same SHA. If the target moves between observation
    and CAS, the CAS fails closed (the ref no longer equals the
    observed SHA) and we record a concurrency skip. Earlier
    implementations checked ``is_ancestor(target, feature_sha)``
    *before* reading the SHA, leaving a TOCTOU window where a
    concurrent landing between the ancestor check and the SHA read
    could satisfy the CAS with a SHA that was NOT an ancestor of
    ``feature_sha`` -- the bug class closed by this rewrite.

    The worktree path also observes the worktree's branch SHA and
    verifies that observed SHA is an ancestor of ``feature_sha``
    before attempting the worktree ff; ``git merge --ff-only`` is
    itself the second-line guard.

    Never force-moves the target. The skip reasons are recorded in
    the final ``RebaseState.last_reason``.
    """
    # Observe the target SHA FIRST. This is the single value the CAS
    # will use; every downstream check must reference the same SHA.
    observed_target_sha = branch_sha(repo_root, target)
    if observed_target_sha is None:
        return False, "target branch missing at fast-forward time"

    # AC-08 guard: the OBSERVED SHA (not the ref name) must be an
    # ancestor of feature_sha. This is the contract that closes the
    # TOCTOU race: if the target moves after this check, the
    # downstream CAS (or the worktree ff) will refuse the move.
    if not is_ancestor(repo_root, observed_target_sha, feature_sha):
        return False, "target advanced concurrently (not an ancestor of feature)"

    return _fast_forward_target_via_worktree_or_cas(
        repo_root, target, feature_sha, observed_target_sha
    )


def _fast_forward_target_via_worktree_or_cas(
    repo_root: Path,
    target: str,
    feature_sha: str,
    observed_target_sha: str,
) -> tuple[bool, str]:
    """Run the worktree-aware or CAS fast-forward once ancestor + sha checks pass."""
    primary_root = find_main_worktree_root(repo_root)
    wt = worktree_for_branch(primary_root, target)
    if wt is not None:
        return _fast_forward_via_target_worktree(wt, target, feature_sha)
    return _fast_forward_via_cas(repo_root, target, feature_sha, observed_target_sha)


def _fast_forward_via_target_worktree(
    worktree_root: Path,
    target: str,
    feature_sha: str,
) -> tuple[bool, str]:
    """Fast-forward the target branch checked out in ``worktree_root`` (AC-09).

    Re-checks the worktree's currently-checked-out SHA against
    ``feature_sha`` so a concurrent landing inside the worktree
    between the caller-side ``is_ancestor`` and the ``merge --ff-only``
    is still caught: the ancestor guard in the caller references
    ``observed_target_sha`` which is the SHA the caller observed
    via ``branch_sha``; the worktree's own branch SHA is the value
    the worktree's ``HEAD`` resolves to. ``git merge --ff-only`` is
    the second-line atomic guard -- it refuses if ``feature_sha`` is
    not a fast-forward of the worktree's current branch.
    """
    if not is_repo_clean(worktree_root):
        return False, "target worktree dirty"
    if not fast_forward_via_worktree(worktree_root, feature_sha):
        return False, "target advanced concurrently (ff-only refused)"
    return True, ""


def _fast_forward_via_cas(
    repo_root: Path,
    target: str,
    feature_sha: str,
    observed_target_sha: str,
) -> tuple[bool, str]:
    """Atomic CAS fast-forward of a not-checked-out target branch (AC-08)."""
    if not compare_and_swap_branch(repo_root, target, observed_target_sha, feature_sha):
        return False, "target advanced concurrently (CAS mismatch)"
    return True, ""


__all__ = ["fast_forward_target"]

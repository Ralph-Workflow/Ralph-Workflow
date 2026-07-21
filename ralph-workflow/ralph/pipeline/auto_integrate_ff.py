"""Fast-forward helpers for :mod:`ralph.pipeline.auto_integrate`.

Houses the worktree-aware / atomic-CAS fast-forward path so the
main :mod:`ralph.pipeline.auto_integrate` module stays under the
repo-structure ``_MAX_FILE_LINES`` cap. The four helpers here are
a coherent unit (the AC-08 / AC-09 fast-forward branch table)
and have no callers outside :mod:`ralph.pipeline.auto_integrate`.

When the target branch is checked out somewhere, the landing order is
``git merge --ff-only`` in that worktree FIRST, then the atomic CAS as
a fallback. ``merge --ff-only`` advances the ref, the index and the
working tree together and exits non-zero WITHOUT mutating anything
when local changes would be overwritten; the CAS advances only the
shared ref, leaving that checkout's index behind so ``git status``
there describes the freshly landed work as a local reverse diff.
Trying the consistent path first is therefore strictly safer, and the
CAS still keeps the ref moving when git refuses the merge.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import (
    branch_sha,
    compare_and_swap_branch,
    fast_forward_via_worktree,
    is_ancestor,
    worktree_for_branch,
)
from ralph.git.operations import find_main_worktree_root

_TARGET_MISSING = "target branch missing at fast-forward time"
_TARGET_NOT_ANCESTOR = "target advanced concurrently (not an ancestor of feature)"
_TARGET_FF_REFUSED = "target advanced concurrently (ff-only refused)"
_TARGET_CAS_MISMATCH = "target advanced concurrently (CAS mismatch)"
_RETRYABLE_REASONS = frozenset(
    {_TARGET_NOT_ANCESTOR, _TARGET_FF_REFUSED, _TARGET_CAS_MISMATCH}
)

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

    The worktree path always tries ``git merge --ff-only`` first so
    the checkout's ref, index and working tree advance together, and
    falls back to the same observed-SHA CAS only when git refuses.
    Neither path can overwrite a concurrent landing or the worktree's
    uncommitted work.

    Never force-moves the target. The skip reasons are recorded in
    the final ``RebaseState.last_reason``.
    """
    # Observe the target SHA FIRST. This is the single value the CAS
    # will use; every downstream check must reference the same SHA.
    observed_target_sha = branch_sha(repo_root, target)
    if observed_target_sha is None:
        return False, _TARGET_MISSING

    # AC-08 guard: the OBSERVED SHA (not the ref name) must be an
    # ancestor of feature_sha. This is the contract that closes the
    # TOCTOU race: if the target moves after this check, the
    # downstream CAS (or the worktree ff) will refuse the move.
    if not is_ancestor(repo_root, observed_target_sha, feature_sha):
        return False, _TARGET_NOT_ANCESTOR

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
        return _fast_forward_via_target_worktree(
            repo_root, wt, target, feature_sha, observed_target_sha
        )
    return _fast_forward_via_cas(repo_root, target, feature_sha, observed_target_sha)


def _fast_forward_via_target_worktree(
    repo_root: Path,
    worktree_root: Path,
    target: str,
    feature_sha: str,
    observed_target_sha: str,
) -> tuple[bool, str]:
    """Fast-forward the target branch checked out in ``worktree_root`` (AC-09).

    ``git merge --ff-only`` is attempted first regardless of how dirty
    that checkout is: it is its own guard, refusing with a non-zero exit
    and no mutation when local changes would be overwritten or when
    ``feature_sha`` is not a descendant. Only once git has refused do we
    fall back to the CAS, which advances the shared ref alone and so
    leaves that checkout's index behind — a transient but operator-
    visible state, hence the WARN.
    """
    if fast_forward_via_worktree(worktree_root, feature_sha):
        return True, ""
    landed, reason = _fast_forward_via_cas(
        repo_root, target, feature_sha, observed_target_sha
    )
    if landed:
        logger.warning(
            "auto_integrate: advanced '{}' by ref while its worktree at {} "
            "could not fast-forward; that checkout's index is now behind",
            target,
            worktree_root,
        )
    return landed, reason


def _fast_forward_via_cas(
    repo_root: Path,
    target: str,
    feature_sha: str,
    observed_target_sha: str,
) -> tuple[bool, str]:
    """Atomic CAS fast-forward of a not-checked-out target branch (AC-08)."""
    if not compare_and_swap_branch(repo_root, target, observed_target_sha, feature_sha):
        return False, _TARGET_CAS_MISMATCH
    return True, ""


def is_retryable_fast_forward_failure(reason: str) -> bool:
    """Whether a failed fast-forward reflects a transient target move."""
    return reason in _RETRYABLE_REASONS


__all__ = ["fast_forward_target", "is_retryable_fast_forward_failure"]

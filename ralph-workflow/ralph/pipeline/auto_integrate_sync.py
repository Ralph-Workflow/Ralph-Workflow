"""Bounded, read-only refresh of the auto-integrate target from origin.

This is the ONLY code path in ``ralph/`` that contacts a remote, and it
contacts it read-only: ``git fetch`` and nothing else. The AC-10
local-only contract stated in :mod:`ralph.git.merge` is preserved --
this module never pushes and never force-moves a ref.

It exists because the mainline pointer is assumed to be moving
underneath us: other agents land work on the same branch continuously.
In the linked-worktree topology (every agent sharing one git common
directory) ``refs/heads/<target>`` is shared and always read fresh, so
this refresh is a no-op. In a clone topology the local target ref would
otherwise go permanently stale, and every integration would be computed
against a mainline that moved long ago.

Every failure is fail-open: an absent remote, an unreachable host, a
timeout or a diverged history all return False and leave the repository
untouched, so integration degrades to the previous local-only behaviour
rather than failing the run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import (
    WORKTREE_FOUND,
    WORKTREE_QUERY_FAILED,
    branch_sha,
    compare_and_swap_branch,
    fast_forward_via_worktree,
    is_ancestor,
    worktree_lookup,
)
from ralph.git.operations import find_main_worktree_root
from ralph.git.subprocess_runner import GitRunOptions, run_git

if TYPE_CHECKING:
    from pathlib import Path

#: Typed outcomes of :func:`refresh_target_from_remote`. The refresh is
#: fail-open by design -- an unreachable remote degrades to local-only
#: integration rather than failing the run -- so the outcome is the ONLY
#: signal that tells an operator whether the mainline pointer the
#: integration landed against was actually fresh. It is recorded on
#: ``RebaseState.last_refresh`` and rendered in the auto-integrate line.
REFRESH_DISABLED = "fetch disabled"
REFRESH_NO_ORIGIN = "no origin remote"
REFRESH_UNREACHABLE = "origin unreachable"
REFRESH_NO_REMOTE_BRANCH = "no remote branch"
REFRESH_NO_LOCAL_BRANCH = "no local branch"
REFRESH_ALREADY_CURRENT = "already current"
REFRESH_DIVERGED = "diverged from origin"
REFRESH_REFRESHED = "refreshed from origin"
REFRESH_RACE_LOST = "lost a concurrent refresh race"

__all__ = [
    "REFRESH_ALREADY_CURRENT",
    "REFRESH_DISABLED",
    "REFRESH_DIVERGED",
    "REFRESH_NO_LOCAL_BRANCH",
    "REFRESH_NO_ORIGIN",
    "REFRESH_NO_REMOTE_BRANCH",
    "REFRESH_RACE_LOST",
    "REFRESH_REFRESHED",
    "REFRESH_UNREACHABLE",
    "refresh_target_from_remote",
]


def refresh_target_from_remote(
    repo_root: Path,
    target: str,
    *,
    timeout_seconds: float,
) -> str:
    """Best-effort refresh of ``refs/heads/<target>`` from ``origin``.

    Returns one of the ``REFRESH_*`` outcomes. Never raises, never
    force-moves a ref, never pushes. The ref moves only when the
    remote-tracking ref is STRICTLY ahead, i.e. the local ref holds no
    commit the remote lacks; a diverged remote is left alone.

    The outcome replaces the previous bare ``bool`` because every
    non-advancing case used to collapse into the same ``False``: a
    repository with no origin, an unreachable host and a mainline that
    was genuinely already current were indistinguishable, so an
    integration computed against a stale pointer looked exactly like a
    healthy one.
    """
    if not _has_origin(repo_root):
        logger.debug("auto_integrate: no origin remote; skipping target refresh")
        return REFRESH_NO_ORIGIN

    fetched = _fetch_target(repo_root, target, timeout_seconds)

    outcome, advance = _pending_advance(repo_root, target, fetched=fetched)
    if advance is None:
        return outcome if outcome is not None else REFRESH_ALREADY_CURRENT
    local_sha, remote_sha = advance

    if not _advance_local_ref(repo_root, target, local_sha, remote_sha):
        logger.debug("auto_integrate: refresh of '{}' lost a concurrent race", target)
        return REFRESH_RACE_LOST

    logger.info(
        "auto_integrate: refreshed '{}' from origin ({} -> {})",
        target,
        local_sha,
        remote_sha,
    )
    return REFRESH_REFRESHED


def _pending_advance(
    repo_root: Path, target: str, *, fetched: bool
) -> tuple[str | None, tuple[str, str] | None]:
    """Decide whether the local ref may be advanced, and why not otherwise.

    Returns ``(terminal_outcome, advance)`` where exactly one slot is
    non-``None``: ``advance`` carries ``(local_sha, remote_sha)`` when
    the ref may move, and ``terminal_outcome`` names the reason it may
    not -- no remote-tracking ref, no local branch, already in sync, or
    a remote that is not STRICTLY ahead. The strict-ancestor
    requirement is what makes this safe: a diverged remote would need a
    force-move to apply, and this module never forces.
    """
    remote_sha = _remote_tracking_sha(repo_root, target)
    if remote_sha is None:
        logger.debug(
            "auto_integrate: no remote-tracking ref for '{}'; nothing to refresh",
            target,
        )
        # A failed fetch with no tracking ref means we never saw the
        # remote pointer at all; that is materially different from a
        # successful fetch of a branch the remote does not carry.
        return (
            REFRESH_NO_REMOTE_BRANCH if fetched else REFRESH_UNREACHABLE,
            None,
        )

    local_sha = branch_sha(repo_root, target)
    if local_sha is None:
        # Materializing a missing local branch is _ensure_local_origin_branch's
        # job, not ours.
        logger.debug("auto_integrate: local branch '{}' absent; not refreshing", target)
        return REFRESH_NO_LOCAL_BRANCH, None
    if local_sha == remote_sha:
        logger.debug("auto_integrate: '{}' already matches origin", target)
        return REFRESH_ALREADY_CURRENT, None
    if not is_ancestor(repo_root, local_sha, remote_sha):
        logger.debug(
            "auto_integrate: origin/{} diverged from the local ref; not force-moving",
            target,
        )
        return REFRESH_DIVERGED, None
    return None, (local_sha, remote_sha)


def _has_origin(repo_root: Path) -> bool:
    """True when an ``origin`` remote is configured; no network call."""
    result = run_git(
        ("remote", "get-url", "origin"),
        cwd=repo_root,
        label="git-origin-url",
    )
    return result.returncode == 0


def _fetch_target(repo_root: Path, target: str, timeout_seconds: float) -> bool:
    """Fetch exactly one branch from origin, bounded and fail-open.

    Returns whether the fetch itself succeeded. A failure does NOT stop
    the refresh: an already-fetched ``refs/remotes/origin/<target>`` is
    still worth reconciling against even when this particular fetch
    could not reach the remote. The boolean only lets the caller tell
    an unreachable remote apart from a remote that simply does not
    carry the branch. ``run_git`` already forces
    ``GIT_TERMINAL_PROMPT=0`` and ``GCM_INTERACTIVE=Never``, so a
    credential prompt fails fast rather than hanging.
    """
    try:
        result = run_git(
            ("fetch", "--quiet", "origin", "--", target),
            cwd=repo_root,
            label="git-fetch-target",
            options=GitRunOptions(timeout=timeout_seconds),
        )
    except Exception as fetch_exc:
        logger.debug("auto_integrate: fetch of '{}' failed: {}", target, fetch_exc)
        return False
    return result.returncode == 0


def _remote_tracking_sha(repo_root: Path, target: str) -> str | None:
    """SHA of ``refs/remotes/origin/<target>``, or None when absent."""
    result = run_git(
        ("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{target}"),
        cwd=repo_root,
        label="git-remote-tracking-sha",
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _advance_local_ref(
    repo_root: Path, target: str, local_sha: str, remote_sha: str
) -> bool:
    """Move the local target ref forward using the landing primitives.

    Reuses the same worktree-aware and compare-and-swap paths as the
    normal fast-forward, so a concurrent mover wins and this refresh
    fails closed rather than clobbering. ``git merge --ff-only`` is
    tried first whenever the branch is checked out, because it keeps
    that checkout's index and working tree consistent with the ref;
    it is its own guard and refuses without mutating anything when
    local changes would be overwritten. The CAS is the fallback.

    A FAILED worktree query is NOT treated as "checked out nowhere":
    moving the shared ref under a live checkout leaves that worktree's
    index describing the freshly landed work as a local reverse diff.
    The refresh is best-effort anyway, so an unanswerable query simply
    declines to move the ref this time round.
    """
    verdict, worktree = worktree_lookup(find_main_worktree_root(repo_root), target)
    if verdict == WORKTREE_QUERY_FAILED:
        logger.warning(
            "auto_integrate: could not determine whether '{}' is checked out; "
            "skipping the origin refresh of the shared ref",
            target,
        )
        return False
    if (
        verdict == WORKTREE_FOUND
        and worktree is not None
        and fast_forward_via_worktree(worktree, remote_sha)
    ):
        return True
    return compare_and_swap_branch(repo_root, target, local_sha, remote_sha)

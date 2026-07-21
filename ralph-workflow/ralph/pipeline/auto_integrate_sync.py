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

import contextlib
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import (
    branch_sha,
    compare_and_swap_branch,
    fast_forward_via_worktree,
    is_ancestor,
    worktree_for_branch,
)
from ralph.git.operations import find_main_worktree_root, is_repo_clean
from ralph.git.subprocess_runner import GitRunOptions, run_git

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["refresh_target_from_remote"]


def refresh_target_from_remote(
    repo_root: Path,
    target: str,
    *,
    timeout_seconds: float,
) -> bool:
    """Best-effort refresh of ``refs/heads/<target>`` from ``origin``.

    Returns True when the local target ref was advanced. Never raises,
    never force-moves a ref, never pushes. The ref moves only when the
    remote-tracking ref is STRICTLY ahead, i.e. the local ref holds no
    commit the remote lacks; a diverged remote is left alone.
    """
    if not _has_origin(repo_root):
        logger.debug("auto_integrate: no origin remote; skipping target refresh")
        return False

    _fetch_target(repo_root, target, timeout_seconds)

    advance = _pending_advance(repo_root, target)
    if advance is None:
        return False
    local_sha, remote_sha = advance

    if not _advance_local_ref(repo_root, target, local_sha, remote_sha):
        logger.debug("auto_integrate: refresh of '{}' lost a concurrent race", target)
        return False

    logger.info(
        "auto_integrate: refreshed '{}' from origin ({} -> {})",
        target,
        local_sha,
        remote_sha,
    )
    return True


def _pending_advance(repo_root: Path, target: str) -> tuple[str, str] | None:
    """Return ``(local_sha, remote_sha)`` when the ref may be advanced.

    ``None`` whenever moving the local ref would be wrong or
    unnecessary: no remote-tracking ref, no local branch, already in
    sync, or a remote that is not STRICTLY ahead. The strict-ancestor
    requirement is what makes this safe -- a diverged remote would need
    a force-move to apply, and this module never forces.
    """
    remote_sha = _remote_tracking_sha(repo_root, target)
    if remote_sha is None:
        logger.debug(
            "auto_integrate: no remote-tracking ref for '{}'; nothing to refresh",
            target,
        )
        return None

    local_sha = branch_sha(repo_root, target)
    if local_sha is None:
        # Materializing a missing local branch is _ensure_local_origin_branch's
        # job, not ours.
        logger.debug("auto_integrate: local branch '{}' absent; not refreshing", target)
        return None
    if local_sha == remote_sha:
        logger.debug("auto_integrate: '{}' already matches origin", target)
        return None
    if not is_ancestor(repo_root, local_sha, remote_sha):
        logger.debug(
            "auto_integrate: origin/{} diverged from the local ref; not force-moving",
            target,
        )
        return None
    return local_sha, remote_sha


def _has_origin(repo_root: Path) -> bool:
    """True when an ``origin`` remote is configured; no network call."""
    result = run_git(
        ("remote", "get-url", "origin"),
        cwd=repo_root,
        label="git-origin-url",
    )
    return result.returncode == 0


def _fetch_target(repo_root: Path, target: str, timeout_seconds: float) -> None:
    """Fetch exactly one branch from origin, bounded and fail-open.

    A non-zero return code is ignored on purpose: an already-fetched
    ``refs/remotes/origin/<target>`` is still worth reconciling against
    even when this particular fetch could not reach the remote.
    ``run_git`` already forces ``GIT_TERMINAL_PROMPT=0`` and
    ``GCM_INTERACTIVE=Never``, so a credential prompt fails fast rather
    than hanging.
    """
    with contextlib.suppress(Exception):
        run_git(
            ("fetch", "--quiet", "origin", "--", target),
            cwd=repo_root,
            label="git-fetch-target",
            options=GitRunOptions(timeout=timeout_seconds),
        )


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
    fails closed rather than clobbering.
    """
    worktree = worktree_for_branch(find_main_worktree_root(repo_root), target)
    if worktree is not None and is_repo_clean(worktree):
        return fast_forward_via_worktree(worktree, remote_sha)
    return compare_and_swap_branch(repo_root, target, local_sha, remote_sha)

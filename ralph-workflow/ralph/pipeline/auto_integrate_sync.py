"""Bounded, OBSERVE-ONLY freshness probe for the auto-integrate target.

This is the ONLY code path in ``ralph/`` that may contact a remote, and
it contacts it strictly read-only: ``git fetch`` updates
``refs/remotes/origin/<target>`` and NOTHING else. Remote state must
never affect a local rebase, merge or landing: the module never moves
``refs/heads/<target>``, never touches a worktree, never pushes. The
authoritative mainline pointer is always the LOCAL ref -- in the
linked-worktree fleet this feature exists for, every agent shares one
git common directory and sibling agents advance ``refs/heads/<target>``
directly, so re-reading that ref IS the freshness primitive.

The refresh used to fast-forward the local target ref from a strictly-
ahead ``origin/<target>`` (a clone-topology convenience). That let a
remote nobody asked about rewrite the base of every local rebase, so
the advance was removed: an origin observed ahead is now REPORTED
(:data:`REFRESH_ORIGIN_AHEAD`) and the local ref is left alone.

Every failure is fail-open: an absent remote, an unreachable host, a
timeout or a diverged history all leave the repository untouched, so
integration proceeds against the local ref exactly as it would have
without the probe.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import branch_sha, is_ancestor
from ralph.git.subprocess_runner import GitRunOptions, run_git

if TYPE_CHECKING:
    from pathlib import Path

#: Typed outcomes of :func:`refresh_target_from_remote`. The refresh is
#: fail-open by design -- an unreachable remote degrades to local-only
#: integration rather than failing the run -- so the outcome is the ONLY
#: signal that tells an operator how the mainline pointer the
#: integration used was observed. It is recorded on
#: ``RebaseState.last_refresh`` and rendered in the auto-integrate line.
REFRESH_DISABLED = "fetch disabled"
REFRESH_NO_ORIGIN = "no origin remote"
REFRESH_UNREACHABLE = "origin unreachable"
REFRESH_NO_REMOTE_BRANCH = "no remote branch"
REFRESH_NO_LOCAL_BRANCH = "no local branch"
REFRESH_ALREADY_CURRENT = "already current"
REFRESH_DIVERGED = "diverged from origin"
#: Historical outcome retained for records persisted by earlier
#: versions, which fast-forwarded the local ref from origin. The
#: observe-only refresh never produces it: remote state no longer
#: moves any local ref.
REFRESH_REFRESHED = "refreshed from origin"
#: Origin holds commits the local ref lacks. Observation ONLY: the
#: local ref is authoritative for every local rebase and landing
#: decision, so nothing is applied and nothing local moves.
REFRESH_ORIGIN_AHEAD = "origin ahead (local ref kept)"
#: The target pointer was re-observed from the SHARED ref store rather
#: than from a remote. This is the normal outcome for Ralph's own
#: linked-worktree fleet, where every agent's ``wt-0NN-*`` worktree
#: shares one git common directory and sibling agents advance the local
#: ``refs/heads/<target>`` directly, with no ``origin`` involved.
#: Distinct from :data:`REFRESH_NO_ORIGIN`, which means the target
#: could not be observed AT ALL -- neither remotely nor locally.
REFRESH_LOCAL_FLEET = "local fleet"
#: The boundary refresh throttle declined this probe, so NO refresh was
#: taken. Recorded rather than left as ``None`` because a boundary
#: decided from a pointer nobody re-read this round is exactly as
#: unverifiable as one whose refresh failed -- the operator has to be
#: able to tell that case from a genuinely fresh one.
REFRESH_SUPPRESSED = "refresh suppressed by throttle"

__all__ = [
    "REFRESH_ALREADY_CURRENT",
    "REFRESH_DISABLED",
    "REFRESH_DIVERGED",
    "REFRESH_LOCAL_FLEET",
    "REFRESH_NO_LOCAL_BRANCH",
    "REFRESH_NO_ORIGIN",
    "REFRESH_NO_REMOTE_BRANCH",
    "REFRESH_ORIGIN_AHEAD",
    "REFRESH_REFRESHED",
    "REFRESH_SUPPRESSED",
    "REFRESH_UNREACHABLE",
    "observe_target_sha",
    "refresh_target_from_remote",
]


def observe_target_sha(repo_root: Path, target: str) -> str | None:
    """Re-read ``refs/heads/<target>`` from the shared ref store.

    Returns the SHA, or ``None`` when the branch does not exist.

    Branch refs live in the git COMMON directory, not in the per-worktree
    git dir, so this read observes updates made by any sibling worktree
    in the fleet -- including ones that landed microseconds ago. That is
    what makes it the correct freshness primitive for the local fleet:
    the pointer other agents advance is already the one this call reads.

    Never raises; an unusable repository reports ``None`` so the
    fail-open refresh contract is preserved.
    """
    try:
        result = run_git(
            ("rev-parse", "--verify", "--quiet", f"refs/heads/{target}"),
            cwd=repo_root,
            label="git-observe-target-sha",
        )
    except Exception as observe_exc:
        logger.debug(
            "auto_integrate: could not observe '{}': {}", target, observe_exc
        )
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def refresh_target_from_remote(
    repo_root: Path,
    target: str,
    *,
    timeout_seconds: float,
) -> str:
    """Observe the freshness of ``refs/heads/<target>``, fetching origin if any.

    Returns one of the ``REFRESH_*`` outcomes. Never raises, never
    pushes, and NEVER moves a local ref: the fetch updates only the
    remote-tracking ref, and the comparison below is pure reporting.
    Remote state must not affect local rebase operations, so an origin
    observed strictly ahead is recorded as
    :data:`REFRESH_ORIGIN_AHEAD` and the local ref -- the authoritative
    pointer every local decision uses -- is left exactly where the
    local fleet put it.
    """
    if not _has_origin(repo_root):
        return _observe_without_origin(repo_root, target)

    if not _fetch_target(repo_root, target, timeout_seconds):
        # A cached ``refs/remotes/origin/<target>`` from an earlier,
        # successful fetch is NOT evidence of a fresh origin read: it
        # can be arbitrarily old. Reporting anything but UNREACHABLE
        # here would assert a freshness this call never established.
        logger.debug(
            "auto_integrate: fetch of '{}' failed; origin unreachable",
            target,
        )
        return REFRESH_UNREACHABLE

    return _classify_remote_position(repo_root, target)


def _observe_without_origin(repo_root: Path, target: str) -> str:
    """Report freshness for a repository that has no ``origin`` remote.

    'No origin' is not the same as 'no fresh pointer'. Ralph's own agent
    fleet runs as linked worktrees over one git common directory with no
    remote at all, and sibling agents advance ``refs/heads/<target>``
    there continuously. So the local ref is re-observed and
    :data:`REFRESH_LOCAL_FLEET` is reported.
    :data:`REFRESH_NO_ORIGIN` survives for the genuinely unobservable
    case: no remote AND no such local branch.
    """
    observed = observe_target_sha(repo_root, target)
    if observed is None:
        logger.debug(
            "auto_integrate: no origin remote and no local '{}'; "
            "nothing to observe",
            target,
        )
        return REFRESH_NO_ORIGIN
    logger.debug(
        "auto_integrate: no origin remote; observed local '{}' at {}",
        target,
        observed,
    )
    return REFRESH_LOCAL_FLEET


def _classify_remote_position(repo_root: Path, target: str) -> str:
    """Name where origin sits relative to the authoritative local ref.

    Pure observation over refs a successful fetch just updated: no
    branch in this function mutates anything. The strict-ancestor probe
    distinguishes an origin that is simply ahead (reported, not
    applied) from one that diverged; both leave the local ref alone,
    because the local ref is the pointer local rebases are FOR.
    """
    remote_sha = _remote_tracking_sha(repo_root, target)
    if remote_sha is None:
        # Reached only after a SUCCESSFUL fetch, so the remote
        # genuinely does not carry this branch -- the unreachable case
        # returned before this function was called.
        logger.debug(
            "auto_integrate: no remote-tracking ref for '{}'; nothing to observe",
            target,
        )
        return REFRESH_NO_REMOTE_BRANCH

    local_sha = branch_sha(repo_root, target)
    if local_sha is None:
        logger.debug("auto_integrate: local branch '{}' absent", target)
        return REFRESH_NO_LOCAL_BRANCH
    if local_sha == remote_sha:
        logger.debug("auto_integrate: '{}' already matches origin", target)
        return REFRESH_ALREADY_CURRENT
    if not is_ancestor(repo_root, local_sha, remote_sha):
        logger.debug(
            "auto_integrate: origin/{} diverged from the local ref; local kept",
            target,
        )
        return REFRESH_DIVERGED
    logger.debug(
        "auto_integrate: origin/{} is ahead of the local ref; local kept "
        "({} != {})",
        target,
        local_sha,
        remote_sha,
    )
    return REFRESH_ORIGIN_AHEAD


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

    Returns whether the fetch itself succeeded. The fetch touches ONLY
    ``refs/remotes/origin/<target>``; no local ref is examined, moved
    or created here. A failure ENDS the refresh with
    :data:`REFRESH_UNREACHABLE`. ``run_git`` already forces
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

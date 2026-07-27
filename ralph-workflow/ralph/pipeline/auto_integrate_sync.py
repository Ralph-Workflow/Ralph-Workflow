"""Bounded, OBSERVE-ONLY freshness probe for the auto-integrate target.

This is the ONLY code path in ``ralph/`` that may contact a remote, and
it contacts it strictly read-only: ``git fetch`` updates
``refs/remotes/<remote>/<target>`` and NOTHING else. Remote state must
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

The remote name is parameterized (``origin`` by default for
backwards compatibility, any configured remote name when remote sync is
opted into) so the same probe services both the legacy observe-only
``origin`` probe and the opt-in ``auto_integrate_remote_target`` sync.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import branch_sha, is_ancestor
from ralph.git.subprocess_runner import GitRunOptions, run_git

if TYPE_CHECKING:
    from pathlib import Path

#: Default remote name for the observe-only probe. ``origin`` matches the
#: clone topology every existing run started with; the opt-in remote
#: sync tier configures its remote via ``auto_integrate_remote_target``.
DEFAULT_REFRESH_REMOTE = "origin"

#: Typed outcomes of :func:`refresh_target_from_remote`. The refresh is
#: fail-open by design -- an unreachable remote degrades to local-only
#: integration rather than failing the run -- so the outcome is the ONLY
#: signal that tells an operator how the mainline pointer the
#: integration used was observed. It is recorded on
#: ``RebaseState.last_refresh`` and rendered in the auto-integrate line.
REFRESH_DISABLED = "fetch disabled"
REFRESH_NO_ORIGIN = "no origin remote"
REFRESH_NO_REMOTE = "no remote configured"
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
#: Local target contains the fetched remote target. This matters only to the
#: opt-in sync path: it must publish later, not rebase local history.
REFRESH_LOCAL_AHEAD = "local ahead of remote"
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
    "DEFAULT_REFRESH_REMOTE",
    "REFRESH_ALREADY_CURRENT",
    "REFRESH_DISABLED",
    "REFRESH_DIVERGED",
    "REFRESH_LOCAL_AHEAD",
    "REFRESH_LOCAL_FLEET",
    "REFRESH_NO_LOCAL_BRANCH",
    "REFRESH_NO_ORIGIN",
    "REFRESH_NO_REMOTE",
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
        logger.debug("auto_integrate: could not observe '{}': {}", target, observe_exc)
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def refresh_target_from_remote(
    repo_root: Path,
    target: str,
    *,
    timeout_seconds: float,
    remote: str = DEFAULT_REFRESH_REMOTE,
) -> str:
    """Observe the freshness of ``refs/heads/<target>``, fetching ``remote`` if any.

    Returns one of the ``REFRESH_*`` outcomes. Never raises, never
    pushes, and NEVER moves a local ref: the fetch updates only the
    remote-tracking ref, and the comparison below is pure reporting.
    Remote state must not affect local rebase operations, so an
    origin observed strictly ahead is recorded as
    :data:`REFRESH_ORIGIN_AHEAD` and the local ref -- the authoritative
    pointer every local decision uses -- is left exactly where the
    local fleet put it.

    Args:
        repo_root: Repository root in which to run the probe.
        target: Branch whose freshness to observe.
        timeout_seconds: Per-attempt wall-clock fetch budget.
        remote: The remote to fetch from. Defaults to ``origin`` for
            backwards compatibility with the observe-only probe that
            shipped first; the opt-in remote-sync tier passes the
            configured ``auto_integrate_remote_target`` here.
    """
    if not _has_remote(repo_root, remote):
        return _observe_without_remote(repo_root, target, remote)

    if not _fetch_target(repo_root, target, timeout_seconds, remote=remote):
        # A cached ``refs/remotes/<remote>/<target>`` from an earlier,
        # successful fetch is NOT evidence of a fresh remote read: it
        # can be arbitrarily old. Reporting anything but UNREACHABLE
        # here would assert a freshness this call never established.
        logger.debug(
            "auto_integrate: fetch of '{}' from '{}' failed; remote unreachable",
            target,
            remote,
        )
        return REFRESH_UNREACHABLE

    return _classify_remote_position(repo_root, target, remote)


def _observe_without_remote(repo_root: Path, target: str, remote: str) -> str:
    """Report freshness for a repository that has no ``remote`` configured.

    'No remote' is not the same as 'no fresh pointer'. Ralph's own agent
    fleet runs as linked worktrees over one git common directory with no
    remote at all, and sibling agents advance ``refs/heads/<target>``
    there continuously. So the local ref is re-observed and
    :data:`REFRESH_LOCAL_FLEET` is reported. The ``REFRESH_NO_ORIGIN``
    outcome survives for the legacy observe-only path where ``remote``
    is ``origin``; ``REFRESH_NO_REMOTE`` is the parametrized equivalent
    used by remote sync with a custom remote.
    """
    observed = observe_target_sha(repo_root, target)
    if observed is None:
        logger.debug(
            "auto_integrate: no remote '{}' and no local '{}'; nothing to observe",
            remote,
            target,
        )
        return REFRESH_NO_ORIGIN if remote == DEFAULT_REFRESH_REMOTE else REFRESH_NO_REMOTE
    logger.debug(
        "auto_integrate: no remote '{}'; observed local '{}' at {}",
        remote,
        target,
        observed,
    )
    return REFRESH_LOCAL_FLEET


def _classify_remote_position(repo_root: Path, target: str, remote: str) -> str:
    """Name where ``remote`` sits relative to the authoritative local ref.

    Pure observation over refs a successful fetch just updated: no
    branch in this function mutates anything. The strict-ancestor probe
    distinguishes a remote that is simply ahead (reported, not
    applied) from one that diverged; both leave the local ref alone,
    because the local ref is the pointer local rebases are FOR.
    """
    remote_sha = _remote_tracking_sha(repo_root, target, remote)
    if remote_sha is None:
        # Reached only after a SUCCESSFUL fetch, so the remote
        # genuinely does not carry this branch -- the unreachable case
        # returned before this function was called.
        logger.debug(
            "auto_integrate: no remote-tracking ref for '{}/{}'; nothing to observe",
            remote,
            target,
        )
        return REFRESH_NO_REMOTE_BRANCH

    local_sha = branch_sha(repo_root, target)
    if local_sha is None:
        logger.debug("auto_integrate: local branch '{}' absent", target)
        return REFRESH_NO_LOCAL_BRANCH
    if local_sha == remote_sha:
        logger.debug("auto_integrate: '{}' already matches remote '{}'", target, remote)
        return REFRESH_ALREADY_CURRENT
    if is_ancestor(repo_root, local_sha, remote_sha):
        logger.debug(
            "auto_integrate: {}/{} is ahead of the local ref; local kept ({} != {})",
            remote,
            target,
            local_sha,
            remote_sha,
        )
        return REFRESH_ORIGIN_AHEAD
    if is_ancestor(repo_root, remote_sha, local_sha):
        logger.debug("auto_integrate: local '{}' is ahead of {}/{}", target, remote, target)
        return REFRESH_LOCAL_AHEAD
    logger.debug(
        "auto_integrate: {}/{} diverged from the local ref; local kept",
        remote,
        target,
    )
    return REFRESH_DIVERGED


def _has_remote(repo_root: Path, remote: str) -> bool:
    """True when ``remote`` is configured; no network call.

    Filters empty / whitespace names defensively -- an empty remote
    name on the legacy observe-only path is the same as "no origin",
    just rendered differently for the operator. Anything else is
    decided from a real ``git remote get-url`` query.
    """
    if not isinstance(remote, str) or not remote.strip():
        return False
    result = run_git(
        ("remote", "get-url", remote),
        cwd=repo_root,
        label=f"git-{remote}-url",
    )
    return result.returncode == 0


def _fetch_target(
    repo_root: Path,
    target: str,
    timeout_seconds: float,
    *,
    remote: str,
) -> bool:
    """Fetch exactly one branch from ``remote``, bounded and fail-open.

    Returns whether the fetch itself succeeded. The fetch touches ONLY
    ``refs/remotes/<remote>/<target>``; no local ref is examined, moved
    or created here. A failure ENDS the refresh with
    :data:`REFRESH_UNREACHABLE`. ``run_git`` already forces
    ``GIT_TERMINAL_PROMPT=0`` and ``GCM_INTERACTIVE=Never``, so a
    credential prompt fails fast rather than hanging.
    """
    try:
        result = run_git(
            ("fetch", "--quiet", remote, "--", target),
            cwd=repo_root,
            label=f"git-fetch-target-{remote}",
            options=GitRunOptions(timeout=timeout_seconds),
        )
    except Exception as fetch_exc:
        logger.debug(
            "auto_integrate: fetch of '{}' from '{}' failed: {}", target, remote, fetch_exc
        )
        return False
    return result.returncode == 0


def _remote_tracking_sha(repo_root: Path, target: str, remote: str) -> str | None:
    """SHA of ``refs/remotes/<remote>/<target>``, or None when absent."""
    result = run_git(
        ("rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{target}"),
        cwd=repo_root,
        label=f"git-remote-tracking-sha-{remote}",
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import branch_sha, is_ancestor
from ralph.git.subprocess_runner import GitRunOptions, run_git

if TYPE_CHECKING:
    from pathlib import Path

DEFAULT_REFRESH_REMOTE = "origin"

REFRESH_DISABLED = "fetch disabled"
REFRESH_NO_ORIGIN = "no origin remote"
REFRESH_NO_REMOTE = "no remote configured"
REFRESH_UNREACHABLE = "origin unreachable"
REFRESH_NO_REMOTE_BRANCH = "no remote branch"
REFRESH_NO_LOCAL_BRANCH = "no local branch"
REFRESH_ALREADY_CURRENT = "already current"
REFRESH_DIVERGED = "diverged from origin"
REFRESH_REFRESHED = "refreshed from origin"
REFRESH_ORIGIN_AHEAD = "origin ahead (local ref kept)"
REFRESH_LOCAL_AHEAD = "local ahead of remote"
REFRESH_LOCAL_FLEET = "local fleet"
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
    "remote_configured",
]


def observe_target_sha(repo_root: Path, target: str) -> str | None:
    try:
        from ralph.git.operations import _read_ref_from_disk, _resolve_gitdir_paths

        gitdir, commondir = _resolve_gitdir_paths(repo_root)
        if gitdir is not None:
            ref = f"refs/heads/{target}"
            search_dirs = [gitdir]
            if commondir is not None and commondir != gitdir:
                search_dirs.append(commondir)
            direct_sha = _read_ref_from_disk(ref, search_dirs)
            if direct_sha is not None:
                return direct_sha
    except (OSError, ValueError) as exc:
        logger.debug("direct target ref read failed for '{}': {}", target, exc)
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
    try:
        has_remote = _has_remote(repo_root, remote)
    except Exception as remote_exc:
        logger.debug("auto_integrate: remote lookup for '{}' failed: {}", remote, remote_exc)
        return REFRESH_UNREACHABLE
    if not has_remote:
        return _observe_without_remote(repo_root, target, remote)

    if not _fetch_target(repo_root, target, timeout_seconds, remote=remote):
        logger.debug(
            "auto_integrate: fetch of '{}' from '{}' failed; remote unreachable",
            target,
            remote,
        )
        return REFRESH_UNREACHABLE

    try:
        return _classify_remote_position(repo_root, target, remote)
    except Exception as classify_exc:
        logger.debug(
            "auto_integrate: could not classify '{}' against '{}': {}",
            target,
            remote,
            classify_exc,
        )
        return REFRESH_UNREACHABLE


def _observe_without_remote(repo_root: Path, target: str, remote: str) -> str:
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
    remote_sha = _remote_tracking_sha(repo_root, target, remote)
    if remote_sha is None:
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


def remote_configured(repo_root: Path, remote: str) -> bool:
    try:
        return _has_remote(repo_root, remote)
    except Exception as remote_exc:
        logger.debug("auto_integrate: remote lookup for '{}' failed: {}", remote, remote_exc)
        return False


def _has_remote(repo_root: Path, remote: str) -> bool:
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
    try:
        from ralph.git.operations import _read_ref_from_disk, _resolve_gitdir_paths

        gitdir, commondir = _resolve_gitdir_paths(repo_root)
        if gitdir is not None:
            ref = f"refs/remotes/{remote}/{target}"
            search_dirs = [gitdir]
            if commondir is not None and commondir != gitdir:
                search_dirs.append(commondir)
            direct_sha = _read_ref_from_disk(ref, search_dirs)
            if direct_sha is not None:
                return direct_sha
    except (OSError, ValueError) as exc:
        logger.debug("direct remote ref read failed for '{}/{}': {}", remote, target, exc)
    result = run_git(
        ("rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{target}"),
        cwd=repo_root,
        label=f"git-remote-tracking-sha-{remote}",
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None

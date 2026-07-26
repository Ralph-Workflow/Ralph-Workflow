"""Fail-open, best-effort push of one local branch to one configured remote.

The opt-in remote-sync tier publishes only ``auto_integrate_remote_target``
after a successful local landing. Remote failures never undo that landing or
fail the run. The explicit non-force refspec ensures this module cannot push
another ref or rewrite remote history.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.subprocess_runner import GitRunOptions, run_git

if TYPE_CHECKING:
    from pathlib import Path


def _list_remotes(repo_root: Path) -> list[str]:
    """Return configured remote names, or no names when Git cannot be queried."""
    try:
        result = run_git(("remote",), cwd=repo_root, label="git-list-remotes")
    except (OSError, FileNotFoundError) as exc:
        logger.debug("auto_integrate_push: `git remote` could not be launched: {}", exc)
        return []
    if result.returncode != 0:
        logger.debug(
            "auto_integrate_push: `git remote` failed: {}", (result.stderr or "").strip()
        )
        return []
    names: list[str] = []
    for raw in result.stdout.splitlines():
        name = raw.strip()
        if name and name not in names:
            names.append(name)
    return names


def _push_to_remote(
    repo_root: Path,
    remote: str,
    branch: str,
    *,
    timeout_seconds: float,
) -> tuple[bool, str]:
    """Push the explicit target-branch refspec and return a fail-open result."""
    try:
        result = run_git(
            ("push", "--", remote, f"refs/heads/{branch}:refs/heads/{branch}"),
            cwd=repo_root,
            label=f"git-push-to-{remote}",
            options=GitRunOptions(timeout=timeout_seconds),
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "").strip()
    return True, ""


def push_branch_to_single_remote(
    repo_root: Path,
    branch: str,
    *,
    remote: str,
    timeout_seconds: float,
) -> str:
    """Push ``branch`` to one configured remote, returning a summary without raising.

    Only ``refs/heads/<branch>:refs/heads/<branch>`` is pushed. Git rejects a
    non-fast-forward update; this helper never supplies a force-capable option.
    """
    if not isinstance(remote, str) or not remote.strip() or remote not in _list_remotes(repo_root):
        return f"remote '{remote if isinstance(remote, str) else ''}' not configured"
    ok, detail = _push_to_remote(repo_root, remote, branch, timeout_seconds=timeout_seconds)
    if ok:
        return f"pushed {branch} to {remote}"
    one_line = " ".join(detail.splitlines()) or "push failed"
    logger.warning(
        "auto_integrate_push: push of '{}' to '{}' failed: {}", branch, remote, one_line
    )
    return f"push of {branch} to {remote} failed: {one_line}"


__all__ = ["push_branch_to_single_remote"]

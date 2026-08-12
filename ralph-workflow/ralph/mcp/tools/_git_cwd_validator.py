"""Workspace-bounded git cwd validation.

The git read handlers resolve a requested cwd and probe its repository top
level before they invoke git. They warn rather than execute when either
resolved location falls outside the active workspace.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from ralph.process.manager import ManagedProcess, SpawnOptions, get_process_manager

#: Bounded timeout for the ``git rev-parse`` probe.
_TOPLEVEL_PROBE_TIMEOUT_SECONDS = 10.0

type GitToplevelRunner = Callable[[Path], Path | None]


def _is_within(path: Path, root: Path) -> bool:
    """Return whether ``path`` is ``root`` or is contained by it."""
    return path == root or root in path.parents


def _has_git_metadata(path: Path) -> bool:
    """Return whether ``path`` lies under a directory carrying ``.git`` metadata."""
    return any((ancestor / ".git").exists() for ancestor in (path, *path.parents))


def _default_toplevel_runner(resolved_cwd: Path) -> Path | None:
    """Return git's containing top-level, or ``None`` when unavailable."""
    try:
        proc: ManagedProcess = get_process_manager().spawn(
            ["git", "rev-parse", "--show-toplevel"],
            SpawnOptions(
                cwd=str(resolved_cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                label="git-cwd-toplevel-probe",
            ),
        )
        stdout, _stderr = proc.communicate_and_cleanup(
            timeout=_TOPLEVEL_PROBE_TIMEOUT_SECONDS
        )
        returncode = proc.returncode if proc.returncode is not None else 1
    except (subprocess.TimeoutExpired, OSError):
        return None
    if returncode != 0:
        return None
    text = (stdout or b"").decode("utf-8", errors="replace").strip()
    return Path(text) if text else None


def resolve_git_cwd(
    *,
    workspace_root: Path,
    requested_cwd: str | None,
    git_runner: GitToplevelRunner | None = None,
) -> tuple[Path, bool, Path | None]:
    """Resolve a git cwd and report whether it escapes the workspace.

    Returns ``(resolved_path, is_outside_workspace, discovered_top_level)``.
    The top-level is ``None`` when no containing repository is found. Both
    resolved locations contribute to the outside flag.
    """
    root = Path(workspace_root).resolve()
    if requested_cwd is None or requested_cwd == "":
        resolved = root
    else:
        candidate = Path(requested_cwd)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
    runner = git_runner or _default_toplevel_runner
    top_level = runner(resolved) if git_runner is not None or _has_git_metadata(resolved) else None
    resolved_top = top_level.resolve() if top_level is not None else None
    is_outside = not _is_within(resolved, root) or (
        resolved_top is not None and not _is_within(resolved_top, root)
    )
    return resolved, is_outside, resolved_top


__all__ = ["GitToplevelRunner", "resolve_git_cwd"]

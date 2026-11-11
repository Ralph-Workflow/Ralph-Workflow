
from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from ralph.process.manager import ManagedProcess, SpawnOptions, get_process_manager

_TOPLEVEL_PROBE_TIMEOUT_SECONDS = 10.0

type GitToplevelRunner = Callable[[Path], Path | None]


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _default_toplevel_runner(resolved_cwd: Path) -> Path | None:
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
        stdout, _stderr = proc.communicate_and_cleanup(timeout=_TOPLEVEL_PROBE_TIMEOUT_SECONDS)
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
    root = Path(workspace_root).resolve()
    if requested_cwd is None or requested_cwd == "":
        resolved = root
    else:
        candidate = Path(requested_cwd)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
    runner = git_runner or _default_toplevel_runner
    top_level = runner(resolved)
    resolved_top = top_level.resolve() if top_level is not None else None
    is_outside = not _is_within(resolved, root) or (
        resolved_top is not None and not _is_within(resolved_top, root)
    )
    return resolved, is_outside, resolved_top


__all__ = ["GitToplevelRunner", "resolve_git_cwd"]

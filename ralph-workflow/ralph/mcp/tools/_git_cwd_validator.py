"""Workspace-bounded git cwd validator.

The MCP git read tools (``git_status`` / ``git_diff`` / ``git_log`` /
``git_show``) accept an optional ``cwd`` parameter so an agent can read
a *nested* repository contained inside the active workspace. That
parameter is a trust boundary: an absolute path, a ``..`` traversal, or
a symlink must not let git operate on a repository outside the
workspace, and a resolved path *inside* the workspace is still not
sufficient when the workspace itself is a subdirectory of an unrelated
parent repository (git discovers that parent's top-level and would
happily read its commits).

This module performs the two-dimensional check before any git
subprocess is spawned:

1. **Resolved-path check** — ``requested_cwd`` is resolved against the
   workspace root with :meth:`pathlib.Path.resolve` (``..`` segments
   collapsed, symlinks followed). The resolved path must be inside the
   workspace root.
2. **Discovered-top-level check** — ``git rev-parse --show-toplevel``
   run in the resolved cwd must return a top-level that is also inside
   the workspace root. This defeats the parent-repo bypass where the
   workspace lives under an unrelated repository.

A violated boundary raises :class:`InvalidParamsError` naming the
resolved path, the discovered top-level (when the second check fails),
and the workspace root, so the framework boundary converts it into a
``ToolResult(is_error=True)`` whose message tells the caller exactly
which check refused the operation.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from ralph.mcp.tools.coordination import InvalidParamsError
from ralph.process.manager import ManagedProcess, SpawnOptions, get_process_manager

#: Bounded timeout for the ``git rev-parse`` probe. This is the only
#: blocking call in the validator; the MCP timeout contract requires it
#: to be fail-closed.
_TOPLEVEL_PROBE_TIMEOUT_SECONDS = 10.0

type GitToplevelRunner = Callable[[Path], Path | None]


def _is_within(path: Path, root: Path) -> bool:
    """Return True when ``path`` is ``root`` itself or strictly inside it."""
    return path == root or root in path.parents


def _default_toplevel_runner(resolved_cwd: Path) -> Path | None:
    """Run ``git rev-parse --show-toplevel`` in ``resolved_cwd``.

    Spawns through the shared ``ProcessManager`` (the repo's single
    subprocess boundary) with the bounded fail-closed timeout the MCP
    timeout contract requires. Returns the discovered top-level as an
    absolute :class:`Path`, or ``None`` when git reports no containing
    repository (non-zero exit, timeout, or missing binary). ``None``
    means "no repository here" — the handler's own git invocation will
    surface the same failure — so the validator does not reject on
    that basis alone.
    """
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
    stdout_bytes = stdout if stdout is not None else b""
    text = stdout_bytes.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    return Path(text)


def resolve_git_cwd(
    *,
    workspace_root: Path,
    requested_cwd: str | None,
    git_runner: GitToplevelRunner | None = None,
    probe_default_cwd: bool = True,
) -> Path:
    """Resolve and validate a git ``cwd`` against the workspace boundary.

    Args:
        workspace_root: Absolute root of the active workspace.
        requested_cwd: Caller-supplied ``cwd`` value. ``None`` or the
            empty string resolves to the workspace root (legacy
            behavior). Relative paths resolve against the workspace
            root; absolute paths are taken as-is.
        git_runner: Optional injected ``git rev-parse --show-toplevel``
            probe (test seam). Defaults to the bounded real-subprocess
            probe.
        probe_default_cwd: When ``True`` (default) the top-level probe
            runs for every request, including the omitted/empty-``cwd``
            legacy default. When ``False`` the probe is SKIPPED for the
            legacy default only (``requested_cwd`` ``None`` or ``""``);
            the caller MUST then run the equivalent top-level check
            before spawning the git command (see
            ``git_read._check_toplevel_boundary``). The skip keeps
            handler-level unit tests — which drive the handlers against
            non-repo mock workspaces with mocked runners — free of the
            real probe subprocess, while production handlers defer the
            probe into the actual subprocess call site where tests
            already mock the runner.

    Returns:
        The resolved absolute path to run git in.

    Raises:
        InvalidParamsError: When the resolved path is outside the
            workspace root, or when the discovered repository top-level
            is outside the workspace root. The message names the
            resolved path, the top-level (second check), and the
            workspace root so the caller sees exactly why the request
            was refused.
    """
    root = Path(workspace_root).resolve()
    if requested_cwd is None or requested_cwd == "":
        is_default_cwd = True
        resolved = root
    else:
        is_default_cwd = False
        candidate = Path(requested_cwd)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
    if not _is_within(resolved, root):
        raise InvalidParamsError(
            "git cwd resolves outside the workspace: "
            f"resolved={resolved} workspace_root={root}. "
            "Git operations outside the active workspace are refused."
        )
    if is_default_cwd and not probe_default_cwd:
        return resolved
    runner = git_runner or _default_toplevel_runner
    top_level = runner(resolved)
    if top_level is not None:
        resolved_top = top_level.resolve()
        if not _is_within(resolved_top, root):
            raise InvalidParamsError(
                "git repository top-level is outside the workspace: "
                f"top_level={resolved_top} resolved={resolved} "
                f"workspace_root={root}. Git operations outside the "
                "active workspace are refused."
            )
    return resolved


__all__ = [
    "GitToplevelRunner",
    "resolve_git_cwd",
]

"""MCP Git read tool handlers.

Ports the Rust MCP Git read tools so agents can inspect repository state
through bounded read-only git commands from the workspace root.

Exported surface:

- ``handle_git_status`` — runs ``git status`` in the workspace root.
  Capability: ``GitStatusRead``.
- ``handle_git_diff`` — runs ``git diff [args]`` in the workspace root.
  The handler uses the *lenient* runner so a non-zero exit (which can
  happen when there is nothing to diff, or when a path filter matches
  no files) is surfaced as stdout/stderr rather than an exception.
  Capability: ``GitDiffRead``.
- ``handle_git_log`` — runs ``git log -<count> --oneline`` (default
  count = ``DEFAULT_LOG_COUNT`` = 10). Capability: ``GitStatusRead``.
- ``handle_git_show`` — runs ``git show <ref>`` for a single object.
  Capability: ``GitStatusRead``.
- ``parse_git_diff_params`` / ``parse_git_log_params`` /
  ``parse_git_show_params`` — the parameter parsers used by the
  handlers above (string-only args, bounded count, ref validation).
- ``run_git_command`` / ``run_git_command_lenient`` — the two
  subprocess runners. Both require a successful ``git`` exit code
  unless the lenient variant is used. They are the only call sites of
  the internal ``_run_git_subprocess`` helper, which always carries
  the fixed ``_GIT_READ_TIMEOUT_SECONDS = 30.0`` bound.

Trust boundary: every handler is gated on a ``McpCapability`` and runs
through a process spawned by ``ralph.process.manager``. The 30-second
hard timeout is the bounded-subprocess contract — a hung ``git status``
over a large ``vendor/`` submodule or a held ``.git`` lock cannot hang
the MCP server thread.

Side effects: spawns a ``git`` subprocess under the workspace root
(registered with the global ``ProcessManager``) and reads its
stdout/stderr. No write to the workspace, no network call. Timeouts
are converted into a non-retryable ``is_error`` result that names the
likely cause (vendor/ submodule or held lock) and tells the agent
*not* to retry unchanged.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping

from ralph.mcp.tools._git_diff_params import GitDiffParams
from ralph.mcp.tools._git_execution_error import ExecutionError
from ralph.mcp.tools._git_log_params import GitLogParams
from ralph.mcp.tools._git_show_params import GitShowParams
from ralph.mcp.tools.coordination import (
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
    ToolResult,
    require_capability,
)
from ralph.process.manager import SpawnOptions, get_process_manager

GIT_STATUS_READ_CAPABILITY = "GitStatusRead"
GIT_DIFF_READ_CAPABILITY = "GitDiffRead"
DEFAULT_LOG_COUNT = 10
type GitRunner = Callable[[list[str], Path], subprocess.CompletedProcess[bytes]]
type CwdProvider = Callable[[], Path]


@runtime_checkable
class WorkspaceWithRoot(Protocol):
    """Workspace surface required for git command execution."""

    @property
    def root(self) -> Path:
        """Return the absolute workspace root path."""
        ...


def _workspace_root(workspace: object, *, cwd_provider: CwdProvider = Path.cwd) -> Path:
    if isinstance(workspace, WorkspaceWithRoot):
        return workspace.root
    root_value = cast("Path | str | None", getattr(workspace, "root", None))
    if isinstance(root_value, Path):
        return root_value
    if isinstance(root_value, str):
        return Path(root_value)
    return cwd_provider()


def parse_git_diff_params(params: Mapping[str, object]) -> GitDiffParams:
    """Parse git diff params, keeping only string arguments."""
    args_value = params.get("args")
    args = (
        [value for value in args_value if isinstance(value, str)]
        if isinstance(args_value, list)
        else []
    )
    return GitDiffParams(args=args)


def parse_git_log_params(params: Mapping[str, object]) -> GitLogParams:
    """Parse git log params with the Rust default count."""
    count_value = params.get("count", DEFAULT_LOG_COUNT)
    count = count_value if isinstance(count_value, int) and count_value >= 0 else DEFAULT_LOG_COUNT
    return GitLogParams(count=count)


def parse_git_show_params(params: Mapping[str, object]) -> GitShowParams:
    """Parse git show params."""
    ref_value = params.get("ref")
    if not isinstance(ref_value, str):
        raise InvalidParamsError("Missing 'ref' parameter")
    return GitShowParams(git_ref=ref_value)


def _decode_output(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def run_git_command(
    workspace: object,
    args: list[str],
    *,
    runner: GitRunner | None = None,
    cwd_provider: CwdProvider = Path.cwd,
) -> str:
    """Execute git and require a successful exit status."""
    git_runner = runner or _run_git_subprocess
    try:
        output = git_runner(["git", *args], _workspace_root(workspace, cwd_provider=cwd_provider))
    except subprocess.TimeoutExpired as exc:
        raise ExecutionError(
            f"git command timed out after {exc.timeout:g}s: {' '.join(args)}",
            timed_out=True,
        ) from exc
    except FileNotFoundError as exc:
        raise ExecutionError(f"Failed to execute git: {exc}") from exc
    except PermissionError as exc:
        raise ExecutionError(f"Failed to execute git: {exc}") from exc
    except OSError as exc:
        raise ExecutionError(f"Failed to execute git: {exc}") from exc

    stdout = _decode_output(output.stdout)
    stderr = _decode_output(output.stderr)

    if output.returncode != 0:
        raise ExecutionError(f"git command failed: {stderr}")

    return stdout


def run_git_command_lenient(
    workspace: object,
    args: list[str],
    *,
    runner: GitRunner | None = None,
    cwd_provider: CwdProvider = Path.cwd,
) -> str:
    """Execute git and return combined stdout/stderr regardless of exit code."""
    git_runner = runner or _run_git_subprocess
    try:
        output = git_runner(["git", *args], _workspace_root(workspace, cwd_provider=cwd_provider))
    except subprocess.TimeoutExpired as exc:
        raise ExecutionError(
            f"git command timed out after {exc.timeout:g}s: {' '.join(args)}",
            timed_out=True,
        ) from exc
    except FileNotFoundError as exc:
        raise ExecutionError(f"Failed to execute git: {exc}") from exc
    except PermissionError as exc:
        raise ExecutionError(f"Failed to execute git: {exc}") from exc
    except OSError as exc:
        raise ExecutionError(f"Failed to execute git: {exc}") from exc

    return f"{_decode_output(output.stdout)}{_decode_output(output.stderr)}"


# MCP read tools must never block the server thread indefinitely: a hung
# `git status` (large vendor/ submodules, a held .git lock) would starve the
# agent of output and trip the idle watchdog. Git reads are bounded at a fixed
# 30s (they are short, fixed subcommands with no agent-tunable timeout, unlike
# the exec tool) and fail closed — communicate_and_cleanup terminates and kills
# the process tree on expiry, then re-raises TimeoutExpired for
# run_git_command* to convert into an actionable is_error result.
_GIT_READ_TIMEOUT_SECONDS = 30.0


def _run_git_subprocess(command: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    proc = get_process_manager().spawn(
        command,
        SpawnOptions(
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            label="git-mcp-read",
        ),
    )
    stdout, stderr = proc.communicate_and_cleanup(timeout=_GIT_READ_TIMEOUT_SECONDS)
    returncode = proc.returncode if proc.returncode is not None else 0
    return subprocess.CompletedProcess(command, returncode, stdout or b"", stderr or b"")


def _git_read_result(produce: Callable[[], str]) -> ToolResult:
    """Run a git read and wrap its output, converting a timeout into an
    actionable, non-retryable is_error result (NOT a propagated -32603)."""
    try:
        output = produce()
    except ExecutionError as exc:
        if not exc.timed_out:
            raise
        message = (
            f"{exc}\n"
            "This is expected for large vendor/ submodules or a held .git lock."
            " Re-issuing the identical call will time out again — narrow the command,"
            " exclude large submodules, or retry later. Do not retry unchanged."
        )
        return ToolResult(content=[ToolContent.text_content(message)], is_error=True)
    return ToolResult(content=[ToolContent.text_content(output)], is_error=False)


def handle_git_status(
    session: CoordinationSessionLike,
    workspace: object,
    _params: Mapping[str, object],
) -> ToolResult:
    """Read the git status of the workspace.

    Args:
        session: Agent session; must declare ``GitStatusRead``.
        workspace: Workspace surface whose root is the cwd for ``git status``.
        _params: Unused; kept for tool-handler signature parity.

    Returns:
        A ``ToolResult`` whose text content is the ``git status`` output.

    Raises:
        CapabilityDeniedError: When the session does not declare
            ``GitStatusRead``.

    Side effects:
        Spawns a ``git status`` subprocess registered with the global
        ``ProcessManager``. Bounded by ``_GIT_READ_TIMEOUT_SECONDS = 30``.
        No workspace writes, no network calls.
    """
    require_capability(session, GIT_STATUS_READ_CAPABILITY, "Git status")
    return _git_read_result(lambda: run_git_command(workspace, ["status"]))


def handle_git_diff(
    session: CoordinationSessionLike,
    workspace: object,
    params: Mapping[str, object],
) -> ToolResult:
    """Read the git diff of the workspace.

    Args:
        session: Agent session; must declare ``GitDiffRead``.
        workspace: Workspace surface whose root is the cwd for ``git diff``.
        params: Mapping with optional ``args`` (list of strings passed
            verbatim to ``git diff``). Empty mapping returns the
            full-tree diff.

    Returns:
        A ``ToolResult`` whose text content is the ``git diff`` output.
        The lenient runner is used so a non-zero exit (no diff to show,
        unmatched path filter) surfaces as stdout/stderr rather than
        an exception.

    Raises:
        CapabilityDeniedError: When the session does not declare
            ``GitDiffRead``.
        InvalidParamsError: When ``params`` fails ``parse_git_diff_params``.

    Side effects:
        Spawns a ``git diff`` subprocess registered with the global
        ``ProcessManager``. Bounded by ``_GIT_READ_TIMEOUT_SECONDS = 30``.
        No workspace writes, no network calls.
    """
    require_capability(session, GIT_DIFF_READ_CAPABILITY, "Git diff")
    parsed = parse_git_diff_params(params)
    return _git_read_result(lambda: run_git_command_lenient(workspace, ["diff", *parsed.args]))


def handle_git_log(
    session: CoordinationSessionLike,
    workspace: object,
    params: Mapping[str, object],
) -> ToolResult:
    """Read the git commit log.

    Args:
        session: Agent session; must declare ``GitStatusRead``.
        workspace: Workspace surface whose root is the cwd for ``git log``.
        params: Mapping with optional ``count`` (positive integer,
            defaults to ``DEFAULT_LOG_COUNT = 10``).

    Returns:
        A ``ToolResult`` whose text content is the ``git log -<count>
        --oneline`` output.

    Raises:
        CapabilityDeniedError: When the session does not declare
            ``GitStatusRead``.
        InvalidParamsError: When ``params`` fails ``parse_git_log_params``.

    Side effects:
        Spawns a ``git log`` subprocess registered with the global
        ``ProcessManager``. Bounded by ``_GIT_READ_TIMEOUT_SECONDS = 30``.
        No workspace writes, no network calls.
    """
    require_capability(session, GIT_STATUS_READ_CAPABILITY, "Git log")
    parsed = parse_git_log_params(params)
    return _git_read_result(
        lambda: run_git_command(workspace, ["log", f"-{parsed.count}", "--oneline"])
    )


def handle_git_show(
    session: CoordinationSessionLike,
    workspace: object,
    params: Mapping[str, object],
) -> ToolResult:
    """Show a git object by ref.

    Args:
        session: Agent session; must declare ``GitStatusRead``.
        workspace: Workspace surface whose root is the cwd for ``git show``.
        params: Mapping with required ``ref`` (string; commit-ish, tag,
            or branch) per ``parse_git_show_params``.

    Returns:
        A ``ToolResult`` whose text content is the ``git show <ref>``
        output.

    Raises:
        CapabilityDeniedError: When the session does not declare
            ``GitStatusRead``.
        InvalidParamsError: When ``params`` fails ``parse_git_show_params``.

    Side effects:
        Spawns a ``git show`` subprocess registered with the global
        ``ProcessManager``. Bounded by ``_GIT_READ_TIMEOUT_SECONDS = 30``.
        No workspace writes, no network calls.
    """
    require_capability(session, GIT_STATUS_READ_CAPABILITY, "Git show")
    parsed = parse_git_show_params(params)
    return _git_read_result(lambda: run_git_command(workspace, ["show", parsed.git_ref]))


__all__ = [
    "GIT_DIFF_READ_CAPABILITY",
    "GIT_STATUS_READ_CAPABILITY",
    "ExecutionError",
    "GitDiffParams",
    "GitLogParams",
    "GitShowParams",
    "WorkspaceWithRoot",
    "handle_git_diff",
    "handle_git_log",
    "handle_git_show",
    "handle_git_status",
    "parse_git_diff_params",
    "parse_git_log_params",
    "parse_git_show_params",
    "run_git_command",
    "run_git_command_lenient",
]

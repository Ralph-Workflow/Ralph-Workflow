"""MCP Git read tool handlers.

Ports the Rust MCP Git read tools so agents can inspect repository state
through bounded read-only git commands from the workspace root.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping

from ralph.mcp.tool_coordination import (
    InvalidParamsError,
    SessionLike,
    ToolContent,
    ToolError,
    ToolResult,
    require_capability,
)

GIT_STATUS_READ_CAPABILITY = "GitStatusRead"
GIT_DIFF_READ_CAPABILITY = "GitDiffRead"
_DEFAULT_LOG_COUNT = 10
type GitRunner = Callable[[list[str], Path], subprocess.CompletedProcess[bytes]]
type CwdProvider = Callable[[], Path]


class ExecutionError(ToolError):
    """Raised when a git subprocess cannot be started or fails."""


@dataclass(frozen=True)
class GitDiffParams:
    """Parsed parameters for the git diff tool."""

    args: list[str]


@dataclass(frozen=True)
class GitLogParams:
    """Parsed parameters for the git log tool."""

    count: int


@dataclass(frozen=True)
class GitShowParams:
    """Parsed parameters for the git show tool."""

    git_ref: str


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
    count_value = params.get("count", _DEFAULT_LOG_COUNT)
    count = count_value if isinstance(count_value, int) and count_value >= 0 else _DEFAULT_LOG_COUNT
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
    except FileNotFoundError as exc:
        raise ExecutionError(f"Failed to execute git: {exc}") from exc
    except PermissionError as exc:
        raise ExecutionError(f"Failed to execute git: {exc}") from exc
    except OSError as exc:
        raise ExecutionError(f"Failed to execute git: {exc}") from exc

    return f"{_decode_output(output.stdout)}{_decode_output(output.stderr)}"


def _run_git_subprocess(command: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(command, cwd=cwd, capture_output=True, check=False)


def handle_git_status(
    session: SessionLike,
    workspace: object,
    _params: Mapping[str, object],
) -> ToolResult:
    """Read the git status of the workspace."""
    require_capability(session, GIT_STATUS_READ_CAPABILITY, "Git status")
    output = run_git_command(workspace, ["status"])
    return ToolResult(content=[ToolContent.text_content(output)], is_error=False)


def handle_git_diff(
    session: SessionLike,
    workspace: object,
    params: Mapping[str, object],
) -> ToolResult:
    """Read the git diff of the workspace."""
    require_capability(session, GIT_DIFF_READ_CAPABILITY, "Git diff")
    parsed = parse_git_diff_params(params)
    output = run_git_command_lenient(workspace, ["diff", *parsed.args])
    return ToolResult(content=[ToolContent.text_content(output)], is_error=False)


def handle_git_log(
    session: SessionLike,
    workspace: object,
    params: Mapping[str, object],
) -> ToolResult:
    """Read the git commit log."""
    require_capability(session, GIT_STATUS_READ_CAPABILITY, "Git log")
    parsed = parse_git_log_params(params)
    output = run_git_command(workspace, ["log", f"-{parsed.count}", "--oneline"])
    return ToolResult(content=[ToolContent.text_content(output)], is_error=False)


def handle_git_show(
    session: SessionLike,
    workspace: object,
    params: Mapping[str, object],
) -> ToolResult:
    """Show a git object by ref."""
    require_capability(session, GIT_STATUS_READ_CAPABILITY, "Git show")
    parsed = parse_git_show_params(params)
    output = run_git_command(workspace, ["show", parsed.git_ref])
    return ToolResult(content=[ToolContent.text_content(output)], is_error=False)


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

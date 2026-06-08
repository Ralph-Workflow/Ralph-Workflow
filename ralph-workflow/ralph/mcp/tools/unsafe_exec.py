"""MCP unsafe_exec tool handler.

Executes unrestricted shell commands in the real workspace directory.
Only version control commands (git, hg, svn) are blocked.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Final

from ralph.mcp.tools._exec_execution_error import ExecutionError
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
    ToolResult,
    require_capability,
)
from ralph.timeout_defaults import EXEC_DEFAULT_TIMEOUT_MS

if TYPE_CHECKING:
    from collections.abc import Mapping

PROCESS_EXEC_UNBOUNDED_CAPABILITY: Final = "ProcessExecUnbounded"
_VCS_COMMANDS: frozenset[str] = frozenset({"git", "hg", "svn"})
_MAX_OUTPUT_BYTES = 1 * 1024 * 1024

type CwdProvider = Callable[[], Path]


def _workspace_root(workspace: object, *, cwd_provider: CwdProvider = Path.cwd) -> Path:
    if isinstance(workspace, Path):
        return workspace
    if isinstance(workspace, str):
        return Path(workspace)
    root_value: object | None = getattr(workspace, "root", None)
    if isinstance(root_value, Path):
        return root_value
    if isinstance(root_value, str):
        return Path(root_value)
    return cwd_provider()


def handle_unsafe_exec(
    session: CoordinationSessionLike,
    workspace: object,
    params: Mapping[str, object],
) -> ToolResult:
    """Execute an unrestricted shell command in the real workspace directory."""
    require_capability(session, PROCESS_EXEC_UNBOUNDED_CAPABILITY, "Unsafe command execution")

    command_value = params.get("command")
    if not isinstance(command_value, str) or not command_value.strip():
        raise InvalidParamsError("'command' must be a non-empty string")

    command = command_value.strip()
    first_token = command.split()[0].lower()
    if first_token in _VCS_COMMANDS:
        raise CapabilityDeniedError(
            f"Command '{first_token}' is blocked: version control operations "
            "are not permitted via unsafe_exec"
        )

    # Require a strictly positive timeout: 0/negative/non-int falls back to the
    # default. Zero must NOT mean "unbounded" — that would make unsafe_exec a
    # blocking-forever call on the MCP server thread (an agent-controllable hang).
    timeout_value = params.get("timeout_ms", EXEC_DEFAULT_TIMEOUT_MS)
    timeout_ms = (
        timeout_value
        if isinstance(timeout_value, int) and timeout_value > 0
        else EXEC_DEFAULT_TIMEOUT_MS
    )
    timeout_seconds: float = timeout_ms / 1000

    workspace_root = _workspace_root(workspace)

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(workspace_root),
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # Return an actionable, non-retryable is_error result rather than letting
        # the exception become a -32603 protocol error the agent reads as transient
        # and re-issues forever (the 5-hour retry-storm pathology). The rendered
        # message teaches both meanings of a timeout (raise the limit vs. fix a
        # genuinely stuck command), matching what the tool description advertises.
        timeout_error = ExecutionError(
            f"Failed to execute {command!r}: timed out after {timeout_ms}ms",
            timed_out=True,
            timeout_ms=timeout_ms,
            suggested_timeout_ms=timeout_ms * 2 if timeout_ms > 0 else None,
        )
        return ToolResult(
            content=[ToolContent.text_content(str(timeout_error))],
            is_error=True,
        )

    stdout = result.stdout[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
    stderr = result.stderr[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
    text = (
        f"Command: {command}\n"
        f"Exit code: {result.returncode}\n\n"
        f"Stdout:\n{stdout}\n\n"
        f"Stderr:\n{stderr}"
    )
    return ToolResult(
        content=[ToolContent.text_content(text)],
        is_error=result.returncode != 0,
    )


__all__ = [
    "PROCESS_EXEC_UNBOUNDED_CAPABILITY",
    "_VCS_COMMANDS",
    "handle_unsafe_exec",
]

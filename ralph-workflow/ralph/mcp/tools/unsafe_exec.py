"""MCP unsafe_exec tool handler.

Executes unrestricted shell commands in the real workspace directory.
Only version control commands (git, hg, svn) are blocked.

Execution goes through the SAME bounded process-manager path as ``exec``
(``run_command``): output is capped (and spilled to a file when oversized rather
than buffered unbounded in memory) and the process tree is killed on timeout. The
sync handler itself is offloaded off the asyncio event loop by the FastMCP
dispatch (``ralph.mcp.server.runtime``), so a long shell command cannot freeze
the server — it is async by default like every other tool.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from ralph.mcp.tools._exec_execution_error import ExecutionError
from ralph.mcp.tools._exec_output_spill import format_or_spill
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
    ToolResult,
    require_capability,
)
from ralph.mcp.tools.exec import run_command
from ralph.timeout_defaults import EXEC_DEFAULT_TIMEOUT_MS, EXEC_MAX_TIMEOUT_MS

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.mcp.tools._exec_run_deps import ExecRunDeps

PROCESS_EXEC_UNBOUNDED_CAPABILITY: Final = "ProcessExecUnbounded"
_VCS_COMMANDS: frozenset[str] = frozenset({"git", "hg", "svn"})


def handle_unsafe_exec(
    session: CoordinationSessionLike,
    workspace: object,
    params: Mapping[str, object],
    deps: ExecRunDeps | None = None,
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
    # blocking-forever call (an agent-controllable hang).
    timeout_value = params.get("timeout_ms", EXEC_DEFAULT_TIMEOUT_MS)
    timeout_ms = (
        timeout_value
        if isinstance(timeout_value, int) and timeout_value > 0
        else EXEC_DEFAULT_TIMEOUT_MS
    )
    # Cap the per-call override: the MCP client request timeout is derived to exceed
    # EXEC_MAX_TIMEOUT_MS, so this call can never outrun the client and re-trigger
    # the -32001 "Request timed out" storm.
    timeout_ms = min(timeout_ms, EXEC_MAX_TIMEOUT_MS)

    try:
        # Run the arbitrary command through a shell, but via the bounded
        # process-manager path (capped output + process-tree kill on timeout).
        output = run_command("sh", ["-c", command], workspace, timeout_ms, deps=deps)
    except ExecutionError as exc:
        if not exc.timed_out:
            raise
        # A timeout becomes an actionable, non-retryable is_error result rather
        # than a propagated -32603 protocol error the agent re-issues forever (the
        # retry-storm pathology). The rendered message teaches both meanings of a
        # timeout (raise the limit vs. fix a genuinely stuck command).
        return ToolResult(
            content=[ToolContent.text_content(str(exc))],
            is_error=True,
        )

    stdout = output.stdout.decode("utf-8", errors="replace")
    stderr = output.stderr.decode("utf-8", errors="replace")
    text = (
        f"Command: {command}\n"
        f"Exit code: {output.returncode}\n\n"
        f"Stdout:\n{stdout}\n\n"
        f"Stderr:\n{stderr}"
    )
    spill_dir = deps.spill_dir if deps is not None else None
    return format_or_spill(
        text,
        returncode=output.returncode,
        truncated=output.truncated,
        spill_dir=spill_dir,
    )


__all__ = [
    "PROCESS_EXEC_UNBOUNDED_CAPABILITY",
    "_VCS_COMMANDS",
    "handle_unsafe_exec",
]

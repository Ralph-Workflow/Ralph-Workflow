"""MCP unsafe_exec tool handler.

Executes unrestricted shell commands in the real workspace directory.
Only version control commands (git, hg, svn) are blocked.

Execution goes through the SAME bounded process-manager path as ``exec``
(``run_command``): output is capped (and spilled to a file when oversized rather
than buffered unbounded in memory) and the process tree is killed on timeout. The
sync handler is dispatched off the asyncio event loop by the production
``_FallbackHttpHandler`` via the saturated-dispatch seam
(``ralph.mcp.server._saturated_dispatch``), so a long shell command cannot
freeze the server.
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
from ralph.mcp.tools.exec import (
    _VCS_COMMANDS,
    _shell_command_segments,
    _workspace_root,
    find_vcs_usage,
    find_vcs_usage_in_scripts,
    resolve_spill_dir,
    run_command,
)
from ralph.timeout_defaults import EXEC_DEFAULT_TIMEOUT_MS, EXEC_MAX_TIMEOUT_MS

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.mcp.tools._exec_run_deps import ExecRunDeps

PROCESS_EXEC_UNBOUNDED_CAPABILITY: Final = "ProcessExecUnbounded"


def _enforce_vcs_blacklist(command: str, workspace: object) -> None:
    """Deny the command when it uses a VCS tool anywhere, however nested.

    The textual match (``find_vcs_usage``) catches a VCS word in any pipeline
    segment, inside quoted ``sh -c`` strings, in ``$(...)``/backtick
    substitutions, and across newline-separated sequences — checking only the
    first token left every shell operator a bypass. Executed shell scripts
    (``bash deploy.sh``, ``./release``) are additionally content-scanned so a
    git call cannot be laundered through a file. A string the tokenizer cannot
    parse raises ``InvalidParamsError`` (fail closed) rather than running
    unchecked.
    """
    segments = _shell_command_segments(command)
    for segment_command, segment_args in segments:
        word = find_vcs_usage(segment_command, segment_args)
        if word is not None:
            raise CapabilityDeniedError(
                f"Command references '{word}': version control operations "
                "are not permitted via unsafe_exec"
            )
    script_hit = find_vcs_usage_in_scripts(segments, _workspace_root(workspace))
    if script_hit is not None:
        script, word = script_hit
        raise CapabilityDeniedError(
            f"Script '{script}' uses '{word}': version control operations "
            "are not permitted via unsafe_exec"
        )


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
    _enforce_vcs_blacklist(command, workspace)

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
    return format_or_spill(
        text,
        returncode=output.returncode,
        truncated=output.truncated,
        spill_dir=resolve_spill_dir(workspace, deps),
    )


__all__ = [
    "PROCESS_EXEC_UNBOUNDED_CAPABILITY",
    "_VCS_COMMANDS",
    "handle_unsafe_exec",
]

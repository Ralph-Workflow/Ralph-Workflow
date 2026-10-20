"""McpOutcomeUnknownError — raised when in-flight non-idempotent tool call is severed."""

from __future__ import annotations

from ralph.mcp.server._mcp_server_error import McpServerError


class McpOutcomeUnknownError(McpServerError, OSError):
    """Raised when an in-flight non-idempotent tool call is severed during retirement.

    Signals to the caller that the operation was interrupted mid-flight and its
    outcome is unknown; the call must not be blindly re-executed without
    deduplication or verification.
    """

    def __init__(
        self,
        message: str = "In-flight non-idempotent MCP tool call severed; outcome unknown",
        *,
        restart_count: int = 0,
        tool_name: str | None = None,
        last_stage: str | None = None,
        elapsed_s: float | None = None,
        restart_reason: str | None = None,
        handover_step: str | None = None,
        probe_duration_s: float | None = None,
        probe_timeout_s: float | None = None,
        child_pid: int | None = None,
        psutil_status: str | None = None,
        startup_diagnostic: str = "",
    ) -> None:
        self.tool_name = tool_name
        super().__init__(
            message,
            restart_count=restart_count,
            last_stage=last_stage,
            elapsed_s=elapsed_s,
            restart_reason=restart_reason,
            handover_step=handover_step,
            probe_duration_s=probe_duration_s,
            probe_timeout_s=probe_timeout_s,
            child_pid=child_pid,
            psutil_status=psutil_status,
            startup_diagnostic=startup_diagnostic,
            failure_kind="outcome_unknown",
        )


__all__ = ["McpOutcomeUnknownError"]

"""ExecutionError for the git read MCP tool."""

from __future__ import annotations

from ralph.mcp.tools.coordination import ToolError


class ExecutionError(ToolError):
    """Raised when a git subprocess cannot be started or fails.

    ``timed_out`` marks the bounded-timeout case so the git read handlers can
    convert it into an actionable, non-retryable is_error result instead of
    letting it surface as a -32603 protocol error the agent retries forever.
    """

    def __init__(self, message: str = "", *, timed_out: bool = False) -> None:
        super().__init__(message)
        self.timed_out = timed_out

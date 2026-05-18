"""ExecutionError for the git read MCP tool."""

from __future__ import annotations

from ralph.mcp.tools.coordination import ToolError


class ExecutionError(ToolError):
    """Raised when a git subprocess cannot be started or fails."""

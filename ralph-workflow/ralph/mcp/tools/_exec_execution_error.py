"""ExecutionError for the exec MCP tool."""

from __future__ import annotations

from ralph.mcp.tools.coordination import ToolError


class ExecutionError(ToolError):
    """Raised when the exec subprocess cannot be started or times out."""

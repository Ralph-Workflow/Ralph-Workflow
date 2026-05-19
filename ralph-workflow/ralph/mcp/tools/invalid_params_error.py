"""Invalid MCP tool parameter error."""

from __future__ import annotations

from .tool_error import ToolError


class InvalidParamsError(ToolError):
    """Raised when tool parameters are missing or invalid."""

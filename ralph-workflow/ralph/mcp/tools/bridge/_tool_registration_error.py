"""ToolRegistrationError exception."""

from __future__ import annotations

from ralph.mcp.tools.bridge._tool_bridge_error import ToolBridgeError


class ToolRegistrationError(ToolBridgeError):
    """Raised when tool registration is invalid."""

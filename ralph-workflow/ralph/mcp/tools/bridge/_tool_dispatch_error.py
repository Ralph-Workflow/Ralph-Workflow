"""ToolDispatchError exception."""

from __future__ import annotations

from ralph.mcp.tools.bridge._tool_bridge_error import ToolBridgeError


class ToolDispatchError(ToolBridgeError):
    """Raised when tool dispatch fails."""

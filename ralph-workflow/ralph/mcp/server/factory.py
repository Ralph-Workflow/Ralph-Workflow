"""Protocol and handle types for the MCP server factory abstraction."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ralph.mcp.server._handle import McpServerHandle


@runtime_checkable
class McpServerFactory(Protocol):
    """Protocol that every MCP server factory implementation must satisfy."""

    def build(self, session: object) -> McpServerHandle: ...


__all__ = ["McpServerFactory", "McpServerHandle"]

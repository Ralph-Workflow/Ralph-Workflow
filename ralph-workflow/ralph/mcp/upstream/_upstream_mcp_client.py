"""UpstreamMcpClient — protocol for upstream MCP client implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ralph.mcp.upstream.models import UpstreamTool

    JsonObject = dict[str, object]


class UpstreamMcpClient(Protocol):
    """Protocol satisfied by both HTTP and stdio upstream MCP client implementations."""

    def list_tools(self) -> list[UpstreamTool]: ...
    def call_tool(self, name: str, arguments: JsonObject) -> object: ...


__all__ = ["UpstreamMcpClient"]

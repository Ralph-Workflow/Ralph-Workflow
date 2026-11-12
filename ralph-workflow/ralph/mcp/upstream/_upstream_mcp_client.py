from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ralph.mcp.upstream.models import UpstreamTool

    JsonObject = dict[str, object]


class UpstreamMcpClient(Protocol):
    def list_tools(self, *, timeout_s: float | None = None) -> list[UpstreamTool]: ...
    def call_tool(self, name: str, arguments: JsonObject) -> object: ...


__all__ = ["UpstreamMcpClient"]

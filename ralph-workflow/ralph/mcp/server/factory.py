
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ralph.mcp.server._handle import McpServerHandle


@runtime_checkable
class McpServerFactory(Protocol):

    def build(self, session: object) -> McpServerHandle: ...


__all__ = ["McpServerFactory", "McpServerHandle"]

from __future__ import annotations

from ralph.mcp.server.factory import McpServerHandle


class _FakeMcpServerFactory:
    def build(self, session: object) -> McpServerHandle:
        return McpServerHandle(
            endpoint="http://127.0.0.1:9999/mcp",
            pid=99999,
            shutdown=lambda: None,
        )

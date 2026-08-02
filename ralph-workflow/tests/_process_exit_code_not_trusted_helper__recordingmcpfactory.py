from __future__ import annotations

from ralph.mcp.server.factory import McpServerHandle


class _RecordingMcpFactory:
    def build(self, session: object) -> McpServerHandle:
        return McpServerHandle(
            endpoint="http://127.0.0.1:19999/mcp",
            pid=9999,
            shutdown=lambda: None,
        )

from __future__ import annotations

from unittest.mock import MagicMock

from ralph.mcp.server.factory import McpServerHandle


class _FakeMcpServerFactory:
    def __init__(self) -> None:
        self.build = MagicMock(
            side_effect=lambda session: McpServerHandle(
                endpoint="http://127.0.0.1:9999/mcp",
                pid=99999,
                shutdown=lambda: None,
            )
        )

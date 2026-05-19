from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.server.factory import McpServerHandle


class _RecordedHandle:
    handle: McpServerHandle
    shutdown_calls: int = 0

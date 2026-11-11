from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.server.factory import McpServerHandle


@dataclass
class _RecordedHandle:
    handle: McpServerHandle
    shutdown_calls: int = 0

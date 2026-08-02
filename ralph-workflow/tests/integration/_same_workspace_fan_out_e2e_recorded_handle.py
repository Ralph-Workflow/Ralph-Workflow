"""_RecordedHandle helper for test_same_workspace_fan_out_e2e_same_workspace_fan_out_e2_e.py."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.server.factory import McpServerHandle


@dataclass
class _RecordedHandle:
    handle: McpServerHandle
    shutdown_calls: int = 0

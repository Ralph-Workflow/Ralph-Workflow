from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.upstream.models import UpstreamTool


@dataclass(frozen=True)
class ProxiedTool:
    alias: str
    server_name: str
    tool: UpstreamTool

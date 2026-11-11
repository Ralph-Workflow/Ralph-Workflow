
from __future__ import annotations

from ralph.mcp.upstream.upstream_tool import UpstreamTool


class UpstreamCallError(Exception):
    pass


__all__ = [
    "UpstreamCallError",
    "UpstreamTool",
]

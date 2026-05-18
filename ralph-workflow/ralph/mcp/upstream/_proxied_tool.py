"""A single upstream tool mapped to a stable proxy alias."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.upstream.models import UpstreamTool


@dataclass(frozen=True)
class ProxiedTool:
    """A single upstream tool mapped to a stable proxy alias."""

    alias: str
    server_name: str
    tool: UpstreamTool

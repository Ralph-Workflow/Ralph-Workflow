"""Upstream tool advertisement model."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class UpstreamTool:
    """A tool advertised by an upstream MCP server."""

    name: str
    description: str
    input_schema: dict[str, object] = field(default_factory=dict)

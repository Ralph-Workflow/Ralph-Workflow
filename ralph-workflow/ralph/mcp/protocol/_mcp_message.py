"""MCP message dataclass."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MCPMessage:
    """Represents an MCP message."""

    method: str
    params: dict[str, object] | None = None
    msg_id: str | int | None = None
    error: dict[str, object] | None = None

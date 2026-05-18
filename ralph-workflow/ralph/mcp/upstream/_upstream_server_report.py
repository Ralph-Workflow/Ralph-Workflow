"""Validation result for a single upstream MCP server."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class UpstreamServerReport:
    """Validation result for a single upstream MCP server."""

    name: str
    transport: Literal["http", "stdio"]
    ok: bool
    tool_count: int = 0
    error: str | None = None
    secret_keys: tuple[str, ...] = field(default_factory=tuple)

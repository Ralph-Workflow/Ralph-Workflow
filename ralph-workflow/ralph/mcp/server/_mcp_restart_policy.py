"""McpRestartPolicy — bounded restart policy for the MCP server bridge."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class McpRestartPolicy:
    """Bounded restart policy for the MCP server bridge."""

    max_restarts: int = 1000


__all__ = ["McpRestartPolicy"]

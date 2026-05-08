"""Protocol and handle types for the MCP server factory abstraction.

Defines ``McpServerFactory`` (the ``Protocol`` every factory must satisfy) and
``McpServerHandle`` (the value returned by ``build``, carrying the server endpoint,
process PID, and a shutdown callback).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class McpServerHandle:
    """Return value from McpServerFactory.build carrying endpoint, PID, and shutdown hook."""

    endpoint: str
    pid: int
    shutdown: Callable[[], None]


@runtime_checkable
class McpServerFactory(Protocol):
    """Protocol that every MCP server factory implementation must satisfy."""

    def build(self, session: object) -> McpServerHandle: ...


__all__ = ["McpServerFactory", "McpServerHandle"]

"""McpServerHandle dataclass — the value returned by McpServerFactory.build."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class McpServerHandle:
    """Return value from McpServerFactory.build carrying endpoint, PID, and shutdown hook."""

    endpoint: str
    pid: int
    shutdown: Callable[[], None]


__all__ = ["McpServerHandle"]

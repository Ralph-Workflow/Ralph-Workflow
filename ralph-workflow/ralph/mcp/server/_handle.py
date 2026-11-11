
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class McpServerHandle:

    endpoint: str
    pid: int
    shutdown: Callable[[], None]


__all__ = ["McpServerHandle"]

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class McpServerHandle:
    endpoint: str
    pid: int
    shutdown: Callable[[], None]


@runtime_checkable
class McpServerFactory(Protocol):
    def build(self, session: object) -> McpServerHandle: ...


__all__ = ["McpServerFactory", "McpServerHandle"]

"""ProcessLike — subset of ManagedProcess API required by the MCP server lifecycle."""

from __future__ import annotations

from typing import Protocol


class ProcessLike(Protocol):
    """Subset of ManagedProcess API required by the MCP server lifecycle."""

    def poll(self) -> int | None: ...
    def terminate(self, grace_period_s: float = 5.0) -> None: ...
    def wait(self, timeout: float | None = None) -> int | None: ...
    def kill(self) -> None: ...

    @property
    def pid(self) -> int: ...


__all__ = ["ProcessLike"]

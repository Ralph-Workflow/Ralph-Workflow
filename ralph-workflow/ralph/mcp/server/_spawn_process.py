"""SpawnProcess — callable protocol that spawns the MCP server subprocess."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.server._process_like import ProcessLike


class SpawnProcess(Protocol):
    """Callable that spawns the MCP server subprocess.

    The ``phase`` keyword argument, when set, is used to label the process
    ``phase:<phase>:mcp-server`` so it is reaped by the phase-scope cleanup.
    """

    def __call__(
        self,
        command: list[str],
        cwd: Path,
        env: dict[str, str],
        *,
        phase: str | None = None,
    ) -> ProcessLike: ...


__all__ = ["SpawnProcess"]

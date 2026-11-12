from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.server._process_like import ProcessLike


class SpawnProcess(Protocol):
    def __call__(
        self,
        command: list[str],
        cwd: Path,
        env: dict[str, str],
        *,
        phase: str | None = None,
        pass_fds: tuple[int, ...] = (),
    ) -> ProcessLike: ...


__all__ = ["SpawnProcess"]

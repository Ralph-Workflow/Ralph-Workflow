
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.server._exit_handler import ExitHandler
    from ralph.mcp.server._stage_recorder import StageRecorder


class OverrunHandler(Protocol):

    def __call__(
        self,
        workspace_root: Path,
        *,
        stage: str,
        elapsed_s: float,
        budget_s: float,
        exit_code: int,
        diagnostic_timeout_s: float | None = None,
        stage_recorder: StageRecorder | None = None,
        exit_fn: ExitHandler | None = None,
    ) -> object: ...


__all__ = ["OverrunHandler"]

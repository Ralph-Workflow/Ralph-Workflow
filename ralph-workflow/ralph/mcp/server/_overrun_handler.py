"""OverrunHandler protocol for the bounded pre-serve path.

The class lives in its own module so ``_pre_serve_overrun`` conforms to the
repo-structure policy (at most one public top-level class per module). It is
re-exported from :mod:`ralph.mcp.server._pre_serve_overrun`, so every existing
importer keeps working unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.server._exit_handler import ExitHandler
    from ralph.mcp.server._stage_recorder import StageRecorder


class OverrunHandler(Protocol):
    """Callable that handles a pre-serve overrun condition."""

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

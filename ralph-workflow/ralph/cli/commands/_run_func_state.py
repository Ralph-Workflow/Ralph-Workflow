"""_RunFuncState — holds the lazily-loaded pipeline runner function."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.cli.commands.run import _RunnerFunc

_RUN_FUNC_UNSET: object = object()


class _RunFuncState:
    """Holds the lazily-loaded pipeline runner function."""

    def __init__(self) -> None:
        self.run_func: _RunnerFunc | None | object = _RUN_FUNC_UNSET


__all__ = ["_RUN_FUNC_UNSET", "_RunFuncState"]

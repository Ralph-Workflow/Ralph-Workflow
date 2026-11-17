from __future__ import annotations

_RUN_FUNC_UNSET: object = object()


class _RunFuncState:
    __slots__ = ("run_func",)

    def __init__(self) -> None:
        self.run_func: object = _RUN_FUNC_UNSET


__all__ = ["_RUN_FUNC_UNSET", "_RunFuncState"]

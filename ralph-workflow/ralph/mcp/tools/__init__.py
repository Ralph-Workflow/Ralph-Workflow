from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.tools._exec_jobs import (
        ExecJobError,
        ExecJobRegistry,
        JobResult,
        JobSnapshot,
        ProgressSubscription,
    )

_EXEC_JOB_EXPORTS = frozenset(
    {
        "ExecJobError",
        "ExecJobRegistry",
        "JobResult",
        "JobSnapshot",
        "ProgressSubscription",
    }
)


def __getattr__(name: str) -> object:
    if name in _EXEC_JOB_EXPORTS:
        module = importlib.import_module("ralph.mcp.tools._exec_jobs")
        exported: object = getattr(module, name)
        return exported
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(_EXEC_JOB_EXPORTS)


__all__ = [
    "ExecJobError",
    "ExecJobRegistry",
    "JobResult",
    "JobSnapshot",
    "ProgressSubscription",
]

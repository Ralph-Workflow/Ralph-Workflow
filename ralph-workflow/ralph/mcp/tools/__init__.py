"""MCP tool implementations Ralph exposes to agents.

This sub-package contains the handlers for every tool the Ralph MCP *server*
advertises to connected AI agents (read_file, write_file, git_status, exec,
submit_artifact, etc.). Ralph is the MCP *server* here.
"""

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
    """Resolve the exec-job public names lazily.

    The exec-job module imports sibling tool modules from this package, so the
    exec-job names are resolved on first attribute access instead of at
    package-import time (which would create an import cycle).
    """
    if name in _EXEC_JOB_EXPORTS:
        module = importlib.import_module("ralph.mcp.tools._exec_jobs")
        exported: object = getattr(module, name)
        return exported
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Return the public names exposed by this package facade."""
    return sorted(_EXEC_JOB_EXPORTS)


__all__ = [
    "ExecJobError",
    "ExecJobRegistry",
    "JobResult",
    "JobSnapshot",
    "ProgressSubscription",
]

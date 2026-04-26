"""Parallel execution mode definitions for same-workspace parallel workers v1."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.interrupt.asyncio_bridge import SignalBridge
    from ralph.mcp.server.factory import McpServerFactory


class ParallelExecutionMode(StrEnum):
    """Supported parallel execution modes.

    In v1 only SAME_WORKSPACE is supported. Workers share the single checked-out
    repository root and are isolated only by edit-area path restrictions and
    per-worker artifact namespaces — not by filesystem or git-worktree isolation.
    """

    SAME_WORKSPACE = "same_workspace"


@dataclass(frozen=True)
class SameWorkspaceContext:
    """Runtime context for same-workspace parallel execution.

    Workers run against ``repo_root`` directly. Per-worker mutable state lives
    under ``worker_namespace_root / <unit_id> / {artifacts,tmp,logs,handoffs}``.
    There is no worktree creation, no per-worker branch, and no merge-back step.
    """

    repo_root: Path
    mcp_factory: McpServerFactory
    executor_command: tuple[str, ...] | None = None
    signal_bridge: SignalBridge | None = None
    worker_namespace_root: Path | None = None

    def __post_init__(self) -> None:
        if self.worker_namespace_root is None:
            object.__setattr__(
                self,
                "worker_namespace_root",
                self.repo_root / ".agent" / "workers",
            )


__all__ = ["ParallelExecutionMode", "SameWorkspaceContext"]

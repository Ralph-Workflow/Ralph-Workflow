
from __future__ import annotations

import weakref
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from ralph.agents.invoke._workspace_change_coalescer import WorkspaceChangeCoalescer
from ralph.agents.invoke.watchdog_event_queue import DroppingEventQueue
from ralph.workspace._shared_awareness import SharedAwarenessSidecar
from ralph.workspace.awareness import awareness_for_workspace

if TYPE_CHECKING:
    from queue import Queue

    from ralph.agents.invoke._workspace import WorkspaceMonitor
    from ralph.agents.invoke.watchdog_observer import (
        WatchObserverFactory,
        _WatchObserverProtocol,
    )

SharedAwarenessSidecarFactory = Callable[[Path], SharedAwarenessSidecar]

SharedAwarenessSidecarRemover = Callable[[Path], None]


class _CrossProcessWatchLockProtocol(Protocol):

    def try_acquire(self, workspace_root: Path) -> str | None:
        ...

    def claimed_owner_id(self, workspace_root: Path) -> str | None:
        ...

    def last_released_holder(self, workspace_root: Path) -> str | None:
        ...

    def release(self, workspace_root: Path, owner_id: str) -> None:
        ...


@dataclass(frozen=True)
class WorkspaceWatchSeams:

    watch_lock: _CrossProcessWatchLockProtocol | None = None
    observer_factory: WatchObserverFactory | None = None
    sidecar_factory: SharedAwarenessSidecarFactory | None = None
    sidecar_remover: SharedAwarenessSidecarRemover | None = None
    stop_observer_budget_seconds: float | None = None


class _SharedWorkspaceWatch:

    def __init__(
        self,
        observer: _WatchObserverProtocol,
        handler: object,
        *,
        cross_process_owner_id: str | None = None,
    ) -> None:
        self.observer = observer
        self.handler = handler
        self.monitors: weakref.WeakSet[WorkspaceMonitor] = weakref.WeakSet()
        self.cross_process_owner_id = cross_process_owner_id


def shared_workspace_key(workspace: Path) -> str:
    return str(workspace.absolute())


def _is_within_workspace(workspace: Path, src_path: str) -> bool:
    try:
        Path(src_path).resolve(strict=False).relative_to(workspace.resolve(strict=False))
    except ValueError:
        return False
    return True


def make_change_tracker(
    workspace: Path, deliver: Callable[[str], None]
) -> WorkspaceChangeCoalescer:
    return WorkspaceChangeCoalescer(
        deliver=deliver,
        is_within=lambda path: _is_within_workspace(workspace, path),
        on_overflow=lambda: awareness_for_workspace(workspace).set_freshness(
            "stale", cause="workspace_event_overflow"
        ),
    )


def _mark_workspace_event_queue_overflow(workspace: Path) -> None:
    awareness_for_workspace(workspace).set_freshness(
        "stale", cause="workspace_event_queue_overflow"
    )


def bind_observer_overflow(observer: _WatchObserverProtocol, workspace: Path) -> None:
    event_queue = cast("Queue[object] | None", getattr(observer, "event_queue", None))
    if isinstance(event_queue, DroppingEventQueue):
        event_queue.set_overflow_callback(partial(_mark_workspace_event_queue_overflow, workspace))

"""Injectable collaborators for the workspace watch lifecycle.

Starting a workspace watch reaches for collaborators that live outside
the monitor: the cross-process watch lock that keeps two processes from
registering overlapping observers, the factory that builds the watchdog
observer, and the shared-awareness sidecar the owner publishes to and
the non-owner polls. Every one of them is a boundary a caller has to be
able to replace -- a test has to be able to drive the coordination
contract without a lock file, a watchdog observer or a sidecar file --
so the contracts they answer to, the container that carries them, and
the shared-watch state they hang off live here rather than inside the
monitor's own module.
"""

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

#: Returns the shared-awareness sidecar for one workspace root.
SharedAwarenessSidecarFactory = Callable[[Path], SharedAwarenessSidecar]

#: Removes the shared-awareness sidecar file at final teardown.
SharedAwarenessSidecarRemover = Callable[[Path], None]


class _CrossProcessWatchLockProtocol(Protocol):
    """Describe the cross-process watch-lock surface the monitor coordinates through.

    The production implementation is
    :class:`ralph.workspace._cross_process_watch_lock.CrossProcessWatchLock`,
    whose members are classmethods. The monitor calls only these four,
    so any object providing them can be injected -- which is how the
    cross-process coordination contract is exercised without touching
    the filesystem.
    """

    def try_acquire(self, workspace_root: Path) -> str | None:
        """Claim the lock, or return the current holder's owner id."""
        ...

    def claimed_owner_id(self, workspace_root: Path) -> str | None:
        """Return this process's owner id for the workspace, when it holds the lock."""
        ...

    def last_released_holder(self, workspace_root: Path) -> str | None:
        """Return the most recently released holder id for the workspace."""
        ...

    def release(self, workspace_root: Path, owner_id: str) -> None:
        """Release the lock when ``owner_id`` matches the recorded holder."""
        ...


@dataclass(frozen=True)
class WorkspaceWatchSeams:
    """The collaborators a workspace watch reaches for, replaceable by a caller.

    Every field defaults to ``None``, which means "use the production
    collaborator". A caller that supplies its own lock, observer factory
    or sidecar drives the watch lifecycle without a lock file, a real
    watchdog observer or a sidecar file, and every production default
    stays exactly what it was.
    """

    watch_lock: _CrossProcessWatchLockProtocol | None = None
    observer_factory: WatchObserverFactory | None = None
    sidecar_factory: SharedAwarenessSidecarFactory | None = None
    sidecar_remover: SharedAwarenessSidecarRemover | None = None
    stop_observer_budget_seconds: float | None = None


class _SharedWorkspaceWatch:
    """One observer plus weak leases for a canonical workspace root.

    ``cross_process_owner_id`` tracks the advisory-lock owner so the
    cross-process lock is released exactly when the shared observer is
    torn down (the final in-process lease), not when any single lease
    stops (S-10 / DA-001 / DA-009).
    """

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
    """Return the lexical root key without probing a possibly absent workspace."""
    return str(workspace.absolute())


def _is_within_workspace(workspace: Path, src_path: str) -> bool:
    """Return whether a delivered event path remains inside the monitor root."""
    try:
        Path(src_path).resolve(strict=False).relative_to(workspace.resolve(strict=False))
    except ValueError:
        return False
    return True


def make_change_tracker(
    workspace: Path, deliver: Callable[[str], None]
) -> WorkspaceChangeCoalescer:
    """Build the coalescer that feeds observed changes to the owning lease.

    Args:
        workspace: The monitored root, used to keep out-of-root events out.
        deliver: Called with each in-root changed path.
    """
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
    """Route the observer's event-queue overflow to the workspace freshness state."""
    event_queue = cast("Queue[object] | None", getattr(observer, "event_queue", None))
    if isinstance(event_queue, DroppingEventQueue):
        event_queue.set_overflow_callback(partial(_mark_workspace_event_queue_overflow, workspace))

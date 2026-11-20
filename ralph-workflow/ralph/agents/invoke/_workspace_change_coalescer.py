from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ralph.agents.invoke._has_dest_path import _HasDestPath
from ralph.agents.invoke._has_src_path import _HasSrcPath

if TYPE_CHECKING:
    from collections.abc import Callable


@runtime_checkable
class _HasEventType(Protocol):
    event_type: str


CHANGE_DRAIN_INTERVAL_SECONDS = 0.1

MAX_PENDING_CHANGES = 4096

ACCESS_ONLY_EVENT_TYPES: frozenset[str] = frozenset({"opened", "closed_no_write"})


class WorkspaceChangeCoalescer:
    __slots__ = (
        "__weakref__",
        "_closed",
        "_deliver",
        "_deliver_access",
        "_drain_finished",
        "_draining",
        "_is_within",
        "_lock",
        "_max_pending",
        "_on_overflow",
        "_overflowed",
        "_pending",
        "_timer",
    )

    def __init__(
        self,
        deliver: Callable[[str], None],
        is_within: Callable[[str], bool],
        on_overflow: Callable[[], None],
        *,
        max_pending: int = MAX_PENDING_CHANGES,
        deliver_access: Callable[[str], None] | None = None,
    ) -> None:
        self._deliver = deliver
        self._deliver_access = deliver_access
        self._is_within = is_within
        self._on_overflow = on_overflow
        self._max_pending = max_pending
        self._lock = threading.Lock()
        self._pending: dict[tuple[str, str | None], bool] = {}
        self._overflowed = False
        self._timer: threading.Timer | None = None
        self._draining = False
        self._drain_finished = threading.Event()
        self._drain_finished.set()
        self._closed = False

    def _schedule_drain_locked(self) -> None:
        self._timer = threading.Timer(CHANGE_DRAIN_INTERVAL_SECONDS, self.flush)
        self._timer.daemon = True
        self._timer.start()

    def dispatch(self, event: object) -> None:
        self.on_any_event(event)

    def on_any_event(self, event: object) -> None:
        if not isinstance(event, _HasSrcPath):
            return
        dest_path = event.dest_path if isinstance(event, _HasDestPath) else None
        mutating = not (
            isinstance(event, _HasEventType) and event.event_type in ACCESS_ONLY_EVENT_TYPES
        )
        with self._lock:
            if self._closed:
                return
            change = (event.src_path, dest_path)
            if change in self._pending or len(self._pending) < self._max_pending:
                self._pending[change] = self._pending.get(change, False) or mutating
            else:
                self._overflowed = True
            if self._timer is None and not self._draining:
                self._schedule_drain_locked()

    def flush(self) -> None:
        with self._lock:
            if self._closed or self._draining:
                return
            self._timer = None
            self._draining = True
            self._drain_finished.clear()
            changes = list(self._pending.items())
            self._pending.clear()
            overflowed, self._overflowed = self._overflowed, False
        try:
            if overflowed:
                self._on_overflow()
            for (src_path, dest_path), mutating in changes:
                deliver = (
                    self._deliver
                    if mutating or self._deliver_access is None
                    else self._deliver_access
                )
                if self._is_within(src_path):
                    deliver(src_path)
                elif dest_path is not None and self._is_within(dest_path):
                    deliver(dest_path)
        finally:
            with self._lock:
                self._draining = False
                self._drain_finished.set()
                if not self._closed and self._pending and self._timer is None:
                    self._schedule_drain_locked()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._pending.clear()
        self._drain_finished.wait()

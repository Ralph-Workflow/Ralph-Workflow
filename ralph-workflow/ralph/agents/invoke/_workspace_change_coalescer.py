"""Absorb watchdog events at O(1) cost and classify them in batches.

watchdog queues every filesystem event without bound and dispatches them on
a single thread. Classifying one event there costs hundreds of microseconds
(path resolution, the change classifier, awareness bookkeeping), which caps
dispatch near 1-2k events/s; a busy build emits more, so the queue grew
without limit and long Ralph runs reached many GB. The handler now only
records the change: repeated writes to one file coalesce, and a timer
drains the distinct set off the queue.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from ralph.agents.invoke._has_dest_path import _HasDestPath
from ralph.agents.invoke._has_src_path import _HasSrcPath

if TYPE_CHECKING:
    from collections.abc import Callable

#: How long observed changes are coalesced before the classifying drain.
CHANGE_DRAIN_INTERVAL_SECONDS = 0.1

#: Distinct pending changes held between drains. Beyond it ``on_overflow``
#: runs (consumers reconcile) instead of the set growing without bound.
MAX_PENDING_CHANGES = 4096


class WorkspaceChangeCoalescer:
    """watchdog handler that defers per-event work to a batched drain.

    Args:
        deliver: Records one in-workspace path (the expensive part).
        is_within: Whether a path belongs to the watched workspace.
        on_overflow: Called on a drain after changes were dropped at the cap.
        max_pending: Distinct changes retained between drains.
    """

    def __init__(
        self,
        deliver: Callable[[str], None],
        is_within: Callable[[str], bool],
        on_overflow: Callable[[], None],
        *,
        max_pending: int = MAX_PENDING_CHANGES,
    ) -> None:
        self._deliver = deliver
        self._is_within = is_within
        self._on_overflow = on_overflow
        self._max_pending = max_pending
        self._lock = threading.Lock()
        self._pending: dict[tuple[str, str | None], None] = {}  # bounded-accumulator-ok: capped
        self._overflowed = False
        self._timer: threading.Timer | None = None
        self._closed = False

    def dispatch(self, event: object) -> None:
        """watchdog entry point."""
        self.on_any_event(event)

    def on_any_event(self, event: object) -> None:
        """Record the event's paths; classification happens in :meth:`flush`."""
        if not isinstance(event, _HasSrcPath):
            return
        dest_path = event.dest_path if isinstance(event, _HasDestPath) else None
        with self._lock:
            if self._closed:
                return
            change = (event.src_path, dest_path)
            if change in self._pending or len(self._pending) < self._max_pending:
                self._pending[change] = None
            else:
                self._overflowed = True
            if self._timer is None:
                self._timer = threading.Timer(CHANGE_DRAIN_INTERVAL_SECONDS, self.flush)
                self._timer.daemon = True
                self._timer.start()

    def flush(self) -> None:
        """Deliver every pending change: the source path, else an in-root destination."""
        with self._lock:
            self._timer = None
            changes = list(self._pending)
            self._pending.clear()
            overflowed, self._overflowed = self._overflowed, False
        if overflowed:
            self._on_overflow()
        for src_path, dest_path in changes:
            if self._is_within(src_path):
                self._deliver(src_path)
            elif dest_path is not None and self._is_within(dest_path):
                self._deliver(dest_path)

    def close(self) -> None:
        """Stop accepting events and cancel any scheduled drain."""
        with self._lock:
            self._closed = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._pending.clear()

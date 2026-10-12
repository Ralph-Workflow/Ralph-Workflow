"""Rate-limit the watch owner's shared-awareness sidecar rewrites.

Each rewrite serializes the whole published path list and its temp-file +
rename raise further watch events. Publishing once per observed change let a
busy build outpace the event handler (and grow watchdog's unbounded queue),
so the first change publishes immediately and the rest of a burst is
coalesced into one trailing rewrite.
"""

from __future__ import annotations

import threading
import weakref
from contextlib import suppress
from typing import TYPE_CHECKING

from ralph.workspace._shared_awareness import SharedAwarenessError

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.workspace._shared_awareness import SharedAwarenessSidecar

#: Minimum spacing between sidecar rewrites.
SIDECAR_PUBLISH_INTERVAL_SECONDS = 0.25

#: Pending paths that force an immediate publish (the sidecar's own bound).
MAX_PENDING_SIDECAR_PATHS = 512

#: Cause handed to ``on_failure`` when a rewrite fails.
SIDECAR_FAILURE_CAUSE = "shared_awareness_io_failed"


class ThrottledSidecarPublisher:
    """Coalesce workspace-relative changes into spaced sidecar publications.

    Args:
        sidecar: Returns the workspace's sidecar (resolved per publish).
        now: Monotonic clock.
        on_failure: Bound method called with :data:`SIDECAR_FAILURE_CAUSE`
            after a failed publish. It is held weakly so the owning monitor,
            which holds this publisher, is still freed by reference counting
            (monitors are tracked in weak sets).
    """

    def __init__(
        self,
        sidecar: Callable[[], SharedAwarenessSidecar],
        now: Callable[[], float],
        on_failure: Callable[[str], None],
    ) -> None:
        self._sidecar = sidecar
        self._now = now
        self._on_failure = weakref.WeakMethod(on_failure)
        self._lock = threading.Lock()
        self._pending: dict[str, None] = {}  # bounded-accumulator-ok: drained per publish
        self._last_publish: float | None = None
        self._timer: threading.Timer | None = None
        self._open = True

    def add(self, relative_path: str) -> None:
        """Queue one change; publish now when the interval has elapsed."""
        with self._lock:
            if not self._open:
                return
            self._pending[relative_path] = None
            last = self._last_publish
            due = (
                last is None
                or self._now() - last >= SIDECAR_PUBLISH_INTERVAL_SECONDS
                or len(self._pending) >= MAX_PENDING_SIDECAR_PATHS
            )
            if not due:
                if self._timer is None:
                    self._timer = threading.Timer(SIDECAR_PUBLISH_INTERVAL_SECONDS, self.flush)
                    self._timer.daemon = True
                    self._timer.start()
                return
        self.flush()

    def flush(self) -> None:
        """Publish every pending change in one rewrite.

        Publishing under the lock lets :meth:`close` wait out an in-flight
        flush, so a timer never rewrites a sidecar that teardown removed.
        """
        with self._lock:
            self._timer = None
            paths = list(self._pending)
            self._pending.clear()
            if not paths or not self._open:
                return
            self._last_publish = self._now()
            sidecar = self._sidecar()
            try:
                sidecar.publish_changes(paths)
            except SharedAwarenessError as exc:
                with suppress(SharedAwarenessError):
                    sidecar.publish_error(str(exc))
                failed = True
            else:
                failed = False
        callback = self._on_failure() if failed else None
        if callback is not None:
            callback(SIDECAR_FAILURE_CAUSE)

    def close(self) -> None:
        """Drop pending changes and cancel any trailing publish."""
        with self._lock:
            self._open = False
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._pending.clear()

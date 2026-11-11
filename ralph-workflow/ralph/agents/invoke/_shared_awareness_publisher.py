
from __future__ import annotations

import threading
import weakref
from contextlib import suppress
from typing import TYPE_CHECKING

from ralph.workspace._shared_awareness import SharedAwarenessError

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.workspace._shared_awareness import SharedAwarenessSidecar

SIDECAR_PUBLISH_INTERVAL_SECONDS = 0.25

MAX_PENDING_SIDECAR_PATHS = 512

SIDECAR_FAILURE_CAUSE = "shared_awareness_io_failed"


class ThrottledSidecarPublisher:

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
        self._pending: dict[str, None] = {}
        self._last_publish: float | None = None
        self._timer: threading.Timer | None = None
        self._open = True

    def add(self, relative_path: str) -> None:
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
        with self._lock:
            self._open = False
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._pending.clear()

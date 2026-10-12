"""Regression tests for unbounded memory growth under heavy file churn.

watchdog queues every inotify event without bound and dispatches them on one
thread. Per-event classification there, plus a shared-awareness sidecar
rewrite per change, capped the handler at a few hundred events/s while busy
builds emit thousands, so long Ralph runs grew to many GB. Dispatch now only
records the change, and sidecar rewrites are coalesced.
"""

from __future__ import annotations

import threading
from pathlib import Path

from ralph.agents.invoke._shared_awareness_publisher import ThrottledSidecarPublisher
from ralph.agents.invoke._workspace import _create_watchdog_observer, _DroppingEventQueue
from ralph.agents.invoke._workspace_change_coalescer import WorkspaceChangeCoalescer
from ralph.workspace._shared_awareness import SharedAwarenessError, SharedAwarenessSidecar


class _Event:
    def __init__(self, src_path: str, dest_path: str = "") -> None:
        self.src_path = src_path
        self.dest_path = dest_path


def _coalescer(
    root: Path, recorded: list[str], overflows: list[bool], *, max_pending: int = 4096
) -> WorkspaceChangeCoalescer:
    return WorkspaceChangeCoalescer(
        deliver=recorded.append,
        is_within=lambda path: Path(path).is_relative_to(root),
        on_overflow=lambda: overflows.append(True),
        max_pending=max_pending,
    )


def test_dispatch_defers_work_and_coalesces_repeated_writes(tmp_path: Path) -> None:
    """Dispatch does no per-event work; repeated writes to a file drain once."""
    recorded: list[str] = []
    coalescer = _coalescer(tmp_path, recorded, [])
    path = str(tmp_path / "src" / "app.py")

    for _ in range(1000):
        coalescer.dispatch(_Event(path))
    assert recorded == []

    coalescer.flush()
    assert recorded == [path]
    coalescer.close()


def test_moves_into_the_workspace_record_their_destination(tmp_path: Path) -> None:
    """An in-root move destination is recorded when the source is outside."""
    recorded: list[str] = []
    coalescer = _coalescer(tmp_path / "ws", recorded, [])
    destination = str(tmp_path / "ws" / "moved.py")

    coalescer.dispatch(_Event(str(tmp_path / "elsewhere.py"), destination))
    coalescer.flush()

    assert recorded == [destination]
    coalescer.close()


def test_pending_changes_are_capped_and_overflow_is_reported(tmp_path: Path) -> None:
    """Past the cap, changes are not retained and the overflow is reported."""
    recorded: list[str] = []
    overflows: list[bool] = []
    coalescer = _coalescer(tmp_path, recorded, overflows, max_pending=3)

    for index in range(10):
        coalescer.dispatch(_Event(str(tmp_path / f"f{index}.py")))
    coalescer.flush()

    assert len(recorded) == 3
    assert overflows == [True]
    coalescer.close()


def test_closed_coalescer_drops_late_events(tmp_path: Path) -> None:
    """Events arriving after teardown never schedule a drain."""
    recorded: list[str] = []
    coalescer = _coalescer(tmp_path, recorded, [])
    coalescer.close()

    coalescer.dispatch(_Event(str(tmp_path / "late.py")))
    coalescer.flush()

    assert recorded == []


def test_blocked_drain_never_spawns_concurrent_drain_threads(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    active = 0
    peak = 0
    active_lock = threading.Lock()

    def deliver(_path: str) -> None:
        nonlocal active, peak
        with active_lock:
            active += 1
            peak = max(peak, active)
        entered.set()
        release.wait(timeout=5)
        with active_lock:
            active -= 1

    coalescer = WorkspaceChangeCoalescer(
        deliver=deliver,
        is_within=lambda _path: True,
        on_overflow=lambda: None,
        max_pending=1,
    )
    coalescer.dispatch(_Event(str(tmp_path / "first.py")))
    assert entered.wait(timeout=2)

    for index in range(4):
        coalescer.dispatch(_Event(str(tmp_path / f"next-{index}.py")))
        threading.Event().wait(0.12)

    assert peak == 1
    release.set()
    coalescer.close()


def test_watchdog_queue_has_hard_backpressure_cap() -> None:
    observer = _create_watchdog_observer()

    assert observer is not None
    assert observer.event_queue.maxsize > 0


def test_watchdog_queue_drops_without_blocking_and_reports_overflow() -> None:
    overflows: list[bool] = []
    event_queue = _DroppingEventQueue(maxsize=1)
    event_queue.set_overflow_callback(lambda: overflows.append(True))

    event_queue.put("first")
    event_queue.put("second")

    assert event_queue.get_nowait() == "second"
    assert overflows == [True]


class _Sidecar(SharedAwarenessSidecar):
    def __init__(self, root: Path, *, fail: bool = False) -> None:
        super().__init__(root)
        self.published: list[list[str]] = []
        self.errors: list[str] = []
        self._fail = fail

    def publish_changes(self, paths: list[str], *, overflowed: bool = False) -> int:
        del overflowed
        if self._fail:
            raise SharedAwarenessError("disk full")
        self.published.append(paths)
        return len(self.published)

    def publish_error(self, cause: str) -> None:
        self.errors.append(cause)


class _Owner:
    """Stands in for the monitor, whose bound method the publisher holds weakly."""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def fall_back(self, cause: str) -> None:
        self.failures.append(cause)


def test_sidecar_burst_publishes_first_change_then_one_coalesced_rewrite(tmp_path: Path) -> None:
    """A burst rewrites the sidecar twice, not once per change."""
    sidecar = _Sidecar(tmp_path)
    owner = _Owner()
    clock = [100.0]
    publisher = ThrottledSidecarPublisher(
        sidecar=lambda: sidecar, now=lambda: clock[0], on_failure=owner.fall_back
    )

    publisher.add("src/a.py")
    for name in ("b", "c", "b"):
        publisher.add(f"src/{name}.py")
    assert sidecar.published == [["src/a.py"]]

    publisher.flush()  # the trailing timer's flush
    assert sidecar.published == [["src/a.py"], ["src/b.py", "src/c.py"]]
    publisher.close()


def test_sidecar_publishes_again_once_the_interval_elapses(tmp_path: Path) -> None:
    """Changes spaced beyond the interval each publish immediately."""
    sidecar = _Sidecar(tmp_path)
    owner = _Owner()
    clock = [100.0]
    publisher = ThrottledSidecarPublisher(
        sidecar=lambda: sidecar, now=lambda: clock[0], on_failure=owner.fall_back
    )

    publisher.add("src/a.py")
    clock[0] += 1.0
    publisher.add("src/b.py")

    assert sidecar.published == [["src/a.py"], ["src/b.py"]]
    publisher.close()


def test_sidecar_failure_is_recorded_and_reported(tmp_path: Path) -> None:
    """A failed rewrite records the error on the sidecar and notifies the owner."""
    sidecar = _Sidecar(tmp_path, fail=True)
    owner = _Owner()
    publisher = ThrottledSidecarPublisher(
        sidecar=lambda: sidecar, now=lambda: 0.0, on_failure=owner.fall_back
    )

    publisher.add("src/a.py")

    assert sidecar.errors == ["disk full"]
    assert owner.failures == ["shared_awareness_io_failed"]
    publisher.close()


def test_closed_publisher_never_rewrites_the_sidecar(tmp_path: Path) -> None:
    """A trailing flush after teardown must not recreate a removed sidecar."""
    sidecar = _Sidecar(tmp_path)
    owner = _Owner()
    clock = [100.0]
    publisher = ThrottledSidecarPublisher(
        sidecar=lambda: sidecar, now=lambda: clock[0], on_failure=owner.fall_back
    )
    publisher.add("src/a.py")
    publisher.add("src/b.py")
    publisher.close()

    publisher.flush()

    assert sidecar.published == [["src/a.py"]]

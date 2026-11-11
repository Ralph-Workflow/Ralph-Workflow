from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.invoke._watch_capacity import CAPACITY_PROBE_BUDGET_SECONDS, call_within_budget
from ralph.agents.invoke._workspace import STOP_OBSERVER_BUDGET_SECONDS, WorkspaceMonitor
from ralph.agents.invoke._workspace_seams import WorkspaceWatchSeams
from ralph.workspace.awareness import awareness_for_workspace, release_workspace_awareness

if TYPE_CHECKING:
    import pytest

_PROBE_BUDGET_SECONDS = 0.05

_HUNG_PROBE_SECONDS = 20.0

_SLOW_WATCH_SECONDS = 0.5


def _hung_counter(workspace: Path, cap: int) -> int | None:
    del workspace, cap
    threading.Event().wait(timeout=_HUNG_PROBE_SECONDS)
    return 0


def test_a_capacity_probe_that_never_answers_does_not_park_the_start(
    tmp_path: Path,
) -> None:
    monitor = WorkspaceMonitor(
        tmp_path,
        host_budget=8192,
        directory_counter=_hung_counter,
        probe_budget_seconds=_PROBE_BUDGET_SECONDS,
    )
    try:
        monitor.start()
        status = awareness_for_workspace(tmp_path).snapshot()
    finally:
        monitor.stop()
        release_workspace_awareness(tmp_path)

    assert status["mode"] == "live_fallback"


class _CompletedProbeThread:
    def __init__(self, *, target: Callable[[], None], name: str, daemon: bool) -> None:
        del name, daemon
        self._target = target

    def start(self) -> None:
        self._target()

    def join(self, timeout: float | None = None) -> None:
        del timeout
        msg = "completed probe results must use explicit publication, not Thread.join"
        raise AssertionError(msg)


def test_capacity_probe_regression_completed_answer_is_published_without_join_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ralph.agents.invoke._watch_capacity.threading.Thread", _CompletedProbeThread
    )

    result = call_within_budget(
        lambda: False,
        fallback=True,
        budget_seconds=_PROBE_BUDGET_SECONDS,
    )

    assert result is False


class _SlowObserver:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.finished = threading.Event()
        self.join_timeouts: list[float | None] = []

    def schedule(self, event_handler: object, path: str, **kwargs: object) -> None:
        del event_handler, path, kwargs

    def start(self) -> None:
        self.entered.set()
        threading.Event().wait(timeout=_SLOW_WATCH_SECONDS)
        self.finished.set()

    def stop(self) -> None:
        return

    def join(self, timeout: float | None = None) -> None:
        self.join_timeouts.append(timeout)

    def is_alive(self) -> bool:
        return False


def test_a_slow_watch_start_does_not_park_the_launch(
    tmp_path: Path,
) -> None:
    observer = _SlowObserver()
    monitor = WorkspaceMonitor(
        tmp_path,
        host_budget=8192,
        directory_counter=lambda workspace, cap: 1,
        live_watch_total=0,
        probe_budget_seconds=_PROBE_BUDGET_SECONDS,
        seams=WorkspaceWatchSeams(observer_factory=lambda: observer),
    )
    try:
        monitor.start()
        still_walking = not observer.finished.is_set()
        status = awareness_for_workspace(tmp_path).snapshot()
    finally:
        monitor.stop()
        release_workspace_awareness(tmp_path)

    assert observer.entered.is_set(), "the watch start was never reached"
    assert still_walking, "the launch waited for the whole recursive walk"
    assert status["mode"] == "live_fallback"


def test_the_shipped_probe_budget_is_finite_and_small() -> None:
    assert 0.0 < CAPACITY_PROBE_BUDGET_SECONDS <= 30.0
    assert 0.0 < STOP_OBSERVER_BUDGET_SECONDS <= 30.0


class _SlowWatchLock:
    def __init__(self, *, stall: bool = True, holder: str | None = None) -> None:
        self.entered = threading.Event()
        self.finished = threading.Event()
        self.released = False
        self.stall = stall
        self.holder = holder

    def try_acquire(self, workspace: Path) -> str | None:
        del workspace
        self.entered.set()
        if self.stall:
            threading.Event().wait(timeout=_SLOW_WATCH_SECONDS)
        self.finished.set()
        return self.holder

    def claimed_owner_id(self, workspace: Path) -> str | None:
        del workspace
        return "owner-1"

    def last_released_holder(self, workspace: Path) -> str | None:
        del workspace
        return None

    def release(self, workspace: Path, owner_id: str) -> None:
        del workspace, owner_id
        self.released = True


def test_slow_watch_lock_io_does_not_park_the_launch(tmp_path: Path) -> None:
    lock = _SlowWatchLock()
    monitor = WorkspaceMonitor(
        tmp_path,
        host_budget=8192,
        directory_counter=lambda workspace, cap: 1,
        live_watch_total=0,
        probe_budget_seconds=_PROBE_BUDGET_SECONDS,
        seams=WorkspaceWatchSeams(watch_lock=lock),
    )
    try:
        monitor.start()
        still_waiting = not lock.finished.is_set()
        status = awareness_for_workspace(tmp_path).snapshot()
    finally:
        monitor.stop()
        release_workspace_awareness(tmp_path)

    assert lock.entered.is_set(), "the watch lock was never reached"
    assert still_waiting, "the launch waited for the whole lock acquisition"
    assert status["mode"] == "live_fallback"


def test_abandoning_a_slow_watch_start_releases_the_cross_process_lock(tmp_path: Path) -> None:
    lock = _SlowWatchLock(stall=False)
    observer = _SlowObserver()
    monitor = WorkspaceMonitor(
        tmp_path,
        host_budget=8192,
        directory_counter=lambda workspace, cap: 1,
        live_watch_total=0,
        probe_budget_seconds=_PROBE_BUDGET_SECONDS,
        seams=WorkspaceWatchSeams(
            watch_lock=lock,
            observer_factory=lambda: observer,
            sidecar_factory=lambda _root: _FastSidecar(),
        ),
    )
    try:
        monitor.start()
    finally:
        monitor.stop()
        release_workspace_awareness(tmp_path)

    assert observer.entered.is_set(), "the watch start was never reached"
    assert lock.released, "the abandoned watch kept its cross-process claim"


class _FastSidecar:
    def begin_ownership(self, owner_id: str, *, prior_holder: str | None = None) -> int:
        del owner_id, prior_holder
        return 1

    def publish_changes(self, *args: object, **kwargs: object) -> int:
        del args, kwargs
        return 1

    def claim_epoch(self, epoch: int) -> None:
        del epoch

    def end_ownership(self) -> None:
        return None


class _SlowStoppingObserver(_SlowObserver):
    def __init__(self) -> None:
        super().__init__()
        self.stopping = threading.Event()
        self.stopped = threading.Event()
        self.release_event = threading.Event()

    def start(self) -> None:
        self.entered.set()

    def stop(self) -> None:
        self.stopping.set()
        self.release_event.wait(timeout=_SLOW_WATCH_SECONDS)
        self.stopped.set()


def test_a_teardown_that_will_not_finish_does_not_park_the_launch(tmp_path: Path) -> None:
    observer = _SlowStoppingObserver()
    monitor = WorkspaceMonitor(
        tmp_path,
        host_budget=8192,
        directory_counter=lambda workspace, cap: 1,
        live_watch_total=0,
        probe_budget_seconds=_PROBE_BUDGET_SECONDS,
        seams=WorkspaceWatchSeams(
            observer_factory=lambda: observer,
            stop_observer_budget_seconds=_PROBE_BUDGET_SECONDS,
        ),
    )
    try:
        monitor.start()
        monitor.stop()
        still_stopping = not observer.stopped.is_set()
    finally:
        observer.release_event.set()
        release_workspace_awareness(tmp_path)

    assert observer.stopping.is_set(), "the teardown was never reached"
    assert still_stopping, "the caller waited for the whole teardown"


class _SlowSidecar:
    def __init__(self) -> None:
        self.polling = threading.Event()
        self.polled = threading.Event()
        self.writing = threading.Event()
        self.written = threading.Event()
        self.release_event = threading.Event()

    def poll(self) -> object:
        self.polling.set()
        if not self.release_event.wait(timeout=1.0):
            raise RuntimeError("sidecar poll was not explicitly released")
        self.polled.set()
        return None

    def begin_ownership(self, owner_id: str, *, prior_holder: str | None = None) -> None:
        del owner_id, prior_holder
        self.writing.set()
        if not self.release_event.wait(timeout=1.0):
            raise RuntimeError("sidecar ownership write was not explicitly released")
        self.written.set()

    def publish_changes(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def release(self, *args: object, **kwargs: object) -> None:
        del args, kwargs


def test_a_slow_ownership_write_does_not_park_the_launch(tmp_path: Path) -> None:
    sidecar = _SlowSidecar()
    monitor = WorkspaceMonitor(
        tmp_path,
        host_budget=8192,
        directory_counter=lambda workspace, cap: 1,
        live_watch_total=0,
        probe_budget_seconds=_PROBE_BUDGET_SECONDS,
        seams=WorkspaceWatchSeams(
            watch_lock=_SlowWatchLock(stall=False),
            sidecar_factory=lambda _root: sidecar,
        ),
    )
    try:
        monitor.start()
        still_writing = not sidecar.written.is_set()
        status = awareness_for_workspace(tmp_path).snapshot()
    finally:
        sidecar.release_event.set()
        assert sidecar.written.wait(timeout=1.0), "the ownership write did not finish"
        monitor.stop()
        release_workspace_awareness(tmp_path)

    assert sidecar.writing.is_set(), "the ownership write was never reached"
    assert still_writing, "the launch waited for the whole ownership write"
    assert status["mode"] == "live_fallback"


def test_a_slow_owner_sidecar_read_does_not_park_the_launch(tmp_path: Path) -> None:
    sidecar = _SlowSidecar()
    monitor = WorkspaceMonitor(
        tmp_path,
        host_budget=8192,
        directory_counter=lambda workspace, cap: 1,
        live_watch_total=0,
        probe_budget_seconds=_PROBE_BUDGET_SECONDS,
        seams=WorkspaceWatchSeams(
            watch_lock=_SlowWatchLock(stall=False, holder="another-process"),
            sidecar_factory=lambda _root: sidecar,
        ),
    )
    try:
        monitor.start()
        still_polling = not sidecar.polled.is_set()
        status = awareness_for_workspace(tmp_path).snapshot()
    finally:
        sidecar.release_event.set()
        assert sidecar.polled.wait(timeout=1.0), "the owner sidecar read did not finish"
        monitor.stop()
        release_workspace_awareness(tmp_path)

    assert sidecar.polling.is_set(), "the owner sidecar was never polled"
    assert still_polling, "the launch waited for the whole sidecar read"
    assert status["mode"] == "live_fallback"


def test_the_watch_teardown_joins_with_a_timeout(tmp_path: Path) -> None:
    observer = _SlowStoppingObserver()
    monitor = WorkspaceMonitor(
        tmp_path,
        host_budget=8192,
        directory_counter=lambda workspace, cap: 1,
        live_watch_total=0,
        probe_budget_seconds=_PROBE_BUDGET_SECONDS,
        seams=WorkspaceWatchSeams(
            observer_factory=lambda: observer,
            stop_observer_budget_seconds=_PROBE_BUDGET_SECONDS,
        ),
    )
    try:
        monitor.start()
        monitor.stop()
    finally:
        observer.release_event.set()
        release_workspace_awareness(tmp_path)

    assert observer.join_timeouts, "the teardown never joined the observer"
    assert all(timeout is not None for timeout in observer.join_timeouts), (
        "the teardown joined the observer with no timeout"
    )

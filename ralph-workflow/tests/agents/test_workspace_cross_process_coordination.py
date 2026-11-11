from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke._workspace import WorkspaceMonitor
from ralph.agents.invoke._workspace_change_classifier import WorkspaceChangeClassifier
from ralph.agents.invoke._workspace_seams import WorkspaceWatchSeams
from ralph.workspace._shared_awareness import (
    SharedAwarenessError,
    SharedAwarenessSidecar,
    SharedAwarenessState,
    release_shared_awareness,
)
from ralph.workspace.awareness import release_workspace_awareness

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.invoke._workspace_seams import (
        _CrossProcessWatchLockProtocol as CrossProcessWatchLockProtocol,
    )
    from ralph.agents.invoke.watchdog_observer import WatchObserverFactory


class _FakeObserver:
    def __init__(self) -> None:
        self.scheduled: list[tuple[object, str, bool]] = []
        self.started: bool = False

    def schedule(self, event_handler: object, path: str, recursive: bool = False) -> None:
        self.scheduled.append((event_handler, path, recursive))

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        pass

    def join(self, timeout: float | None = None) -> None:
        del timeout


class _FakeCrossProcessWatchLock:
    def __init__(self, *, holder: str | None = None) -> None:
        self._holder = holder
        self.acquire_calls: list[Path] = []
        self.release_calls: list[tuple[Path, str]] = []
        self._counter = 0

    def try_acquire(self, workspace_root: Path) -> str | None:
        self.acquire_calls.append(workspace_root)
        if self._holder is None:
            self._counter += 1
            self._holder = f"us:{self._counter}"
            return None
        return self._holder

    def claimed_owner_id(self, workspace_root: Path) -> str | None:
        del workspace_root
        return self._holder

    def last_released_holder(self, workspace_root: Path) -> str | None:
        del workspace_root
        return None

    def release(self, workspace_root: Path, owner_id: str) -> None:
        self.release_calls.append((workspace_root, owner_id))
        if self._holder == owner_id:
            self._holder = None


@pytest.fixture(autouse=True)
def _cleanup_shared_workspace_state() -> Iterator[None]:
    yield
    WorkspaceMonitor._shared_watches.clear()
    release_workspace_awareness(Path("/ws"))
    release_shared_awareness(Path("/ws"))


class _FakeSharedAwarenessSidecar(SharedAwarenessSidecar):
    def __init__(self, workspace_root: Path) -> None:
        super().__init__(workspace_root)
        self.owner_id: str | None = None
        self._current_epoch = 0
        self.paths: list[str] = []
        self.overflowed = False
        self.error: str | None = None
        self.begin_calls: list[tuple[str, str | None]] = []
        self.publish_calls: list[tuple[list[str], bool]] = []
        self.poll_calls = 0
        self.claim_calls: list[int] = []

    @property
    def epoch(self) -> int:
        return self._current_epoch

    def begin_ownership(self, owner_id: str, *, prior_holder: str | None) -> int:
        self.begin_calls.append((owner_id, prior_holder))
        self.owner_id = owner_id
        self._current_epoch += 1
        return self._current_epoch

    def publish_changes(self, paths: list[str], *, overflowed: bool = False) -> int:
        self.publish_calls.append((paths, overflowed))
        for path in paths:
            if path not in self.paths:
                self.paths.append(path)
        self.overflowed = self.overflowed or overflowed
        self._current_epoch += 1
        return self._current_epoch

    def publish_error(self, cause: str) -> None:
        self.error = cause

    def end_ownership(self) -> None:
        self.owner_id = None

    def poll(self) -> SharedAwarenessState:
        self.poll_calls += 1
        if self.error is not None:
            raise SharedAwarenessError(self.error)
        return SharedAwarenessState(
            epoch=self._current_epoch,
            paths=list(self.paths),
            overflowed=self.overflowed,
            owner_id=self.owner_id or "unknown",
            changed=True,
        )

    def claim_epoch(self, epoch: int) -> None:
        self.claim_calls.append(epoch)


def _monitor(
    observer_factory: WatchObserverFactory,
    *,
    lock: CrossProcessWatchLockProtocol,
    sidecar: SharedAwarenessSidecar,
) -> WorkspaceMonitor:
    return WorkspaceMonitor(
        Path("/ws"),
        classifier=WorkspaceChangeClassifier(),
        host_budget=8192,
        directory_counter=_zero_directory_counter,
        live_watch_total=0,
        seams=WorkspaceWatchSeams(
            watch_lock=lock,
            observer_factory=observer_factory,
            sidecar_factory=lambda _root: sidecar,
            sidecar_remover=lambda _root: None,
        ),
    )


def _zero_directory_counter(workspace: Path, *, cap: int) -> int:
    del workspace, cap
    return 0


def test_cross_process_holder_blocks_start_and_reports_shared_awareness_consumer() -> None:
    fake_lock = _FakeCrossProcessWatchLock(holder="proc-99:1")
    fake = _FakeObserver()
    fake_sidecar = _FakeSharedAwarenessSidecar(Path("/ws"))
    fake_sidecar.owner_id = "proc-99:1"
    fake_sidecar.paths = ["src/app.py"]

    monitor = _monitor(
        lambda: fake,
        lock=fake_lock,
        sidecar=fake_sidecar,
    )
    monitor.start()

    assert len(fake.scheduled) == 0
    assert fake.started is False
    status = monitor.awareness_status
    assert status["mode"] == "shared_awareness"
    assert status["cause"] == "cross_process_holder"
    assert status["shared_owner"] == "proc-99:1"
    assert fake_sidecar.poll_calls == 1
    assert fake_sidecar.claim_calls == [fake_sidecar.epoch]
    assert monitor._cross_process_owner_id is None
    assert fake_lock.release_calls == []
    monitor.stop()


def test_free_lock_lets_start_schedule_one_observer_and_release_roundtrips() -> None:
    fake_lock = _FakeCrossProcessWatchLock(holder=None)
    observers: list[_FakeObserver] = []

    def _factory() -> _FakeObserver:
        observer = _FakeObserver()
        observers.append(observer)
        return observer

    monitor = _monitor(
        _factory,
        lock=fake_lock,
        sidecar=_FakeSharedAwarenessSidecar(Path("/ws")),
    )

    monitor.start()

    assert len(observers) == 1
    assert len(observers[0].scheduled) == 1
    _handler, path, recursive = observers[0].scheduled[0]
    assert path == "/ws"
    assert recursive is True
    assert observers[0].started is True
    owner_id = monitor._cross_process_owner_id
    assert owner_id is not None
    assert fake_lock.acquire_calls == [Path("/ws")]

    monitor.stop()

    assert fake_lock.release_calls == [(Path("/ws"), owner_id)]

    monitor.start()

    assert len(observers) == 2
    assert len(observers[1].scheduled) == 1
    assert observers[1].started is True
    monitor.stop()


def test_non_owner_stop_does_not_release_and_does_not_raise() -> None:
    fake_lock = _FakeCrossProcessWatchLock(holder="proc-99:1")
    fake = _FakeObserver()

    monitor = _monitor(
        lambda: fake,
        lock=fake_lock,
        sidecar=_FakeSharedAwarenessSidecar(Path("/ws")),
    )
    monitor.start()

    assert monitor._cross_process_owner_id is None

    monitor.stop()

    assert fake_lock.release_calls == []


def test_shared_lease_releases_cross_process_lock_only_at_final_stop() -> None:
    fake_lock = _FakeCrossProcessWatchLock(holder=None)
    fake = _FakeObserver()
    fake_sidecar = _FakeSharedAwarenessSidecar(Path("/ws"))

    monitor_a = _monitor(
        lambda: fake,
        lock=fake_lock,
        sidecar=fake_sidecar,
    )
    monitor_a.start()
    assert len(fake.scheduled) == 1
    assert fake_lock.acquire_calls == [Path("/ws")]

    monitor_b = _monitor(
        lambda: fake,
        lock=fake_lock,
        sidecar=fake_sidecar,
    )
    monitor_b.start()
    assert len(fake.scheduled) == 1
    assert len(fake_lock.acquire_calls) == 1

    monitor_a.stop()
    assert fake_lock.release_calls == []

    monitor_b.stop()
    assert len(fake_lock.release_calls) == 1
    released_workspace, released_owner = fake_lock.release_calls[0]
    assert released_workspace == Path("/ws")
    assert released_owner is not None


def test_owner_publishes_source_changes_to_sidecar() -> None:
    fake_lock = _FakeCrossProcessWatchLock(holder=None)
    fake = _FakeObserver()
    fake_sidecar = _FakeSharedAwarenessSidecar(Path("/ws"))

    monitor = _monitor(
        lambda: fake,
        lock=fake_lock,
        sidecar=fake_sidecar,
    )
    monitor.start()

    assert fake_sidecar.begin_calls, "owner must begin sidecar ownership"
    owner_id = monitor._cross_process_owner_id
    assert fake_sidecar.begin_calls[0][0] == owner_id

    epoch_before = fake_sidecar.epoch
    monitor.record_event("/ws/src/app.py")
    assert fake_sidecar.publish_calls == [(["src/app.py"], False)]
    assert fake_sidecar.epoch > epoch_before
    assert fake_sidecar.paths == ["src/app.py"]

    monitor.record_event("/ws/.agent/tmp/scratch.md")
    assert fake_sidecar.publish_calls == [(["src/app.py"], False)]
    monitor.stop()


def test_sidecar_write_failure_enters_live_fallback() -> None:

    class _FailingSidecar(_FakeSharedAwarenessSidecar):
        def publish_changes(self, paths: list[str], *, overflowed: bool = False) -> int:
            raise SharedAwarenessError("disk full")

    fake_lock = _FakeCrossProcessWatchLock(holder=None)
    fake = _FakeObserver()

    monitor = _monitor(
        lambda: fake,
        lock=fake_lock,
        sidecar=_FailingSidecar(Path("/ws")),
    )
    monitor.start()
    monitor.record_event("/ws/src/app.py")

    assert monitor.awareness_status["freshness"] == "live_fallback"
    assert monitor.awareness_status["cause"] == "shared_awareness_io_failed"
    monitor.stop()


def test_consumer_sidecar_read_failure_enters_live_fallback() -> None:
    fake_lock = _FakeCrossProcessWatchLock(holder="proc-99:1")
    fake = _FakeObserver()
    fake_sidecar = _FakeSharedAwarenessSidecar(Path("/ws"))
    fake_sidecar.error = "corrupt sidecar"

    monitor = _monitor(
        lambda: fake,
        lock=fake_lock,
        sidecar=fake_sidecar,
    )
    monitor.start()

    assert len(fake.scheduled) == 0
    status = monitor.awareness_status
    assert status["freshness"] == "live_fallback"
    assert status["cause"] == "shared_awareness_io_failed"
    monitor.stop()


def test_crash_takeover_restarts_sidecar_epoch(tmp_path: Path) -> None:
    from ralph.workspace._shared_awareness import (
        SharedAwarenessSidecar as RealSharedAwarenessSidecar,
    )
    from ralph.workspace._shared_awareness import (
        release_shared_awareness as release_real_shared_awareness,
    )

    workspace = tmp_path / "ws"
    workspace.mkdir()

    crashed = RealSharedAwarenessSidecar(workspace)
    crashed.begin_ownership("dead-proc:7", prior_holder=None)
    for _ in range(5):
        crashed.publish_changes(["stale.py"])
    crashed_epoch = crashed.epoch
    release_real_shared_awareness(workspace)

    new_owner = RealSharedAwarenessSidecar(workspace)
    new_epoch = new_owner.begin_ownership("us:1", prior_holder=None)
    try:
        assert new_epoch == 1, "stale owner id must restart the epoch"
        assert crashed_epoch > new_epoch
        state = new_owner.poll()
        assert state.owner_id == "us:1"
        assert state.paths == []
    finally:
        release_real_shared_awareness(workspace)

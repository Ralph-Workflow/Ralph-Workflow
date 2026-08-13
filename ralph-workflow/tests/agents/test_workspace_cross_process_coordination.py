"""S-10: cross-process workspace watch coordination.

Three black-box tests proving ``WorkspaceMonitor.start()`` consults a
``CrossProcessWatchLock`` before registering a recursive watchdog observer
so two independent processes do not each schedule their own watch on the
same workspace root. A fake lock is monkeypatched in so no real flock or
lock file is touched; the fake observer records ``schedule`` calls so the
tests can assert zero overlap.

Patterns follow ``tests/agents/test_workspace_watch_scoping.py`` (fake
observer + ``_create_watchdog_observer`` monkeypatch) and the held/free
assertion style from the awareness-status regression tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.agents.invoke._workspace import WorkspaceMonitor
from ralph.agents.invoke._workspace_change_classifier import WorkspaceChangeClassifier
from ralph.workspace.awareness import release_workspace_awareness
from ralph.workspace._shared_awareness import release_shared_awareness


class _FakeObserver:
    """Stand-in for ``watchdog.observers.Observer`` recording schedule calls."""

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
    """In-memory stand-in for ``CrossProcessWatchLock``.

    Starts either free (``holder=None``) or held by another process
    (``holder`` set to that process's owner id). Records every
    ``try_acquire`` and ``release`` call so tests can assert on wiring.
    """

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
def _cleanup_shared_workspace_state() -> None:
    """Clear shared watch and awareness state between tests."""
    yield
    WorkspaceMonitor._shared_watches.clear()
    release_workspace_awareness(Path("/ws"))
    release_shared_awareness(Path("/ws"))


class _FakeSharedAwarenessSidecar:
    """In-memory stand-in for the shared-awareness sidecar.

    Records every ``begin_ownership``, ``poll``, and ``claim_epoch`` call so
    tests can assert the owner publication and non-owner consumption
    contracts without touching the filesystem.
    """

    def __init__(self) -> None:
        self.owner_id: str | None = None
        self.epoch = 0
        self.paths: list[str] = []
        self.overflowed = False
        self.error: str | None = None
        self.begin_calls: list[tuple[str, str | None]] = []
        self.publish_calls: list[tuple[list[str], bool]] = []
        self.poll_calls = 0
        self.claim_calls: list[int] = []

    def begin_ownership(self, owner_id: str, *, prior_holder: str | None) -> int:
        self.begin_calls.append((owner_id, prior_holder))
        self.owner_id = owner_id
        self.epoch += 1
        return self.epoch

    def publish_changes(self, paths: list[str], *, overflowed: bool = False) -> int:
        self.publish_calls.append((paths, overflowed))
        for path in paths:
            if path not in self.paths:
                self.paths.append(path)
        self.overflowed = self.overflowed or overflowed
        self.epoch += 1
        return self.epoch

    def publish_error(self, cause: str) -> None:
        self.error = cause

    def end_ownership(self) -> None:
        self.owner_id = None

    def poll(self) -> dict[str, object]:
        self.poll_calls += 1
        if self.error is not None:
            from ralph.workspace._shared_awareness import SharedAwarenessError

            raise SharedAwarenessError(self.error)
        return {
            "epoch": self.epoch,
            "paths": list(self.paths),
            "overflowed": self.overflowed,
            "owner_id": self.owner_id or "unknown",
            "changed": True,
        }

    def claim_epoch(self, epoch: int) -> None:
        self.claim_calls.append(epoch)


def test_cross_process_holder_blocks_start_and_reports_shared_awareness_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Held case: ``try_acquire`` returns a non-None owner id, so the second
    process's ``WorkspaceMonitor.start()`` schedules zero observers and
    consumes the owner's shared-awareness sidecar instead (S-2). The
    awareness status carries ``cause="cross_process_holder"`` and the owner
    id."""
    fake_lock = _FakeCrossProcessWatchLock(holder="proc-99:1")
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace.CrossProcessWatchLock", fake_lock
    )
    fake = _FakeObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer", lambda: fake
    )
    fake_sidecar = _FakeSharedAwarenessSidecar()
    fake_sidecar.owner_id = "proc-99:1"
    fake_sidecar.paths = ["src/app.py"]
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace.shared_awareness_for_workspace",
        lambda _root: fake_sidecar,
    )

    monitor = WorkspaceMonitor(Path("/ws"), classifier=WorkspaceChangeClassifier())
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
    # The held lease never registered a shared watch and never released.
    assert fake_lock.release_calls == []
    monitor.stop()


def test_free_lock_lets_start_schedule_one_observer_and_release_roundtrips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Free case: ``try_acquire`` returns None, so the first ``start()``
    schedules exactly one observer and stores the owner id; the matching
    ``stop()`` calls ``release`` with that id; a second ``start()`` after
    release succeeds."""
    fake_lock = _FakeCrossProcessWatchLock(holder=None)
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace.CrossProcessWatchLock", fake_lock
    )
    observers: list[_FakeObserver] = []

    def _factory() -> _FakeObserver:
        observer = _FakeObserver()
        observers.append(observer)
        return observer

    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer", _factory
    )

    monitor = WorkspaceMonitor(Path("/ws"), classifier=WorkspaceChangeClassifier())
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

    # Second start after release: lock is free again, observer scheduled.
    monitor.start()

    assert len(observers) == 2
    assert len(observers[1].scheduled) == 1
    assert observers[1].started is True
    monitor.stop()


def test_non_owner_stop_does_not_release_and_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-owner stop: a monitor that never won the cross-process lock
    does NOT call ``release`` and does NOT raise."""
    fake_lock = _FakeCrossProcessWatchLock(holder="proc-99:1")
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace.CrossProcessWatchLock", fake_lock
    )
    fake = _FakeObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer", lambda: fake
    )

    monitor = WorkspaceMonitor(Path("/ws"), classifier=WorkspaceChangeClassifier())
    monitor.start()  # blocked by the cross-process holder

    assert monitor._cross_process_owner_id is None

    monitor.stop()  # must not raise and must not release

    assert fake_lock.release_calls == []


def test_shared_lease_releases_cross_process_lock_only_at_final_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DA-001/DA-009: stopping one in-process lease while another lease still
    shares the observer must leave the cross-process lock held; only the final
    matching ``stop()`` releases it."""
    fake_lock = _FakeCrossProcessWatchLock(holder=None)
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace.CrossProcessWatchLock", fake_lock
    )
    fake = _FakeObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer", lambda: fake
    )

    # Monitor A: creates the shared watch and acquires the cross-process lock.
    monitor_a = WorkspaceMonitor(Path("/ws"), classifier=WorkspaceChangeClassifier())
    monitor_a.start()
    assert len(fake.scheduled) == 1
    assert fake_lock.acquire_calls == [Path("/ws")]

    # Monitor B: joins the existing shared watch (no new observer, no acquire).
    monitor_b = WorkspaceMonitor(Path("/ws"), classifier=WorkspaceChangeClassifier())
    monitor_b.start()
    assert len(fake.scheduled) == 1
    assert len(fake_lock.acquire_calls) == 1

    # Monitor A stops first: NOT the final lease, so the lock stays held.
    monitor_a.stop()
    assert fake_lock.release_calls == []

    # Monitor B stops: IS the final lease, so the lock is released.
    monitor_b.stop()
    assert len(fake_lock.release_calls) == 1
    released_workspace, released_owner = fake_lock.release_calls[0]
    assert released_workspace == Path("/ws")
    assert released_owner is not None


# ---------------------------------------------------------------------------
# S-2: shared awareness sidecar — publication, consumption, takeover, fallback
# ---------------------------------------------------------------------------


def test_owner_publishes_source_changes_to_sidecar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-2: the watch-lock owner publishes observed source changes to the
    shared sidecar (owner id, epoch bump, coalesced relative path) while
    Ralph-managed internal paths are excluded."""
    fake_lock = _FakeCrossProcessWatchLock(holder=None)
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace.CrossProcessWatchLock", fake_lock
    )
    fake = _FakeObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer", lambda: fake
    )
    fake_sidecar = _FakeSharedAwarenessSidecar()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace.shared_awareness_for_workspace",
        lambda _root: fake_sidecar,
    )

    monitor = WorkspaceMonitor(Path("/ws"), classifier=WorkspaceChangeClassifier())
    monitor.start()

    assert fake_sidecar.begin_calls, "owner must begin sidecar ownership"
    owner_id = monitor._cross_process_owner_id
    assert fake_sidecar.begin_calls[0][0] == owner_id

    epoch_before = fake_sidecar.epoch
    monitor.record_event("/ws/src/app.py")
    assert fake_sidecar.publish_calls == [(["src/app.py"], False)]
    assert fake_sidecar.epoch > epoch_before
    assert fake_sidecar.paths == ["src/app.py"]

    # Internal paths are excluded from the sidecar (no publication).
    monitor.record_event("/ws/.agent/tmp/scratch.md")
    assert fake_sidecar.publish_calls == [(["src/app.py"], False)]
    monitor.stop()


def test_sidecar_write_failure_enters_live_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-2: an owner whose sidecar publication fails surfaces explicit
    ``live_fallback`` rather than silently dropping the change."""
    from ralph.workspace._shared_awareness import SharedAwarenessError

    class _FailingSidecar(_FakeSharedAwarenessSidecar):
        def publish_changes(
            self, paths: list[str], *, overflowed: bool = False
        ) -> int:
            raise SharedAwarenessError("disk full")

    fake_lock = _FakeCrossProcessWatchLock(holder=None)
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace.CrossProcessWatchLock", fake_lock
    )
    fake = _FakeObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer", lambda: fake
    )
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace.shared_awareness_for_workspace",
        lambda _root: _FailingSidecar(),
    )

    monitor = WorkspaceMonitor(Path("/ws"), classifier=WorkspaceChangeClassifier())
    monitor.start()
    monitor.record_event("/ws/src/app.py")

    assert monitor.awareness_status["freshness"] == "live_fallback"
    assert monitor.awareness_status["cause"] == "shared_awareness_io_failed"
    monitor.stop()


def test_consumer_sidecar_read_failure_enters_live_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-2: a non-owner that cannot read the owner sidecar (corrupt or
    owner-reported error) enters bounded ``live_fallback`` instead of
    registering a duplicate observer."""
    fake_lock = _FakeCrossProcessWatchLock(holder="proc-99:1")
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace.CrossProcessWatchLock", fake_lock
    )
    fake = _FakeObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer", lambda: fake
    )
    fake_sidecar = _FakeSharedAwarenessSidecar()
    fake_sidecar.error = "corrupt sidecar"
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace.shared_awareness_for_workspace",
        lambda _root: fake_sidecar,
    )

    monitor = WorkspaceMonitor(Path("/ws"), classifier=WorkspaceChangeClassifier())
    monitor.start()

    assert len(fake.scheduled) == 0
    status = monitor.awareness_status
    assert status["freshness"] == "live_fallback"
    assert status["cause"] == "shared_awareness_io_failed"
    monitor.stop()


def test_crash_takeover_restarts_sidecar_epoch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """S-2: a new owner that acquires the lock after a stale owner crashed
    reconciles the stale sidecar and restarts the epoch at 1, so consumers
    can detect the owner change."""
    from ralph.workspace._shared_awareness import (
        SharedAwarenessSidecar,
        release_shared_awareness,
    )

    workspace = tmp_path / "ws"
    workspace.mkdir()

    # Simulate a crashed owner's sidecar: unknown owner id, high epoch.
    crashed = SharedAwarenessSidecar(workspace)
    crashed.begin_ownership("dead-proc:7", prior_holder=None)
    for _ in range(5):
        crashed.publish_changes(["stale.py"])
    crashed_epoch = crashed.epoch
    release_shared_awareness(workspace)

    # A new owner takes over with a stale (unknown) prior holder.
    new_owner = SharedAwarenessSidecar(workspace)
    new_epoch = new_owner.begin_ownership("us:1", prior_holder=None)
    try:
        assert new_epoch == 1, "stale owner id must restart the epoch"
        assert crashed_epoch > new_epoch
        state = new_owner.poll()
        assert state["owner_id"] == "us:1"
        assert state["paths"] == []
    finally:
        release_shared_awareness(workspace)

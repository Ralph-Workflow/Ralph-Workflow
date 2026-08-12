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


def test_cross_process_holder_blocks_start_and_reports_live_fallback_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Held case: ``try_acquire`` returns a non-None owner id, so the second
    process's ``WorkspaceMonitor.start()`` schedules zero observers and the
    awareness status carries ``cause="cross_process_holder"``."""
    fake_lock = _FakeCrossProcessWatchLock(holder="proc-99:1")
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace.CrossProcessWatchLock", fake_lock
    )
    fake = _FakeObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer", lambda: fake
    )

    monitor = WorkspaceMonitor(Path("/ws"), classifier=WorkspaceChangeClassifier())
    monitor.start()

    assert len(fake.scheduled) == 0
    assert fake.started is False
    status = monitor.awareness_status
    assert status["freshness"] == "live_fallback"
    assert status["cause"] == "cross_process_holder"
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

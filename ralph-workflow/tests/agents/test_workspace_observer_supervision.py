from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from watchdog.events import FileModifiedEvent

from ralph.agents.invoke import WorkspaceMonitor, WorkspaceWatchSeams
from ralph.agents.invoke.watchdog_observer import (
    MAX_OBSERVER_RESTARTS,
    observer_restart_delay_seconds,
)
from ralph.workspace.awareness import awareness_for_workspace, release_workspace_awareness

if TYPE_CHECKING:
    from pathlib import Path


class _FakeObserver:
    def __init__(self, created: list[_FakeObserver]) -> None:
        self.alive = False
        self.stopped = False
        self.scheduled: list[tuple[object, str, dict[str, object]]] = []
        created.append(self)

    def schedule(self, event_handler: object, path: str, **kwargs: object) -> None:
        self.scheduled.append((event_handler, path, dict(kwargs)))

    def start(self) -> None:
        self.alive = True

    def stop(self) -> None:
        self.alive = False
        self.stopped = True

    def join(self, timeout: float | None = None) -> None:
        del timeout

    def is_alive(self) -> bool:
        return self.alive


def _monitor(
    root: Path, created: list[_FakeObserver], delays: list[float] | None = None
) -> WorkspaceMonitor:
    recorded = delays if delays is not None else []
    return WorkspaceMonitor(
        root,
        seams=WorkspaceWatchSeams(
            observer_factory=lambda: _FakeObserver(created),
            observer_restart_sleep=recorded.append,
            supervise_in_background=False,
        ),
    )


def _dirty_count(root: Path) -> int:
    count = awareness_for_workspace(root).snapshot()["dirty_paths_count"]
    assert isinstance(count, int)
    return count


def test_a_dead_watcher_thread_is_restarted_with_the_same_watch(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text("print('hello')\n", encoding="utf-8")
    created: list[_FakeObserver] = []
    monitor = _monitor(tmp_path, created)
    monitor.start()
    try:
        crashed = created[0]
        crashed.alive = False

        assert WorkspaceMonitor.supervise_observers_once() == 1

        replacement = created[1]
        assert crashed.stopped
        assert replacement.alive
        handler, path, options = replacement.scheduled[0]
        assert path == str(tmp_path)
        assert options == {"recursive": True}
        handler.dispatch(FileModifiedEvent(str(source)))
        handler.flush()
        assert _dirty_count(tmp_path) == 1
        assert monitor.awareness_status["mode"] != "live_fallback"
        assert WorkspaceMonitor.supervise_observers_once() == 0
    finally:
        monitor.stop()
        release_workspace_awareness(tmp_path)


def test_restarts_back_off_are_bounded_and_then_fall_back_to_live_reads(tmp_path: Path) -> None:
    created: list[_FakeObserver] = []
    delays: list[float] = []
    monitor = _monitor(tmp_path, created, delays)
    monitor.start()
    try:
        for _ in range(MAX_OBSERVER_RESTARTS + 3):
            created[-1].alive = False
            WorkspaceMonitor.supervise_observers_once()

        assert len(created) == 1 + MAX_OBSERVER_RESTARTS
        assert delays == [
            observer_restart_delay_seconds(attempt) for attempt in range(MAX_OBSERVER_RESTARTS)
        ]
        assert delays == sorted(delays)
        assert monitor.awareness_status["mode"] == "live_fallback"
    finally:
        monitor.stop()
        release_workspace_awareness(tmp_path)


@pytest.mark.parametrize(
    "relative",
    [
        "tmp/ralph-pytest-shards/shard-5/repo/test_parity.py",
        "ralph-workflow/tmp/ralph-pytest-shards/shard-5/repo/test_parity.py",
        "ralph-workflow/.pytest_cache/v/cache/nodeids",
        "ralph-workflow/__pycache__/mod.cpython-314.pyc",
        "ralph-workflow/.mypy_cache/3.14/mod.json",
        "ralph-workflow/.ruff_cache/0.1/cache",
        ".venv/lib/python3.14/site-packages/mod.py",
    ],
)
def test_events_under_throwaway_trees_are_never_recorded(tmp_path: Path, relative: str) -> None:
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text("x\n", encoding="utf-8")
    monitor = _monitor(tmp_path, [])
    monitor.start()
    try:
        monitor.dispatch_event(FileModifiedEvent(str(path)))

        assert monitor.event_count == 0
        assert _dirty_count(tmp_path) == 0
    finally:
        monitor.stop()
        release_workspace_awareness(tmp_path)


def test_a_workspace_that_lives_under_a_tmp_directory_still_records_source_edits(
    tmp_path: Path,
) -> None:
    root = tmp_path / "tmp" / "workspace"
    source = root / "app.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('hello')\n", encoding="utf-8")
    monitor = _monitor(root, [])
    monitor.start()
    try:
        monitor.dispatch_event(FileModifiedEvent(str(source)))

        assert monitor.event_count == 1
        assert _dirty_count(root) == 1
    finally:
        monitor.stop()
        release_workspace_awareness(root)


def test_a_watcher_over_its_inotify_budget_is_stopped_for_live_reads(tmp_path: Path) -> None:
    created: list[_FakeObserver] = []
    monitor = WorkspaceMonitor(
        tmp_path,
        seams=WorkspaceWatchSeams(
            observer_factory=lambda: _FakeObserver(created),
            supervise_in_background=False,
            watch_count_probe=lambda: 10**6,
        ),
    )
    monitor.start()
    try:
        WorkspaceMonitor.supervise_observers_once()

        assert created[0].stopped
        assert monitor.awareness_status["mode"] == "live_fallback"
        assert len(created) == 1
    finally:
        monitor.stop()
        release_workspace_awareness(tmp_path)


def test_a_watcher_within_its_inotify_budget_keeps_running(tmp_path: Path) -> None:
    created: list[_FakeObserver] = []
    monitor = WorkspaceMonitor(
        tmp_path,
        seams=WorkspaceWatchSeams(
            observer_factory=lambda: _FakeObserver(created),
            supervise_in_background=False,
            watch_count_probe=lambda: 10,
        ),
    )
    monitor.start()
    try:
        WorkspaceMonitor.supervise_observers_once()

        assert not created[0].stopped
        assert monitor.awareness_status["mode"] != "live_fallback"
    finally:
        monitor.stop()
        release_workspace_awareness(tmp_path)

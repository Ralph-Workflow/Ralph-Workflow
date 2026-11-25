from __future__ import annotations

from typing import TYPE_CHECKING

from watchdog.events import (
    FileClosedEvent,
    FileClosedNoWriteEvent,
    FileModifiedEvent,
    FileOpenedEvent,
)

from ralph.agents.invoke import WorkspaceMonitor, WorkspaceWatchSeams
from ralph.workspace.awareness import awareness_for_workspace, release_workspace_awareness
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.probe import DefaultWorkspaceChangeProbe

if TYPE_CHECKING:
    from pathlib import Path

_PHASE = "planning_analysis"


class _InertObserver:
    def schedule(self, event_handler: object, path: str, **kwargs: object) -> None:
        del event_handler, path, kwargs

    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def join(self, timeout: float | None = None) -> None:
        del timeout

    def is_alive(self) -> bool:
        return False


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    readme = tmp_path / "README.md"
    source = tmp_path / "app.py"
    readme.write_text("readme\n", encoding="utf-8")
    source.write_text("print('hello')\n", encoding="utf-8")
    return readme, source


def _dirty_paths_after(tmp_path: Path, events: list[object]) -> int:
    monitor = WorkspaceMonitor(tmp_path)
    monitor.start()
    try:
        for event in events:
            monitor.dispatch_event(event)
        count = awareness_for_workspace(tmp_path).snapshot()["dirty_paths_count"]
        assert isinstance(count, int)
        return count
    finally:
        monitor.stop()
        release_workspace_awareness(tmp_path)


def test_open_and_read_only_close_events_never_mark_a_path_dirty(tmp_path: Path) -> None:
    readme, source = _workspace(tmp_path)

    dirty = _dirty_paths_after(
        tmp_path,
        [
            FileOpenedEvent(str(readme)),
            FileClosedNoWriteEvent(str(readme)),
            FileOpenedEvent(str(source)),
            FileClosedEvent(str(source)),
        ],
    )

    assert dirty >= 1


def test_a_modification_still_marks_its_path_dirty(tmp_path: Path) -> None:
    readme, _source = _workspace(tmp_path)

    dirty = _dirty_paths_after(
        tmp_path,
        [FileOpenedEvent(str(readme)), FileModifiedEvent(str(readme))],
    )

    assert dirty >= 1


def test_an_agent_that_only_reads_files_is_active_but_changed_nothing(tmp_path: Path) -> None:
    readme, source = _workspace(tmp_path)
    workspace = FsWorkspace(tmp_path)
    probe = DefaultWorkspaceChangeProbe()
    monitor = WorkspaceMonitor(tmp_path, seams=WorkspaceWatchSeams(observer_factory=_InertObserver))
    monitor.start()
    try:
        probe.observe_before(_PHASE, workspace)
        for path in (readme, source):
            monitor.dispatch_event(FileOpenedEvent(str(path)))
            monitor.dispatch_event(FileClosedNoWriteEvent(str(path)))

        assert monitor.event_count >= len((readme, source)) * 2
        assert monitor.last_event_at is not None
        assert awareness_for_workspace(tmp_path).snapshot()["dirty_paths_count"] == 0
        assert probe.has_changed(_PHASE, workspace) is False
    finally:
        monitor.stop()
        release_workspace_awareness(tmp_path)


def test_an_agent_that_writes_a_file_still_changed_the_workspace(tmp_path: Path) -> None:
    readme, _source = _workspace(tmp_path)
    workspace = FsWorkspace(tmp_path)
    probe = DefaultWorkspaceChangeProbe()
    monitor = WorkspaceMonitor(tmp_path)
    monitor.start()
    try:
        probe.observe_before(_PHASE, workspace)
        readme.write_text("edited by the agent\n", encoding="utf-8")
        monitor.dispatch_event(FileModifiedEvent(str(readme)))

        assert awareness_for_workspace(tmp_path).snapshot()["dirty_paths_count"] == 1
        assert probe.has_changed(_PHASE, workspace) is True
    finally:
        monitor.stop()
        release_workspace_awareness(tmp_path)

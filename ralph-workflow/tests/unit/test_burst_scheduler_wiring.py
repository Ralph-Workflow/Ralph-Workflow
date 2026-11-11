from __future__ import annotations

import inspect
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.invoke._workspace import WorkspaceMonitor
from ralph.mcp.explore import dirty_paths

if TYPE_CHECKING:
    import pytest


class _FakeClock:
    def __init__(self, initial: float = 0.0) -> None:
        self._t = initial

    def __call__(self) -> float:
        return self._t

    def advance(self, delta: float) -> None:
        self._t += delta


class _RecordingHandle:
    def __init__(self) -> None:
        self.mark_dirty_calls: list[tuple[list[str], str, str]] = []

    def mark_dirty(self, paths: list[str], *, source_tool: str, reason: str = "mutated") -> None:
        self.mark_dirty_calls.append((list(paths), source_tool, reason))

    @property
    def store(self) -> None:
        return None

    @property
    def reindex_in_progress(self) -> bool:
        return False


class _SpyScheduler:
    def __init__(self, clock: _FakeClock, debounce_window: float) -> None:
        self._clock = clock
        self._debounce_window = debounce_window
        self._last_mark_at: float | None = None
        self.mark_calls: int = 0
        self.on_workflow_complete_calls: int = 0
        self.on_workflow_cancel_calls: int = 0
        self.on_workflow_fail_calls: int = 0
        self.on_workflow_restart_calls: int = 0

    def mark(self, closure: object) -> None:
        del closure
        self.mark_calls += 1
        self._last_mark_at = self._clock()
        self.fire_if_due()

    def fire_if_due(self) -> bool:
        if self._last_mark_at is None:
            return False
        if self._clock() - self._last_mark_at < self._debounce_window:
            return False
        self._last_mark_at = None
        dirty_paths._drain_pending_marks()
        return True

    def on_workflow_complete(self) -> None:
        self.on_workflow_complete_calls += 1
        dirty_paths._PENDING_MARKS.clear()

    def on_workflow_cancel(self) -> None:
        self.on_workflow_cancel_calls += 1
        dirty_paths._PENDING_MARKS.clear()

    def on_workflow_fail(self) -> None:
        self.on_workflow_fail_calls += 1
        dirty_paths._PENDING_MARKS.clear()

    def on_workflow_restart(self) -> None:
        self.on_workflow_restart_calls += 1
        dirty_paths._PENDING_MARKS.clear()


def test_mark_path_routes_through_single_shared_scheduler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _FakeClock()
    spy = _SpyScheduler(clock=clock, debounce_window=1.0)
    monkeypatch.setattr(dirty_paths, "_dirty_scheduler", spy)
    dirty_paths._PENDING_MARKS.clear()

    handle = _RecordingHandle()
    for _ in range(50):
        dirty_paths.mark_path(handle, path="a.py", source_tool="write_file")

    assert spy.mark_calls == 50
    assert len(handle.mark_dirty_calls) == 1
    fired_paths, source_tool, reason = handle.mark_dirty_calls[0]
    assert fired_paths == ["a.py"]
    assert source_tool == "write_file"
    assert reason == "mutated"
    clock.advance(1.0)
    assert spy.fire_if_due() is True
    assert len(handle.mark_dirty_calls) == 1


def test_workspace_monitor_does_not_own_scheduler(tmp_path: Path) -> None:
    monitor = WorkspaceMonitor(tmp_path)
    assert not hasattr(monitor, "_dirty_scheduler")


def test_monitor_stop_invokes_on_workflow_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _FakeClock()
    spy = _SpyScheduler(clock=clock, debounce_window=1.0)
    monkeypatch.setattr(dirty_paths, "_dirty_scheduler", spy)
    monitor = WorkspaceMonitor(tmp_path)
    monitor.stop()
    assert spy.on_workflow_complete_calls == 1


def test_execute_pipeline_wires_four_lifecycle_hooks() -> None:
    from ralph.cli.commands import run as run_module

    source = inspect.getsource(run_module._execute_pipeline)
    assert "_dirty_scheduler.on_workflow_complete()" in source
    assert "_dirty_scheduler.on_workflow_cancel()" in source
    assert "_dirty_scheduler.on_workflow_restart()" in source
    assert "_dirty_scheduler.on_workflow_fail()" in source

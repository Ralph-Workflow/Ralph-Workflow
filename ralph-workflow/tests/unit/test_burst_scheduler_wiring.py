"""Wiring tests for the burst-debounce scheduler (S-3b).

Asserts ONE shared scheduler routes dirty-path calls and that
``WorkspaceMonitor.stop()`` releases the pending timer. Also asserts
the four lifecycle hooks are wired into ``_execute_pipeline`` exit
branches by reading ``run.py`` source.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.invoke._workspace import WorkspaceMonitor
from ralph.mcp.explore import dirty_paths

if TYPE_CHECKING:
    import pytest


class _FakeClock:
    """Controllable monotonic clock."""

    def __init__(self, initial: float = 0.0) -> None:
        self._t = initial

    def __call__(self) -> float:
        return self._t

    def advance(self, delta: float) -> None:
        self._t += delta


class _RecordingHandle:
    """Fake explore index handle that records ``mark_dirty`` invocations."""

    def __init__(self) -> None:
        self.mark_dirty_calls: list[tuple[list[str], str, str]] = []

    def mark_dirty(
        self, paths: list[str], *, source_tool: str, reason: str = "mutated"
    ) -> None:
        self.mark_dirty_calls.append((list(paths), source_tool, reason))

    @property
    def store(self) -> None:
        return None

    @property
    def reindex_in_progress(self) -> bool:
        return False


class _SpyScheduler:
    """Scheduler spy that records ``mark`` calls and fires on demand.

    Replaces ``dirty_paths._dirty_scheduler`` so the test can assert the
    production code reads ONE shared instance (no parallel construction).
    The spy respects the debounce window (like the real scheduler): marks
    inside the window accumulate; ``fire_if_due`` only drains when the
    window has elapsed since the last mark.
    """

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
        # Delegate to the production drain so the dedup contract holds.
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
    """50 mark_path calls coalesce into one handle.mark_dirty invocation."""
    clock = _FakeClock()
    spy = _SpyScheduler(clock=clock, debounce_window=1.0)
    monkeypatch.setattr(dirty_paths, "_dirty_scheduler", spy)
    # Clear any pending marks from prior tests sharing the module.
    dirty_paths._PENDING_MARKS.clear()

    handle = _RecordingHandle()
    for _ in range(50):
        dirty_paths.mark_path(handle, path="a.py", source_tool="write_file")

    # The spy recorded all 50 marks (no parallel scheduler construction).
    assert spy.mark_calls == 50
    # Synchronous persistence: 50 calls for the same (handle, path)
    # pair produce one direct mark_dirty (deduped by _PENDING_MARKS).
    # The BurstDebounceScheduler coalesces the reindex trigger only.
    assert len(handle.mark_dirty_calls) == 1
    fired_paths, source_tool, reason = handle.mark_dirty_calls[0]
    assert fired_paths == ["a.py"]
    assert source_tool == "write_file"
    assert reason == "mutated"
    # Advance past the debounce window and drain.
    clock.advance(1.0)
    assert spy.fire_if_due() is True
    # The drain clears pending marks; no additional mark_dirty call
    # because marks were already persisted synchronously.
    assert len(handle.mark_dirty_calls) == 1


def test_workspace_monitor_does_not_own_scheduler(tmp_path: Path) -> None:
    """``WorkspaceMonitor`` does NOT construct its own scheduler."""
    monitor = WorkspaceMonitor(tmp_path)
    assert not hasattr(monitor, "_dirty_scheduler")


def test_monitor_stop_invokes_on_workflow_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``WorkspaceMonitor.stop()`` releases the shared scheduler's timer."""
    clock = _FakeClock()
    spy = _SpyScheduler(clock=clock, debounce_window=1.0)
    monkeypatch.setattr(dirty_paths, "_dirty_scheduler", spy)
    monitor = WorkspaceMonitor(tmp_path)
    monitor.stop()
    assert spy.on_workflow_complete_calls == 1


def test_execute_pipeline_wires_four_lifecycle_hooks() -> None:
    """The four lifecycle hooks are wired into ``_execute_pipeline`` branches."""
    from ralph.cli.commands import run as run_module

    source = inspect.getsource(run_module._execute_pipeline)
    # Success branch releases the timer on completion.
    assert "_dirty_scheduler.on_workflow_complete()" in source
    # KeyboardInterrupt branch releases on cancel.
    assert "_dirty_scheduler.on_workflow_cancel()" in source
    # CheckpointPolicyMismatch / PolicyValidationError branches release on restart.
    assert "_dirty_scheduler.on_workflow_restart()" in source
    # Generic Exception branch releases on fail.
    assert "_dirty_scheduler.on_workflow_fail()" in source

"""Tests for LogTailPanel."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from rich.console import Console

from ralph.display.panels.log_tail import make_log_tail_panel
from ralph.display.ring_buffer import RingBuffer
from ralph.display.snapshot import DashboardSnapshot, WorkerSnapshot

if TYPE_CHECKING:
    from rich.panel import Panel


def _make_snapshot(
    workers: tuple[WorkerSnapshot, ...] = (),
) -> DashboardSnapshot:
    return DashboardSnapshot(
        phase="development",
        previous_phase="planning",
        iteration=1,
        total_iterations=10,
        reviewer_pass=1,
        total_reviewer_passes=3,
        review_issues_found=False,
        interrupted_by_user=False,
        last_error=None,
        pr_url=None,
        push_count=0,
        total_agent_calls=0,
        total_continuations=0,
        total_fallbacks=0,
        total_retries=0,
        workers=workers,
        prompt_path=None,
        prompt_preview=(),
        run_id=None,
        created_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
    )


def _make_worker(
    unit_id: str,
    status_semantic: str = "running",
) -> WorkerSnapshot:
    return WorkerSnapshot(
        unit_id=unit_id,
        description=f"Desc for {unit_id}",
        status=status_semantic.upper(),
        status_semantic=status_semantic,
        started_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
        finished_at=None,
        elapsed_s=10.5,
        exit_code=None,
        commit_sha=None,
        error_message=None,
    )


def _render_str(panel: Panel) -> str:
    console = Console(force_terminal=False, width=200)
    with console.capture() as capture:
        console.print(panel)
    return capture.get()


def _make_buffer(lines: list[str], *, maxsize: int = 10) -> RingBuffer:
    buffer = RingBuffer(maxsize=maxsize)
    for line in lines:
        buffer.enqueue(line)
    return buffer


def test_log_tail_panel_name() -> None:
    panel = make_log_tail_panel({})

    assert panel.name == "log_tail"


def test_render_no_workers() -> None:
    panel = make_log_tail_panel({})

    rendered = _render_str(panel.render(_make_snapshot()))
    assert "no logs yet" in rendered


def test_render_no_buffer_shows_waiting() -> None:
    workers = (_make_worker("worker-1"),)
    panel = make_log_tail_panel({})

    rendered = _render_str(panel.render(_make_snapshot(workers)))
    assert "waiting for output" in rendered


def test_render_buffer_has_lines() -> None:
    workers = (_make_worker("worker-1"),)
    panel = make_log_tail_panel({"worker-1": _make_buffer(["line one", "line two", "line three"])})

    rendered = _render_str(panel.render(_make_snapshot(workers)))
    assert "line one" in rendered
    assert "line two" in rendered
    assert "line three" in rendered


def test_render_dropped_count() -> None:
    workers = (_make_worker("worker-1"),)
    buffer = _make_buffer(["line one"], maxsize=1)
    panel = make_log_tail_panel({"worker-1": buffer})
    buffer.enqueue("line two")

    rendered = _render_str(panel.render(_make_snapshot(workers)))
    assert "dropped: 1" in rendered


def test_render_multiple_workers_limited_to_3() -> None:
    workers = tuple(_make_worker(f"worker-{i}") for i in range(5))
    buffers: dict[str, RingBuffer] = {
        f"worker-{i}": _make_buffer([f"line {i}"]) for i in range(5)
    }
    panel = make_log_tail_panel(buffers)

    rendered = _render_str(panel.render(_make_snapshot(workers)))
    assert "worker-0" in rendered
    assert "worker-1" in rendered
    assert "worker-2" in rendered
    assert "worker-3" not in rendered
    assert "worker-4" not in rendered

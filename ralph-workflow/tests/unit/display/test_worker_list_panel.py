"""Tests for WorkerListPanel."""

from __future__ import annotations

from datetime import UTC, datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from ralph.display.panels.worker_list import worker_list_panel
from ralph.display.snapshot import DashboardSnapshot, WorkerSnapshot


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
    unit_id: str = "worker-1",
    status_semantic: str = "running",
    elapsed_s: float = 10.5,
    error_message: str | None = None,
    commit_sha: str | None = None,
) -> WorkerSnapshot:
    return WorkerSnapshot(
        unit_id=unit_id,
        description=f"Desc for {unit_id}",
        status=status_semantic.upper(),
        status_semantic=status_semantic,
        started_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
        finished_at=None,
        elapsed_s=elapsed_s,
        exit_code=None,
        commit_sha=commit_sha,
        error_message=error_message,
    )


def _render_str(panel: Panel | Text) -> str:
    console = Console(force_terminal=False, width=200)
    with console.capture() as capture:
        console.print(panel)
    return capture.get()


def test_worker_list_panel_name() -> None:
    assert worker_list_panel.name == "worker_list"


def test_render_zero_workers() -> None:
    panel = worker_list_panel.render(_make_snapshot())

    assert isinstance(panel, Panel)
    assert "no workers" in _render_str(panel)


def test_render_four_workers_shows_grid_message() -> None:
    workers = tuple(_make_worker(unit_id=f"worker-{i}") for i in range(4))
    panel = worker_list_panel.render(_make_snapshot(workers))

    assert isinstance(panel, Panel)
    rendered = _render_str(panel)
    assert "use grid view (≤4 workers)" in rendered


def test_render_five_workers_shows_table() -> None:
    workers = tuple(_make_worker(unit_id=f"worker-{i}") for i in range(5))
    panel = worker_list_panel.render(_make_snapshot(workers))

    rendered = _render_str(panel)
    assert "worker-0" in rendered
    assert "worker-1" in rendered
    assert "worker-2" in rendered
    assert "worker-3" in rendered
    assert "worker-4" in rendered


def test_render_sorted_error_first() -> None:
    workers = (
        _make_worker(unit_id="running-worker", status_semantic="running", elapsed_s=10.0),
        _make_worker(
            unit_id="error-worker",
            status_semantic="error",
            error_message="boom",
        ),
        _make_worker(unit_id="pending-worker", status_semantic="pending"),
        _make_worker(unit_id="success-worker-1", status_semantic="success"),
        _make_worker(unit_id="success-worker-2", status_semantic="success"),
    )
    panel = worker_list_panel.render(_make_snapshot(workers))
    rendered = _render_str(panel)

    assert rendered.index("error-worker") < rendered.index("running-worker")


def test_render_compact_width_hides_description() -> None:
    workers = tuple(
        _make_worker(unit_id=f"worker-{i}", error_message=None, commit_sha=None)
        for i in range(5)
    )
    panel = worker_list_panel.render(_make_snapshot(workers), width=60)
    rendered = _render_str(panel)

    assert "Desc for worker-0" not in rendered
    assert "Desc for worker-4" not in rendered

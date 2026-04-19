"""Tests for WorkerGridPanel."""

from __future__ import annotations

from datetime import UTC, datetime

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel

from ralph.display.panels.worker_grid import worker_grid_panel
from ralph.display.snapshot import DashboardSnapshot, WorkerSnapshot


def _make_snapshot(
    *,
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
    *,
    unit_id: str = "worker-1",
    description: str = "Test worker",
    status_semantic: str = "running",
    elapsed_s: float = 10.5,
    **kwargs: str | None,
) -> WorkerSnapshot:
    return WorkerSnapshot(
        unit_id=unit_id,
        description=description,
        status=status_semantic.upper(),
        status_semantic=status_semantic,
        started_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
        finished_at=None,
        elapsed_s=elapsed_s,
        exit_code=None,
        commit_sha=kwargs.get("commit_sha"),
        error_message=kwargs.get("error_message"),
    )


def _render_to_str(renderable: Panel | Columns) -> str:
    console = Console(force_terminal=False, width=200)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


def test_worker_grid_panel_name() -> None:
    assert worker_grid_panel.name == "worker_grid"


def test_render_zero_workers_shows_no_workers() -> None:
    snapshot = _make_snapshot(workers=())
    panel = worker_grid_panel.render(snapshot)

    assert isinstance(panel, Panel)
    rendered = _render_to_str(panel)
    assert "no workers" in rendered


def test_render_three_workers_shows_all_three() -> None:
    workers = (
        _make_worker(unit_id="worker-1", description="First worker"),
        _make_worker(unit_id="worker-2", description="Second worker"),
        _make_worker(unit_id="worker-3", description="Third worker"),
    )
    snapshot = _make_snapshot(workers=workers)
    panel = worker_grid_panel.render(snapshot)

    assert isinstance(panel, Columns)
    rendered = _render_to_str(panel)
    assert "worker-1" in rendered
    assert "worker-2" in rendered
    assert "worker-3" in rendered


def test_render_five_workers_shows_list_view_message() -> None:
    workers = tuple(
        _make_worker(unit_id=f"worker-{i}") for i in range(5)
    )
    snapshot = _make_snapshot(workers=workers)
    panel = worker_grid_panel.render(snapshot)

    assert isinstance(panel, Panel)
    rendered = _render_to_str(panel)
    assert "use list view" in rendered
    assert ">4 workers" in rendered


def test_render_worker_with_error_shows_error_message() -> None:
    workers = (
        _make_worker(
            unit_id="failing-worker",
            description="A failing worker",
            status_semantic="error",
            error_message="Build failed: syntax error in main.py",
        ),
    )
    snapshot = _make_snapshot(workers=workers)
    panel = worker_grid_panel.render(snapshot)

    assert isinstance(panel, Columns)
    rendered = _render_to_str(panel)
    assert "Build failed" in rendered
    assert "syntax error" in rendered

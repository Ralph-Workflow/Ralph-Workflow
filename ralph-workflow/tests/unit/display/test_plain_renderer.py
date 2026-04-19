from __future__ import annotations

import importlib
from datetime import UTC, datetime
from io import StringIO

from rich.console import Console

from ralph.display.snapshot import DashboardSnapshot, WorkerSnapshot

plain_renderer = importlib.import_module("ralph.display.plain_renderer")
PlainLogRenderer = plain_renderer.PlainLogRenderer


FIXED_TIME = datetime(2026, 4, 18, 12, 0, tzinfo=UTC)


def _make_snapshot(
    *,
    phase: str = "development",
    iteration: int = 1,
    total_iterations: int = 3,
    workers: tuple[WorkerSnapshot, ...] = (),
) -> DashboardSnapshot:
    return DashboardSnapshot(
        phase=phase,
        previous_phase=None,
        iteration=iteration,
        total_iterations=total_iterations,
        reviewer_pass=0,
        total_reviewer_passes=0,
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
        created_at=FIXED_TIME,
    )


def _make_worker(*, unit_id: str = "worker-1", status: str = "RUNNING") -> WorkerSnapshot:
    return WorkerSnapshot(
        unit_id=unit_id,
        description=unit_id,
        status=status,
        status_semantic="info",
        started_at=None,
        finished_at=None,
        elapsed_s=0.0,
        exit_code=None,
        commit_sha=None,
        error_message=None,
    )


def _make_renderer() -> tuple[PlainLogRenderer, StringIO]:
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None)
    renderer = PlainLogRenderer(console, clock=lambda: FIXED_TIME)
    return renderer, stream


def test_emit_snapshot_for_development_outputs_one_line() -> None:
    renderer, stream = _make_renderer()

    renderer.emit_snapshot(_make_snapshot(phase="development"))

    assert stream.getvalue().splitlines() == [
        "2026-04-18T12:00:00+00:00 INFO [phase] development"
    ]


def test_emit_snapshot_deduplicates_identical_snapshots() -> None:
    renderer, stream = _make_renderer()
    snapshot = _make_snapshot(phase="development")

    renderer.emit_snapshot(snapshot)
    renderer.emit_snapshot(snapshot)

    assert stream.getvalue().splitlines() == [
        "2026-04-18T12:00:00+00:00 INFO [phase] development"
    ]


def test_emit_log_line_prints_markup_literally() -> None:
    renderer, stream = _make_renderer()

    renderer.emit_log_line("worker-1", "[bold magenta]hello[/bold magenta]")

    assert stream.getvalue().splitlines() == [
        "2026-04-18T12:00:00+00:00 INFO [worker-1] [bold magenta]hello[/bold magenta]"
    ]


def test_emit_snapshot_output_has_no_ansi_escape_codes() -> None:
    renderer, stream = _make_renderer()

    renderer.emit_snapshot(
        _make_snapshot(workers=(_make_worker(status="RUNNING"),))
    )

    output = stream.getvalue()
    assert "\x1b" not in output

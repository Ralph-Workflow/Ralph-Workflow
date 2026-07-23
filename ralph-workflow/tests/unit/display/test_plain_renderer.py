"""Tests for ParallelDisplay snapshot and log-line output (consolidated from PlainLogRenderer)."""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.snapshot import PipelineSnapshot, WorkerSnapshot

FIXED_TIME = datetime(2026, 4, 18, 12, 0, tzinfo=UTC)
PLAN_STEP_COUNT = 2


def _make_snapshot(
    *,
    phase: str = "development",
    current_phase_role: str | None = "execution",
    workers: tuple[WorkerSnapshot, ...] = (),
    plan_summary: str | None = None,
    plan_scope_items: tuple[str, ...] = (),
    plan_total_steps: int = 0,
    analysis_phase: str | None = None,
    analysis_decision: str | None = None,
    analysis_reason: str | None = None,
    active_agent: str | None = None,
    active_tool: str | None = None,
    active_path: str | None = None,
    active_workdir: str | None = None,
    active_command: str | None = None,
    last_activity_line: str | None = None,
) -> PipelineSnapshot:
    return PipelineSnapshot(
        phase=phase,
        previous_phase=None,
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
        plan_summary=plan_summary,
        plan_scope_items=plan_scope_items,
        plan_total_steps=plan_total_steps,
        analysis_phase=analysis_phase,
        analysis_decision=analysis_decision,
        analysis_reason=analysis_reason,
        active_agent=active_agent,
        active_tool=active_tool,
        active_path=active_path,
        active_workdir=active_workdir,
        active_command=active_command,
        last_activity_line=last_activity_line,
        current_phase_role=current_phase_role,
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
        error_message=None,
    )


def _make_display() -> tuple[ParallelDisplay, StringIO]:
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, width=200)
    renderer = ParallelDisplay(
        make_display_context(console=console, env={}),
        clock=lambda: FIXED_TIME,
    )
    return renderer, stream


def test_emit_snapshot_for_development_outputs_phase_and_placeholders() -> None:
    pd, stream = _make_display()

    pd.emit_snapshot(_make_snapshot(phase="development"))

    assert stream.getvalue().splitlines() == [
        "2026-04-18T12:00:00+00:00 MILESTONE META [phase] ◆ development",
        "2026-04-18T12:00:00+00:00 INFO META [plan] (no plan loaded yet)",
        "2026-04-18T12:00:00+00:00 INFO META [activity] (no active agent yet)",
        "2026-04-18T12:00:00+00:00 INFO META [analysis] (no decisions recorded yet)",
    ]


def test_emit_snapshot_deduplicates_identical_snapshots() -> None:
    pd, stream = _make_display()
    snapshot = _make_snapshot(phase="development")

    pd.emit_snapshot(snapshot)
    pd.emit_snapshot(snapshot)

    assert stream.getvalue().splitlines() == [
        "2026-04-18T12:00:00+00:00 MILESTONE META [phase] ◆ development",
        "2026-04-18T12:00:00+00:00 INFO META [plan] (no plan loaded yet)",
        "2026-04-18T12:00:00+00:00 INFO META [activity] (no active agent yet)",
        "2026-04-18T12:00:00+00:00 INFO META [analysis] (no decisions recorded yet)",
    ]


def test_emit_log_line_preserves_literal_rich_markup_for_copy_paste() -> None:
    """Plain-text output keeps literal brackets while stripping controls."""
    pd, stream = _make_display()

    pd.emit_log_line("worker-1", "[bold magenta]hello[/bold magenta]")

    assert stream.getvalue().splitlines() == [
        "2026-04-18T12:00:00+00:00 INFO CONT [content][worker-1] "
        "[bold magenta]hello[/bold magenta]"
    ]


def test_emit_snapshot_output_has_no_ansi_escape_codes() -> None:
    pd, stream = _make_display()

    pd.emit_snapshot(_make_snapshot(workers=(_make_worker(status="RUNNING"),)))

    output = stream.getvalue()
    assert "\x1b" not in output


def test_emit_snapshot_includes_plan_activity_and_analysis_context() -> None:
    pd, stream = _make_display()

    pd.emit_snapshot(
        _make_snapshot(
            phase="planning",
            plan_summary="Expose the full NDJSON transcript in the UI",
            plan_scope_items=("Render all events", "Keep output copy-pasteable"),
            plan_total_steps=PLAN_STEP_COUNT,
            active_agent="planner",
            active_tool="read",
            active_path="PROMPT.md",
            active_workdir="/tmp/project",
            active_command="python -m ralph",
            last_activity_line="Inspecting PROMPT.md for visibility requirements",
            analysis_phase="review",
            analysis_decision="revise",
            analysis_reason="The current dashboard drops key state",
        )
    )

    lines = stream.getvalue().splitlines()
    assert any("[plan] Expose the full NDJSON transcript in the UI" in line for line in lines)
    assert any(
        "[plan-scope] Render all events | Keep output copy-pasteable" in line for line in lines
    )
    assert not any("[activity] agent=planner" in line for line in lines), (
        "[activity] structured fields must be suppressed when last_activity_line is set"
    )
    assert any(
        "[activity] Inspecting PROMPT.md for visibility requirements" in line for line in lines
    ), "Expected [activity] line with last_activity_line content"
    assert "[activity-line]" not in stream.getvalue(), (
        "[activity-line] tag must not appear; use [activity] instead"
    )
    assert any(
        "[analysis] review revise \u2014 The current dashboard drops key state" in line
        for line in lines
    )

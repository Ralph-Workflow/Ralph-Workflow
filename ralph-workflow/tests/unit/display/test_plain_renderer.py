from __future__ import annotations

import importlib
from datetime import UTC, datetime
from io import StringIO

from rich.console import Console

from ralph.display.snapshot import PipelineSnapshot, WorkerSnapshot

plain_renderer = importlib.import_module("ralph.display.plain_renderer")
PlainLogRenderer = plain_renderer.PlainLogRenderer


FIXED_TIME = datetime(2026, 4, 18, 12, 0, tzinfo=UTC)
PLAN_STEP_COUNT = 2


def _make_snapshot(  # noqa: PLR0913
    *,
    phase: str = "development",
    iteration: int = 1,
    total_iterations: int = 3,
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
    console = Console(file=stream, force_terminal=False, color_system=None, width=200)
    renderer = PlainLogRenderer(console, clock=lambda: FIXED_TIME)
    return renderer, stream


def test_emit_snapshot_for_development_outputs_one_line() -> None:
    renderer, stream = _make_renderer()

    renderer.emit_snapshot(_make_snapshot(phase="development"))

    assert stream.getvalue().splitlines() == [
        "2026-04-18T12:00:00+00:00 MILESTONE META [phase] ◆ development"
    ]


def test_emit_snapshot_deduplicates_identical_snapshots() -> None:
    renderer, stream = _make_renderer()
    snapshot = _make_snapshot(phase="development")

    renderer.emit_snapshot(snapshot)
    renderer.emit_snapshot(snapshot)

    assert stream.getvalue().splitlines() == [
        "2026-04-18T12:00:00+00:00 MILESTONE META [phase] ◆ development"
    ]


def test_emit_log_line_strips_markup_for_copy_paste() -> None:
    renderer, stream = _make_renderer()

    renderer.emit_log_line("worker-1", "[bold magenta]hello[/bold magenta]")

    assert stream.getvalue().splitlines() == [
        "2026-04-18T12:00:00+00:00 INFO CONT [content][worker-1] hello"
    ]


def test_emit_snapshot_output_has_no_ansi_escape_codes() -> None:
    renderer, stream = _make_renderer()

    renderer.emit_snapshot(_make_snapshot(workers=(_make_worker(status="RUNNING"),)))

    output = stream.getvalue()
    assert "\x1b" not in output


def test_emit_snapshot_includes_plan_activity_and_analysis_context() -> None:
    renderer, stream = _make_renderer()

    renderer.emit_snapshot(
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
    assert any("[activity] agent=planner tool=read path=PROMPT.md" in line for line in lines)
    assert any("workdir=/tmp/project" in line for line in lines)
    assert any("command=python -m ralph" in line for line in lines)
    assert any("Inspecting PROMPT.md for visibility requirements" in line for line in lines)
    assert any(
        "[analysis] review revise — The current dashboard drops key state" in line
        for line in lines
    )

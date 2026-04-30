"""Tests for PlainLogRenderer empty-state placeholder lines."""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.plain_renderer import PlainLogRenderer
from ralph.display.snapshot import PipelineSnapshot


def _make_renderer() -> tuple[PlainLogRenderer, StringIO]:
    buf = StringIO()
    console = Console(
        file=buf,
        force_terminal=False,
        highlight=False,
        color_system=None,
        width=200,
    )
    return PlainLogRenderer(make_display_context(console=console, env={})), buf


def _make_snapshot(
    *,
    plan_summary: str | None = None,
    active_agent: str | None = None,
    decision_log: tuple[tuple[str, str, str, str], ...] = (),
) -> PipelineSnapshot:
    return PipelineSnapshot(
        phase="planning",
        previous_phase=None,
        iteration=0,
        total_iterations=1,
        reviewer_pass=0,
        total_reviewer_passes=1,
        review_issues_found=False,
        interrupted_by_user=False,
        last_error=None,
        pr_url=None,
        push_count=0,
        total_agent_calls=0,
        total_continuations=0,
        total_fallbacks=0,
        total_retries=0,
        workers=(),
        prompt_path=None,
        prompt_preview=(),
        run_id=None,
        created_at=datetime.now(UTC),
        plan_summary=plan_summary,
        active_agent=active_agent,
        decision_log=decision_log,
    )


def test_plan_empty_state_emitted_once() -> None:
    renderer, buf = _make_renderer()
    snapshot = _make_snapshot()

    renderer.emit_snapshot(snapshot)
    renderer.emit_snapshot(snapshot)

    out = buf.getvalue()
    assert out.count("[plan] (no plan loaded yet)") == 1


def test_activity_empty_state_emitted_once() -> None:
    renderer, buf = _make_renderer()
    snapshot = _make_snapshot()

    renderer.emit_snapshot(snapshot)
    renderer.emit_snapshot(snapshot)

    out = buf.getvalue()
    assert out.count("[activity] (no active agent yet)") == 1


def test_plan_empty_state_suppressed_when_plan_loaded() -> None:
    renderer, buf = _make_renderer()
    snapshot = _make_snapshot(plan_summary="done plan")

    renderer.emit_snapshot(snapshot)

    out = buf.getvalue()
    assert "(no plan loaded yet)" not in out


def test_activity_empty_state_suppressed_when_agent_active() -> None:
    renderer, buf = _make_renderer()
    snapshot = _make_snapshot(active_agent="claude")

    renderer.emit_snapshot(snapshot)

    out = buf.getvalue()
    assert "(no active agent yet)" not in out


def test_decision_log_empty_state_emitted_once() -> None:
    renderer, buf = _make_renderer()
    snapshot = _make_snapshot()

    renderer.emit_snapshot(snapshot)
    renderer.emit_snapshot(snapshot)

    out = buf.getvalue()
    assert out.count("[analysis] (no decisions recorded yet)") == 1


def test_decision_log_empty_state_suppressed_when_decisions_exist() -> None:
    renderer, buf = _make_renderer()
    snapshot = _make_snapshot(
        decision_log=(("development_analysis", "approved", "looks good", "2024-01-01"),)
    )

    renderer.emit_snapshot(snapshot)

    out = buf.getvalue()
    assert "(no decisions recorded yet)" not in out

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from io import StringIO

from rich.console import Console

from ralph.display.completion_summary import (
    CompletionSummaryOptions,
    emit_completion_summary,
    render_completion_summary_group,
)
from ralph.display.context import make_display_context
from ralph.display.snapshot import BudgetProgress, PipelineSnapshot
from ralph.display.theme import make_console


def _make_snapshot(
    *,
    phase: str = "complete",
    plan_summary: str | None = "Build the feature",
    plan_scope_items: tuple[str, ...] = ("item A",),
    decision_log: tuple[tuple[str, str, str, str], ...] = (
        ("development_analysis", "proceed", "all green", "2026-04-21T00:00:00+00:00"),
        ("review_analysis", "revise", "nit fix", "2026-04-21T00:01:00+00:00"),
    ),
    last_error: str | None = None,
    pr_url: str | None = None,
    plan_risks: tuple[str, ...] = (),
    is_terminal_success: bool = True,
    is_terminal_failure: bool = False,
    auto_integrate_remote_sync: str | None = None,
) -> PipelineSnapshot:
    return PipelineSnapshot(
        phase=phase,
        previous_phase=None,
        review_issues_found=False,
        interrupted_by_user=False,
        last_error=last_error,
        pr_url=pr_url,
        push_count=1,
        total_agent_calls=4,
        total_continuations=1,
        total_fallbacks=0,
        total_retries=0,
        workers=(),
        prompt_path="PROMPT.md",
        prompt_preview=(),
        run_id="r1",
        created_at=datetime(2026, 4, 21, tzinfo=UTC),
        plan_summary=plan_summary,
        plan_scope_items=plan_scope_items,
        plan_total_steps=2,
        plan_current_step=2,
        plan_risks=plan_risks,
        decision_log=decision_log,
        is_terminal_success=is_terminal_success,
        is_terminal_failure=is_terminal_failure,
        auto_integrate_action="rebased" if auto_integrate_remote_sync else None,
        auto_integrate_target="main" if auto_integrate_remote_sync else None,
        auto_integrate_fast_forwarded=auto_integrate_remote_sync is not None,
        auto_integrate_remote_sync=auto_integrate_remote_sync,
    )


def _render_group(
    snapshot: PipelineSnapshot,
    *,
    thinking_block_count: int = 0,
    overflow_path: str | None = None,
) -> str:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    ctx = make_display_context(console=console, env={})
    group = render_completion_summary_group(
        snapshot,
        display_context=ctx,
        options=CompletionSummaryOptions(
            thinking_block_count=thinking_block_count,
            overflow_path=overflow_path,
        ),
    )
    console.print(group, markup=False, highlight=False)
    return buf.getvalue()


def _render_group_full(
    snapshot: PipelineSnapshot,
    *,
    content_block_count: int = 0,
    thinking_block_count: int = 0,
    tool_call_count: int = 0,
    error_count: int = 0,
    elapsed_seconds: float | None = None,
    overflow_path: str | None = None,
) -> str:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    ctx = make_display_context(console=console, env={})
    group = render_completion_summary_group(
        snapshot,
        display_context=ctx,
        options=CompletionSummaryOptions(
            content_block_count=content_block_count,
            thinking_block_count=thinking_block_count,
            tool_call_count=tool_call_count,
            error_count=error_count,
            elapsed_seconds=elapsed_seconds,
            overflow_path=overflow_path,
        ),
    )
    console.print(group, markup=False, highlight=False)
    return buf.getvalue()


def test_completion_summary_colours_the_exit_carrier_in_terminal_output() -> None:
    buffer = StringIO()
    console = make_console(
        file=buffer,
        force_terminal=True,
        color_system="truecolor",
        terminal_bg_is_light=False,
        width=120,
    )
    context = make_display_context(console=console, env={"RALPH_TERMINAL_BG": "dark"})
    console.print(
        render_completion_summary_group(
            _make_snapshot(), display_context=context, options=CompletionSummaryOptions()
        ),
        markup=False,
        highlight=False,
    )

    assert re.search(r"\x1b\[[0-9;]*38;[^m]*m  exit=completed", buffer.getvalue())


def test_group_contains_pipeline_complete_title() -> None:
    out = _render_group(_make_snapshot())
    assert "Pipeline Complete" in out


def test_group_contains_pipeline_failed_title_on_failure() -> None:
    out = _render_group(
        _make_snapshot(
            phase="failed",
            last_error="crash",
            is_terminal_success=False,
            is_terminal_failure=True,
        )
    )
    assert "Pipeline Failed" in out


def test_group_active_artifact_validation_never_renders_success_headline() -> None:
    snapshot = _make_snapshot(last_error="SPEC008 missing proof")
    snapshot = replace(snapshot, last_failure_category="artifact_validation")

    out = _render_group(snapshot)

    assert "VALIDATION FAILURE" in out
    assert "Pipeline Complete" not in out


def test_group_active_artifact_validation_uses_error_role() -> None:
    snapshot = replace(
        _make_snapshot(last_error="SPEC008 missing proof"),
        last_failure_category="artifact_validation",
    )
    buffer = StringIO()
    console = make_console(
        file=buffer,
        force_terminal=True,
        color_system="truecolor",
        terminal_bg_is_light=False,
        width=120,
    )
    ctx = make_display_context(console=console, env={"RALPH_TERMINAL_BG": "dark"})

    console.print(
        render_completion_summary_group(
            snapshot,
            display_context=ctx,
            options=CompletionSummaryOptions(),
        ),
        markup=False,
        highlight=False,
    )

    rendered = buffer.getvalue()
    assert "VALIDATION FAILURE" in rendered
    assert "\x1b[1;38;2;255;97;136m" in rendered


def test_group_validation_recovery_preserves_recovered_activity_and_completion() -> None:
    snapshot = _make_snapshot()
    recovered_snapshot = replace(
        snapshot,
        last_activity_line="VALIDATION RECOVERED",
        last_failure_category=None,
    )

    out = _render_group(recovered_snapshot)

    assert "VALIDATION RECOVERED" in out
    assert "Pipeline Complete" in out
    assert "VALIDATION FAILURE" not in out


def test_group_cleared_artifact_validation_restores_normal_completion() -> None:
    snapshot = replace(
        _make_snapshot(),
        last_failure_category=None,
    )

    out = _render_group(snapshot)

    assert "Pipeline Complete" in out
    assert "VALIDATION FAILURE" not in out


def test_group_contains_plan_section() -> None:
    out = _render_group(_make_snapshot())
    assert "Plan" in out
    assert "Build the feature" in out


def test_group_contains_metrics_section() -> None:
    out = _render_group(_make_snapshot())
    assert "Metrics" in out
    assert "agent_calls=4" in out


def test_group_contains_decisions_section() -> None:
    out = _render_group(_make_snapshot())
    assert "Decisions" in out
    assert "Development Analysis" in out
    assert "Review Analysis" in out


def test_group_decision_badges_pass() -> None:
    out = _render_group(_make_snapshot())
    assert "[PASS]" in out


def test_group_decision_badges_warn() -> None:
    out = _render_group(_make_snapshot())
    assert "[WARN]" in out


def test_group_no_decisions_shows_none_recorded() -> None:
    out = _render_group(_make_snapshot(decision_log=()))
    assert "none recorded" in out


def test_group_cause_section_shown_on_failure() -> None:
    out = _render_group(_make_snapshot(phase="failed", last_error="boom", is_terminal_failure=True))
    assert "Error Cause" in out
    assert "boom" in out


def test_completion_summary_regression_failure_phase_and_cause_precede_metrics() -> None:
    out = _render_group(
        _make_snapshot(
            phase="verification",
            last_error="verification failed",
            is_terminal_success=False,
            is_terminal_failure=True,
        )
    )
    assert out.index("failed_phase=verification") < out.index("verification failed")
    assert out.index("verification failed") < out.index("Metrics")


def test_group_sections_appear_in_order() -> None:
    out = _render_group(_make_snapshot())
    positions = {
        "Pipeline Complete": out.index("Pipeline Complete"),
        "Metrics": out.index("Metrics"),
        "Decisions": out.index("Decisions"),
        "Activity": out.index("Activity"),
    }
    assert positions["Pipeline Complete"] < positions["Metrics"]
    assert positions["Metrics"] < positions["Decisions"]
    assert positions["Decisions"] < positions["Activity"]


def test_emit_completion_summary_uses_group_format() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    ctx = make_display_context(console=console, env={})
    emit_completion_summary(_make_snapshot(), display_context=ctx)
    out = buf.getvalue()
    assert "Pipeline Complete" in out
    assert "Decisions" in out


def test_group_renders_remote_sync_outcome() -> None:
    out = _render_group(_make_snapshot(auto_integrate_remote_sync="pushed"))
    assert "auto-integrate:" in out
    assert "remote: pushed" in out


def test_group_no_markup_tags_in_output() -> None:
    out = _render_group(_make_snapshot())
    assert "[bold]" not in out
    assert "[/bold]" not in out
    assert "[green]" not in out


def test_group_contains_activity_summary_section() -> None:
    out = _render_group(_make_snapshot())
    assert "Activity" in out


def test_group_activity_summary_shows_agent_calls() -> None:
    out = _render_group(_make_snapshot())
    assert "agent_calls=4" in out


def test_group_activity_summary_shows_thinking_blocks_when_nonzero() -> None:
    out = _render_group(_make_snapshot(), thinking_block_count=7)
    assert "thinking_blocks=7" in out


def test_group_activity_summary_shows_overflow_path_when_provided() -> None:
    out = _render_group(_make_snapshot(), overflow_path=".agent/raw/unit-1.log")
    assert "raw_overflow=.agent/raw/unit-1.log" in out


def test_group_activity_summary_no_overflow_path_when_none() -> None:
    out = _render_group(_make_snapshot(), overflow_path=None)
    assert "raw_overflow=" not in out


def test_emit_completion_summary_accepts_thinking_and_overflow_params() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    ctx = make_display_context(console=console, env={})
    emit_completion_summary(
        _make_snapshot(),
        display_context=ctx,
        options=CompletionSummaryOptions(
            thinking_block_count=3,
            overflow_path=".agent/raw/u.log",
        ),
    )
    out = buf.getvalue()
    assert "thinking_blocks=3" in out
    assert "raw_overflow=.agent/raw/u.log" in out


def test_completion_panel_renders_elapsed_seconds_when_provided() -> None:
    out = _render_group_full(_make_snapshot(), elapsed_seconds=12.4)
    assert "elapsed=12.4s" in out


def test_completion_panel_skips_elapsed_when_none() -> None:
    out = _render_group_full(_make_snapshot(), elapsed_seconds=None)
    assert "elapsed=" not in out


def test_completion_panel_renders_content_blocks_even_when_zero() -> None:
    out = _render_group_full(_make_snapshot(), content_block_count=0)
    assert "content_blocks=0" in out


def test_completion_panel_renders_thinking_blocks_even_when_zero() -> None:
    out = _render_group_full(_make_snapshot(), thinking_block_count=0)
    assert "thinking_blocks=0" in out


def test_completion_panel_renders_tool_calls() -> None:
    out = _render_group_full(_make_snapshot(), tool_call_count=7)
    assert "tool_calls=7" in out


def test_completion_panel_renders_errors_even_when_zero() -> None:
    out = _render_group_full(_make_snapshot(), error_count=0)
    assert "errors=0" in out


def test_completion_panel_parity_with_run_end() -> None:
    out = _render_group_full(
        _make_snapshot(),
        content_block_count=3,
        thinking_block_count=2,
        tool_call_count=7,
        error_count=0,
        elapsed_seconds=12.4,
    )
    assert "elapsed=12.4s" in out
    assert "content_blocks=3" in out
    assert "thinking_blocks=2" in out
    assert "tool_calls=7" in out
    assert "errors=0" in out
    assert "agent_calls=4" in out


def _make_snapshot_with_outer_dev(outer_dev_iteration: int = 3) -> PipelineSnapshot:
    return PipelineSnapshot(
        phase="complete",
        previous_phase=None,
        review_issues_found=False,
        interrupted_by_user=False,
        last_error=None,
        pr_url=None,
        push_count=1,
        total_agent_calls=4,
        total_continuations=1,
        total_fallbacks=0,
        total_retries=0,
        workers=(),
        prompt_path="PROMPT.md",
        prompt_preview=(),
        run_id="r1",
        created_at=datetime(2026, 4, 21, tzinfo=UTC),
        plan_summary="Build the feature",
        plan_scope_items=("item A",),
        plan_total_steps=2,
        plan_current_step=2,
        plan_risks=(),
        decision_log=(),
        is_terminal_success=True,
        is_terminal_failure=False,
        outer_dev_iteration=outer_dev_iteration,
    )


def test_elapsed_appears_before_metrics_in_default_mode() -> None:
    out = _render_group_full(_make_snapshot(), elapsed_seconds=12.4)
    assert "elapsed=12.4s" in out
    assert out.index("elapsed=12.4s") < out.index("Metrics")


def test_iteration_context_section_appears_with_outer_dev() -> None:
    out = _render_group_full(_make_snapshot_with_outer_dev(outer_dev_iteration=3))
    assert "Iteration Context" in out
    assert "Cycle #3" in out


def test_iteration_context_section_absent_without_context() -> None:
    out = _render_group_full(_make_snapshot())
    assert "Iteration Context" not in out


def _make_snapshot_with_budget_bp(budget_progress: dict) -> PipelineSnapshot:
    return PipelineSnapshot(
        phase="complete",
        previous_phase="development",
        review_issues_found=False,
        interrupted_by_user=False,
        last_error=None,
        pr_url=None,
        push_count=0,
        total_agent_calls=5,
        total_continuations=0,
        total_fallbacks=0,
        total_retries=0,
        workers=(),
        prompt_path="PROMPT.md",
        prompt_preview=(),
        run_id="run-bp",
        created_at=datetime(2026, 4, 18, tzinfo=UTC),
        budget_progress=budget_progress,
    )


def test_default_mode_budget_progress_never_shown() -> None:
    snap = _make_snapshot_with_budget_bp(
        {
            "dev_cycles": BudgetProgress(
                completed=2, cap=8, description="Dev Cycles", tracks_budget=True
            ),
        }
    )
    out = _render_group_full(snap)
    assert "Budget Progress" not in out
    assert "remaining" not in out


def test_default_mode_budget_progress_absent_when_no_tracked_counters() -> None:
    snap = _make_snapshot_with_budget_bp(
        {
            "dev_cycles": BudgetProgress(
                completed=2, cap=8, description="Dev Cycles", tracks_budget=False
            ),
        }
    )
    out = _render_group_full(snap)
    assert "Budget Progress" not in out


def test_group_exit_trigger_shown_in_default_mode() -> None:
    out = _render_group(_make_snapshot())
    assert "exit=completed" in out


def test_group_exit_trigger_failed_shown_in_default_mode() -> None:
    out = _render_group(
        _make_snapshot(
            phase="failed",
            last_error="crash",
            is_terminal_success=False,
            is_terminal_failure=True,
        )
    )
    assert "exit=failed" in out


def test_group_exit_trigger_appears_before_metrics_in_default_mode() -> None:
    out = _render_group(_make_snapshot())
    assert "exit=completed" in out
    assert "Metrics" in out
    assert out.index("exit=completed") < out.index("Metrics")

"""Tests for render_completion_summary_group — rule-delimited section layout."""

from __future__ import annotations

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
    """Render group with all activity counter parameters."""
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


def test_group_contains_verification_section() -> None:
    out = _render_group(_make_snapshot())
    assert "Verification" in out


def test_group_no_decisions_shows_none_recorded() -> None:
    out = _render_group(_make_snapshot(decision_log=()))
    assert "none recorded" in out


def test_group_error_section_shown_on_failure() -> None:
    out = _render_group(_make_snapshot(phase="failed", last_error="boom", is_terminal_failure=True))
    assert "Error" in out
    assert "boom" in out


def test_group_sections_appear_in_order() -> None:
    out = _render_group(_make_snapshot())
    positions = {
        "Pipeline Complete": out.index("Pipeline Complete"),
        "Metrics": out.index("Metrics"),
        "Decisions": out.index("Decisions"),
        "Verification": out.index("Verification"),
    }
    assert positions["Pipeline Complete"] < positions["Metrics"]
    assert positions["Metrics"] < positions["Decisions"]
    assert positions["Decisions"] < positions["Verification"]


def test_emit_completion_summary_uses_group_format() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    ctx = make_display_context(console=console, env={})
    emit_completion_summary(_make_snapshot(), display_context=ctx)
    out = buf.getvalue()
    assert "Pipeline Complete" in out
    assert "Decisions" in out


def test_group_no_markup_tags_in_output() -> None:
    out = _render_group(_make_snapshot())
    assert "[bold]" not in out
    assert "[/bold]" not in out
    assert "[green]" not in out


# --- Activity Summary section tests ---


def test_group_contains_activity_summary_section() -> None:
    out = _render_group(_make_snapshot())
    assert "Activity" in out


def test_group_activity_summary_shows_agent_calls() -> None:
    out = _render_group(_make_snapshot())
    # agent_calls appears in both Metrics and Activity Summary
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


def test_group_activity_summary_appears_before_verification() -> None:
    out = _render_group(_make_snapshot())
    assert out.index("Activity") < out.index("Verification")


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


# --- New Activity Summary counter tests (parity with run-end block) ---


def test_completion_panel_renders_elapsed_seconds_when_provided() -> None:
    """elapsed_seconds renders as elapsed=<value>s when provided."""
    out = _render_group_full(_make_snapshot(), elapsed_seconds=12.4)
    assert "elapsed=12.4s" in out


def test_completion_panel_skips_elapsed_when_none() -> None:
    """elapsed_seconds=None produces no elapsed= line."""
    out = _render_group_full(_make_snapshot(), elapsed_seconds=None)
    assert "elapsed=" not in out


def test_completion_panel_renders_content_blocks_even_when_zero() -> None:
    """content_block_count=0 renders content_blocks=0 (not hidden)."""
    out = _render_group_full(_make_snapshot(), content_block_count=0)
    assert "content_blocks=0" in out


def test_completion_panel_renders_thinking_blocks_even_when_zero() -> None:
    """thinking_block_count=0 renders thinking_blocks=0 (not hidden)."""
    out = _render_group_full(_make_snapshot(), thinking_block_count=0)
    assert "thinking_blocks=0" in out


def test_completion_panel_renders_tool_calls() -> None:
    """tool_call_count=7 renders tool_calls=7."""
    out = _render_group_full(_make_snapshot(), tool_call_count=7)
    assert "tool_calls=7" in out


def test_completion_panel_renders_errors_even_when_zero() -> None:
    """error_count=0 renders errors=0 (not hidden)."""
    out = _render_group_full(_make_snapshot(), error_count=0)
    assert "errors=0" in out


def test_completion_panel_parity_with_run_end() -> None:
    """Activity Summary shows all five counter fields that run-end emits."""
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


# --- Elapsed and Iteration Context placement tests ---


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
    """elapsed= line appears before the Metrics section in default mode output."""
    out = _render_group_full(_make_snapshot(), elapsed_seconds=12.4)
    assert "elapsed=12.4s" in out
    assert out.index("elapsed=12.4s") < out.index("Metrics")


def test_iteration_context_section_appears_with_outer_dev() -> None:
    """Default mode shows an 'Iteration Context' section when outer_dev_iteration is set."""
    out = _render_group_full(_make_snapshot_with_outer_dev(outer_dev_iteration=3))
    assert "Iteration Context" in out
    assert "Dev #3" in out


def test_iteration_context_section_absent_without_context() -> None:
    """Default mode omits 'Iteration Context' section when no iteration fields are set."""
    out = _render_group_full(_make_snapshot())
    assert "Iteration Context" not in out


# --- Budget Progress section tests ---


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
    """Default mode never shows 'Budget Progress' or 'remaining' budget wording."""
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
    """Default mode omits 'Budget Progress' section when no budget-tracked counters exist."""
    snap = _make_snapshot_with_budget_bp(
        {
            "dev_cycles": BudgetProgress(
                completed=2, cap=8, description="Dev Cycles", tracks_budget=False
            ),
        }
    )
    out = _render_group_full(snap)
    assert "Budget Progress" not in out


# --- Exit trigger tests ---


def test_group_exit_trigger_shown_in_default_mode() -> None:
    """Default mode shows exit= label after header rule."""
    out = _render_group(_make_snapshot())
    assert "exit=completed" in out


def test_group_exit_trigger_failed_shown_in_default_mode() -> None:
    """Default mode shows exit=failed when is_terminal_failure=True."""
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
    """exit= line appears before the Metrics section in default mode output."""
    out = _render_group(_make_snapshot())
    assert "exit=completed" in out
    assert "Metrics" in out
    assert out.index("exit=completed") < out.index("Metrics")

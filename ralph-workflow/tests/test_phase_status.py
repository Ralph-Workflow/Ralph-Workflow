"""Tests for ralph/display/phase_status.py canonical presentation formatters."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.phase_banner import PhaseStartContext, show_phase_start
from ralph.display.phase_status import (
    PhaseIterationContext,
    format_analysis_cycle,
    format_budget_remaining,
    format_dev_cycle,
    format_exit_trigger,
    format_fixer_cycle,
    format_transition_context_items,
)

# --- Unit tests for format functions ---


def test_format_dev_cycle_renders_number() -> None:
    assert format_dev_cycle(1) == "Dev #1"
    assert format_dev_cycle(5) == "Dev #5"
    assert format_dev_cycle(10) == "Dev #10"


def test_format_analysis_cycle_without_cap() -> None:
    assert format_analysis_cycle(1) == "Analysis #1"
    assert format_analysis_cycle(3) == "Analysis #3"


def test_format_analysis_cycle_with_cap() -> None:
    assert format_analysis_cycle(1, 3) == "Analysis 1/3"
    assert format_analysis_cycle(3, 3) == "Analysis 3/3"
    assert format_analysis_cycle(2, 5) == "Analysis 2/5"


def test_format_fixer_cycle_renders_number() -> None:
    assert format_fixer_cycle(1) == "Fixer #1"
    assert format_fixer_cycle(3) == "Fixer #3"


def test_format_budget_remaining_renders_count() -> None:
    assert format_budget_remaining(0) == "Budget: 0 left"
    assert format_budget_remaining(5) == "Budget: 5 left"
    assert format_budget_remaining(1) == "Budget: 1 left"


# --- PhaseIterationContext tests ---


def test_phase_iteration_context_has_context_false_when_empty() -> None:
    ctx = PhaseIterationContext()
    assert not ctx.has_context()


def test_phase_iteration_context_has_context_true_with_outer_dev() -> None:
    ctx = PhaseIterationContext(outer_dev=2)
    assert ctx.has_context()


def test_phase_iteration_context_has_context_true_with_fixer() -> None:
    ctx = PhaseIterationContext(fixer=1)
    assert ctx.has_context()


def test_phase_iteration_context_labels_empty() -> None:
    ctx = PhaseIterationContext()
    assert ctx.context_labels() == []


def test_phase_iteration_context_labels_outer_dev_only() -> None:
    ctx = PhaseIterationContext(outer_dev=3)
    labels = ctx.context_labels()
    assert len(labels) == 1
    text, style = labels[0]
    assert text == "Dev #3"
    assert style == "theme.outer_dev"


def test_phase_iteration_context_labels_full_context() -> None:
    ctx = PhaseIterationContext(outer_dev=2, inner_analysis=1, inner_analysis_cap=3, fixer=1)
    labels = ctx.context_labels()
    texts = [t for t, _ in labels]
    assert "Dev #2" in texts
    assert "Analysis 1/3" in texts
    assert "Fixer #1" in texts
    assert len(texts) == len({"Dev #2", "Analysis 1/3", "Fixer #1"})


def test_phase_iteration_context_labels_order() -> None:
    """outer_dev appears before inner_analysis before fixer before budget."""
    ctx = PhaseIterationContext(
        outer_dev=2,
        inner_analysis=1,
        inner_analysis_cap=3,
        fixer=1,
        budget_remaining=4,
    )
    labels = ctx.context_labels()
    texts = [t for t, _ in labels]
    assert texts.index("Dev #2") < texts.index("Analysis 1/3")
    assert texts.index("Analysis 1/3") < texts.index("Fixer #1")
    assert texts.index("Fixer #1") < texts.index("Budget: 4 left")


def test_phase_iteration_context_labels_budget_style() -> None:
    ctx = PhaseIterationContext(budget_remaining=2)
    labels = ctx.context_labels()
    assert len(labels) == 1
    _, style = labels[0]
    assert style == "theme.level.warn"


# --- Integration tests: new labels appear in phase start banners ---


def _make_ctx(mode: str = "wide"):
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=200)
    ctx = make_display_context(console=console, force_mode=mode)
    return ctx, buf


def test_phase_start_outer_dev_shows_dev_cycle_label() -> None:
    """show_phase_start with outer_iteration renders [Dev #N] label."""
    ctx, buf = _make_ctx()
    phase_ctx = PhaseStartContext(outer_iteration=3)
    show_phase_start("development", ctx=phase_ctx, display_context=ctx)
    output = buf.getvalue()
    assert "[Dev #3]" in output, f"Expected [Dev #3] in: {output}"


def test_phase_start_fixer_shows_fixer_cycle_label() -> None:
    """show_phase_start with fixer_iteration renders [Fixer #N] label."""
    ctx, buf = _make_ctx()
    phase_ctx = PhaseStartContext(fixer_iteration=2)
    show_phase_start("fix", ctx=phase_ctx, display_context=ctx)
    output = buf.getvalue()
    assert "[Fixer #2]" in output, f"Expected [Fixer #2] in: {output}"


def test_phase_start_budget_remaining_shows_budget_left_label() -> None:
    """show_phase_start with budget_remaining renders [Budget: N left] label."""
    ctx, buf = _make_ctx()
    phase_ctx = PhaseStartContext(budget_remaining=5)
    show_phase_start("development", ctx=phase_ctx, display_context=ctx)
    output = buf.getvalue()
    assert "[Budget: 5 left]" in output, f"Expected [Budget: 5 left] in: {output}"


def test_phase_start_inner_analysis_shows_analysis_label_with_cap() -> None:
    """show_phase_start with inner_analysis + cap renders [Analysis X/Y] label."""
    ctx, buf = _make_ctx()
    phase_ctx = PhaseStartContext(inner_analysis=2, inner_analysis_cap=4)
    show_phase_start("development_analysis", ctx=phase_ctx, display_context=ctx)
    output = buf.getvalue()
    assert "[Analysis 2/4]" in output, f"Expected [Analysis 2/4] in: {output}"


def test_phase_start_inner_analysis_without_cap_shows_analysis_label() -> None:
    """show_phase_start with inner_analysis but no cap renders [Analysis #N] label."""
    ctx, buf = _make_ctx()
    phase_ctx = PhaseStartContext(inner_analysis=1)
    show_phase_start("development_analysis", ctx=phase_ctx, display_context=ctx)
    output = buf.getvalue()
    assert "[Analysis #1]" in output, f"Expected [Analysis #1] in: {output}"


def test_phase_start_all_new_context_labels_together() -> None:
    """All new context labels appear together when set."""
    ctx, buf = _make_ctx()
    phase_ctx = PhaseStartContext(
        outer_iteration=2,
        fixer_iteration=1,
        budget_remaining=3,
    )
    show_phase_start("fix", ctx=phase_ctx, display_context=ctx)
    output = buf.getvalue()
    assert "[Dev #2]" in output, f"Expected [Dev #2] in: {output}"
    assert "[Fixer #1]" in output, f"Expected [Fixer #1] in: {output}"
    assert "[Budget: 3 left]" in output, f"Expected [Budget: 3 left] in: {output}"


def test_phase_start_new_labels_not_using_old_format() -> None:
    """New canonical labels do not use old key=value format for outer_iteration/fixer."""
    ctx, buf = _make_ctx()
    phase_ctx = PhaseStartContext(outer_iteration=1, fixer_iteration=2)
    show_phase_start("development", ctx=phase_ctx, display_context=ctx)
    output = buf.getvalue()
    assert "dev-iteration=" not in output, "Old format should not appear"
    assert "fixer-iteration=" not in output, "Old format should not appear"
    assert "budget=" not in output, "Old format should not appear"


# --- Tests for format_transition_context_items ---


def test_transition_context_analysis_status_renders_as_value_only() -> None:
    """'analysis_status' key renders as the bare value without key prefix."""
    result = format_transition_context_items({"analysis_status": "final, skipping next"})
    assert result == ["final, skipping next"]


def test_transition_context_decision_renders_with_arrow() -> None:
    """'decision' key renders as '→ value'."""
    result = format_transition_context_items({"decision": "approved"})
    assert result == ["→ approved"]


def test_transition_context_decision_needs_changes_renders_with_arrow() -> None:
    """'decision' key with 'needs changes' value renders as '→ needs changes'."""
    result = format_transition_context_items({"decision": "needs changes"})
    assert result == ["→ needs changes"]


def test_transition_context_budget_key_uses_canonical_label() -> None:
    """Keys ending in '_budget' with 'N remaining' value use canonical Budget label."""
    result = format_transition_context_items({"iteration_budget": "3 remaining"})
    assert result == ["Budget: 3 left"]


def test_transition_context_budget_key_zero_remaining() -> None:
    """Budget of 0 remaining renders as 'Budget: 0 left'."""
    result = format_transition_context_items({"dev_budget": "0 remaining"})
    assert result == ["Budget: 0 left"]


def test_transition_context_multi_word_key_uses_bracket_notation() -> None:
    """Multi-word keys (with spaces) render as '[key value]' bracket notation."""
    result = format_transition_context_items({"Planning Analysis": "3/3"})
    assert result == ["[Planning Analysis 3/3]"]


def test_transition_context_multi_word_key_with_slash_value() -> None:
    """Multi-word key with slash value still uses bracket notation."""
    result = format_transition_context_items({"Development Analysis": "2/4"})
    assert result == ["[Development Analysis 2/4]"]


def test_transition_context_single_word_key_uses_equals_format() -> None:
    """Single-word keys (no spaces) render as 'key=value'."""
    result = format_transition_context_items({"iteration": "1/5"})
    assert result == ["iteration=1/5"]


def test_transition_context_multiple_items_preserve_order() -> None:
    """Multiple context items are returned in insertion order."""
    context = {
        "Planning Analysis": "3/3",
        "analysis_status": "final, skipping next",
        "decision": "needs changes",
        "iteration": "1/5",
    }
    result = format_transition_context_items(context)
    assert result == [
        "[Planning Analysis 3/3]",
        "final, skipping next",
        "→ needs changes",
        "iteration=1/5",
    ]


def test_transition_context_empty_dict_returns_empty_list() -> None:
    """Empty context dict returns empty list."""
    assert format_transition_context_items({}) == []


# --- Unit tests for format_exit_trigger ---


class _FakeSnapshot:
    def __init__(
        self,
        *,
        interrupted_by_user: bool = False,
        is_terminal_success: bool = False,
        is_terminal_failure: bool = False,
    ) -> None:
        self.interrupted_by_user = interrupted_by_user
        self.is_terminal_success = is_terminal_success
        self.is_terminal_failure = is_terminal_failure


def test_format_exit_trigger_interrupted() -> None:
    snap = _FakeSnapshot(interrupted_by_user=True)
    assert format_exit_trigger(snap) == "interrupted"


def test_format_exit_trigger_success() -> None:
    snap = _FakeSnapshot(is_terminal_success=True)
    assert format_exit_trigger(snap) == "completed"


def test_format_exit_trigger_failure() -> None:
    snap = _FakeSnapshot(is_terminal_failure=True)
    assert format_exit_trigger(snap) == "failed"


def test_format_exit_trigger_unknown_state() -> None:
    snap = _FakeSnapshot()
    assert format_exit_trigger(snap) == "exited"


def test_format_exit_trigger_interrupted_takes_priority() -> None:
    """interrupted_by_user has highest priority over terminal flags."""
    snap = _FakeSnapshot(interrupted_by_user=True, is_terminal_success=True)
    assert format_exit_trigger(snap) == "interrupted"

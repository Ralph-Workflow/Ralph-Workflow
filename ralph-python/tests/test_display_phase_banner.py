"""Tests for ralph/display/phase_banner.py — phase transition display."""

from __future__ import annotations

from rich.console import Console

from ralph.display.phase_banner import (
    PhaseStartContext,
    _phase_label,
    _phase_style,
    show_phase_complete,
    show_phase_start,
    show_phase_transition,
)


def test_show_phase_transition_renders_styled_output() -> None:
    console = Console(record=True)
    show_phase_transition("planning", "development", console=console)
    output = console.export_text()
    assert "Planning" in output
    assert "Development" in output


def test_show_phase_transition_minor_renders_rule() -> None:
    console = Console(record=True)
    show_phase_transition("development", "development_analysis", console=console)
    output = console.export_text()
    assert "Development" in output
    assert "Development Analysis" in output


def test_show_phase_start_with_iteration() -> None:
    console = Console(record=True)
    ctx = PhaseStartContext(iteration=1, total_iterations=5)
    show_phase_start("development", ctx=ctx, console=console)
    output = console.export_text()
    assert "Development" in output
    assert "2/5" in output


def test_show_phase_start_with_reviewer_pass() -> None:
    console = Console(record=True)
    ctx = PhaseStartContext(reviewer_pass=0, total_reviewer_passes=3)
    show_phase_start("review", ctx=ctx, console=console)
    output = console.export_text()
    assert "Review" in output
    assert "1/3" in output


def test_show_phase_complete_with_decision() -> None:
    console = Console(record=True)
    show_phase_complete("review_analysis", decision="approved", console=console)
    output = console.export_text()
    assert "approved" in output
    assert "Review Analysis" in output


def test_phase_label_converts_underscore_names() -> None:
    assert _phase_label("development_analysis") == "Development Analysis"
    assert _phase_label("review_commit") == "Review Commit"
    assert _phase_label("planning") == "Planning"


def test_phase_style_returns_correct_styles() -> None:
    assert _phase_style("planning") == "cyan"
    assert _phase_style("development") == "green"
    assert _phase_style("review") == "yellow"
    assert _phase_style("fix") == "red"
    assert _phase_style("complete") == "bold green"
    assert _phase_style("failed") == "bold red"


def test_show_phase_start_without_counters() -> None:
    console = Console(record=True)
    show_phase_start("planning", console=console)
    output = console.export_text()
    assert "Planning" in output
    assert "▶" in output


def test_show_phase_start_with_agent_name() -> None:
    console = Console(record=True)
    show_phase_start("development", agent_name="claude", console=console)
    output = console.export_text()
    assert "Development" in output
    assert "claude" in output


def test_show_phase_start_zero_indexed_boundary() -> None:
    """Iteration 0 should display as 1 (1-indexed for users)."""
    console = Console(record=True)
    ctx = PhaseStartContext(iteration=0, total_iterations=5)
    show_phase_start("development", ctx=ctx, console=console)
    output = console.export_text()
    assert "1/5" in output


def test_show_phase_start_last_iteration_boundary() -> None:
    """Last iteration (N-1) should display as N/N."""
    console = Console(record=True)
    ctx = PhaseStartContext(iteration=4, total_iterations=5)
    show_phase_start("development", ctx=ctx, console=console)
    output = console.export_text()
    assert "5/5" in output


def test_show_phase_complete_without_decision() -> None:
    console = Console(record=True)
    show_phase_complete("development", console=console)
    output = console.export_text()
    assert "Development" in output
    assert "complete" in output


def test_show_phase_transition_with_context() -> None:
    console = Console(record=True)
    show_phase_transition(
        "planning",
        "development",
        context={"iteration": "1/5"},
        console=console,
    )
    output = console.export_text()
    assert "Planning" in output
    assert "Development" in output
    assert "iteration=1/5" in output

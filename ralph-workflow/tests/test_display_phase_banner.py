"""Tests for ralph/display/phase_banner.py — phase transition display."""

from __future__ import annotations

import types
from io import StringIO

from rich.console import Console

from ralph.display.phase_banner import (
    _MAJOR_TRANSITIONS,
    _TRANSITION_DESCRIPTIONS,
    PhaseStartContext,
    _phase_label,
    _phase_style,
    show_phase_complete,
    show_phase_start,
    show_phase_start_from_state,
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


# --- New tests for expanded transitions and descriptions ---


def test_major_transition_analysis_to_commit() -> None:
    """Analysis approved → commit should be a major (double-rule) transition."""
    console = Console(record=True, width=120)
    show_phase_transition("development_analysis", "development_commit", console=console)
    output = console.export_text()
    assert "Development Analysis" in output
    assert "Development Commit" in output
    assert "Analysis approved" in output


def test_major_transition_analysis_loopback() -> None:
    """Analysis loopback → development should be a major transition."""
    console = Console(record=True, width=120)
    show_phase_transition("development_analysis", "development", console=console)
    output = console.export_text()
    assert "Development Analysis" in output
    assert "Development" in output
    assert "Analysis requested changes" in output


def test_major_transition_review_analysis_to_fix() -> None:
    """Review analysis → fix should be a major transition."""
    console = Console(record=True, width=120)
    show_phase_transition("review_analysis", "fix", console=console)
    output = console.export_text()
    assert "Review Analysis" in output
    assert "Fix" in output
    assert "Review found issues" in output


def test_major_transition_review_analysis_to_review_commit() -> None:
    """Review analysis approved → review_commit should be a major transition."""
    console = Console(record=True, width=120)
    show_phase_transition("review_analysis", "review_commit", console=console)
    output = console.export_text()
    assert "Review Analysis" in output
    assert "Review Commit" in output
    assert "approved" in output


def test_major_transition_review_commit_to_development() -> None:
    """Review commit → development (continue dev) should be major."""
    console = Console(record=True, width=120)
    show_phase_transition("review_commit", "development", console=console)
    output = console.export_text()
    assert "Review Commit" in output
    assert "Development" in output
    assert "continuing development" in output


def test_major_transition_review_commit_to_planning() -> None:
    """Review commit → planning (re-plan) should be major."""
    console = Console(record=True, width=120)
    show_phase_transition("review_commit", "planning", console=console)
    output = console.export_text()
    assert "Review Commit" in output
    assert "Planning" in output
    assert "re-planning" in output


def test_major_transition_review_commit_to_complete() -> None:
    """Review commit → complete should be major."""
    console = Console(record=True, width=120)
    show_phase_transition("review_commit", "complete", console=console)
    output = console.export_text()
    assert "Review Commit" in output
    assert "Complete" in output
    assert "pipeline complete" in output


def test_major_transition_fix_to_review() -> None:
    """Fix → review should be major with description."""
    console = Console(record=True, width=120)
    show_phase_transition("fix", "review", console=console)
    output = console.export_text()
    assert "Fix" in output
    assert "Review" in output
    assert "re-reviewing" in output


def test_minor_transition_dev_to_analysis_has_description() -> None:
    """Dev → analysis is a minor transition but should have a description."""
    console = Console(record=True, width=120)
    show_phase_transition("development", "development_analysis", console=console)
    output = console.export_text()
    assert "Development" in output
    assert "Development Analysis" in output
    assert "analyzing results" in output


def test_minor_transition_review_to_analysis_has_description() -> None:
    """Review → review_analysis is minor but should have description."""
    console = Console(record=True, width=120)
    show_phase_transition("review", "review_analysis", console=console)
    output = console.export_text()
    assert "Review" in output
    assert "Review Analysis" in output
    assert "analyzing findings" in output


def test_unknown_transition_renders_gracefully() -> None:
    """Unknown transition pair should still render without crashing."""
    console = Console(record=True, width=120)
    show_phase_transition("unknown_phase", "another_unknown", console=console)
    output = console.export_text()
    assert "Unknown Phase" in output
    assert "Another Unknown" in output


def test_all_major_transitions_have_descriptions() -> None:
    """Every major transition should have a description for good UX."""
    for from_phase, to_phase in _MAJOR_TRANSITIONS:
        assert (from_phase, to_phase) in _TRANSITION_DESCRIPTIONS, (
            f"Major transition ({from_phase}, {to_phase}) has no description"
        )


def test_transition_descriptions_render_in_major_banners() -> None:
    """Major transitions should include the description text in output."""
    for (from_phase, to_phase), description in _TRANSITION_DESCRIPTIONS.items():
        if (from_phase, to_phase) not in _MAJOR_TRANSITIONS:
            continue
        console = Console(record=True, width=120)
        show_phase_transition(from_phase, to_phase, console=console)
        output = console.export_text()
        # Description should appear in the output (at least a substring)
        assert description[:20] in output, (
            f"Description '{description}' not found in output for ({from_phase}, {to_phase})"
        )


def test_show_phase_start_reviewer_pass_zero_boundary() -> None:
    """Reviewer pass 0 should display as 1/N (1-indexed)."""
    console = Console(record=True)
    ctx = PhaseStartContext(reviewer_pass=0, total_reviewer_passes=3)
    show_phase_start("review", ctx=ctx, console=console)
    output = console.export_text()
    assert "1/3" in output


def test_show_phase_start_reviewer_pass_last_boundary() -> None:
    """Last reviewer pass (N-1) should display as N/N."""
    console = Console(record=True)
    ctx = PhaseStartContext(reviewer_pass=2, total_reviewer_passes=3)
    show_phase_start("review", ctx=ctx, console=console)
    output = console.export_text()
    assert "3/3" in output


# --- New tests for analysis iteration counters (Step 5) ---


def test_show_phase_start_dev_analysis_shows_analysis_counter() -> None:
    """When phase is development_analysis with counter context, suffix [analysis N/M] appears."""
    console = Console(record=True)
    ctx = PhaseStartContext(
        development_analysis_iteration=1,
        max_development_analysis_iterations=3,
    )
    show_phase_start("development_analysis", ctx=ctx, console=console)
    output = console.export_text()
    assert "Development Analysis" in output
    assert "[analysis 2/3]" in output


def test_show_phase_start_dev_analysis_zero_index_shows_one() -> None:
    """development_analysis_iteration=0 shows as [analysis 1/M]."""
    console = Console(record=True)
    ctx = PhaseStartContext(
        development_analysis_iteration=0,
        max_development_analysis_iterations=3,
    )
    show_phase_start("development_analysis", ctx=ctx, console=console)
    output = console.export_text()
    assert "[analysis 1/3]" in output


def test_show_phase_start_dev_analysis_at_max_shows_max() -> None:
    """development_analysis_iteration=2 with max=3 shows [analysis 3/3]."""
    console = Console(record=True)
    ctx = PhaseStartContext(
        development_analysis_iteration=2,
        max_development_analysis_iterations=3,
    )
    show_phase_start("development_analysis", ctx=ctx, console=console)
    output = console.export_text()
    assert "[analysis 3/3]" in output


def test_show_phase_start_review_analysis_shows_analysis_counter() -> None:
    """When phase is review_analysis with counter context, suffix [analysis N/M] appears."""
    console = Console(record=True)
    ctx = PhaseStartContext(
        review_analysis_iteration=0,
        max_review_analysis_iterations=2,
    )
    show_phase_start("review_analysis", ctx=ctx, console=console)
    output = console.export_text()
    assert "Review Analysis" in output
    assert "[analysis 1/2]" in output


def test_show_phase_start_review_analysis_at_max_shows_max() -> None:
    """review_analysis_iteration=1 with max=2 shows [analysis 2/2]."""
    console = Console(record=True)
    ctx = PhaseStartContext(
        review_analysis_iteration=1,
        max_review_analysis_iterations=2,
    )
    show_phase_start("review_analysis", ctx=ctx, console=console)
    output = console.export_text()
    assert "[analysis 2/2]" in output


def test_show_phase_start_dev_analysis_no_suffix_without_context() -> None:
    """When phase is development_analysis but no counter context, no [analysis] suffix."""
    console = Console(record=True)
    ctx = PhaseStartContext(
        iteration=0,
        total_iterations=5,
        # No development_analysis_iteration or max set
    )
    show_phase_start("development_analysis", ctx=ctx, console=console)
    output = console.export_text()
    assert "Development Analysis" in output
    assert "[analysis" not in output


def test_show_phase_start_development_no_analysis_suffix() -> None:
    """When phase is development (not analysis), no [analysis] suffix even with counters."""
    console = Console(record=True)
    ctx = PhaseStartContext(
        iteration=1,
        total_iterations=5,
        development_analysis_iteration=2,
        max_development_analysis_iterations=3,
    )
    show_phase_start("development", ctx=ctx, console=console)
    output = console.export_text()
    assert "Development" in output
    assert "[analysis" not in output


def test_show_phase_start_review_no_analysis_suffix() -> None:
    """When phase is review (not analysis), no [analysis] suffix even with counters."""
    console = Console(record=True)
    ctx = PhaseStartContext(
        reviewer_pass=0,
        total_reviewer_passes=2,
        review_analysis_iteration=1,
        max_review_analysis_iterations=2,
    )
    show_phase_start("review", ctx=ctx, console=console)
    output = console.export_text()
    assert "Review" in output
    assert "[analysis" not in output


def test_show_phase_start_combines_iteration_and_analysis_counters() -> None:
    """Both iteration and analysis counters can appear together."""
    console = Console(record=True)
    ctx = PhaseStartContext(
        iteration=2,
        total_iterations=5,
        development_analysis_iteration=1,
        max_development_analysis_iterations=3,
    )
    show_phase_start("development_analysis", ctx=ctx, console=console)
    output = console.export_text()
    assert "Development Analysis" in output
    assert "[iteration 3/5]" in output
    assert "[analysis 2/3]" in output


# --- Tests for show_phase_start_from_state (Step 13) ---


def test_show_phase_start_from_state_forwards_counters() -> None:
    stub = types.SimpleNamespace(
        iteration=0,
        total_iterations=3,
        reviewer_pass=1,
        total_reviewer_passes=2,
        agent_name="coder",
    )
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=200)
    show_phase_start_from_state(stub, "development", console=console)
    output = buf.getvalue()
    assert "iteration 1/3" in output
    assert "pass 2/2" in output
    assert "Development" in output


def test_show_phase_start_from_state_tolerates_missing_attrs() -> None:
    stub = types.SimpleNamespace(iteration=0, total_iterations=3)
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=200)
    show_phase_start_from_state(stub, "planning", console=console)
    output = buf.getvalue()
    assert "iteration 1/3" in output
    assert "Planning" in output
    assert "pass" not in output
